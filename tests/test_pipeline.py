"""Tests for the parts where a bug means a duplicate post or a rejected upload."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pytest

from tiktok_pipeline import config
from tiktok_pipeline.client import (
    CreatorInfo,
    PostRequest,
    iter_chunk_ranges,
    plan_chunks,
    validate_request,
)
from tiktok_pipeline.config import RateLimits, Settings
from tiktok_pipeline.errors import ComplianceError, RetryableError, TerminalError, classify
from tiktok_pipeline.queue import PostQueue, idempotency_key
from tiktok_pipeline.ratelimit import PostingWindow, TokenBucket
from tiktok_pipeline.retry import backoff_delay, with_retries

MB = 1024 * 1024


# ---- chunk planning -------------------------------------------------

def test_small_file_is_single_whole_chunk():
    size = 3 * MB
    chunk_size, count = plan_chunks(size)
    assert count == 1
    assert chunk_size == size


def test_chunks_respect_minimum_size():
    chunk_size, count = plan_chunks(50 * MB)
    assert chunk_size >= config.MIN_CHUNK_BYTES
    assert count == 10


def test_chunk_count_never_exceeds_cap():
    """The 4 GB file limit keeps us under the 1000-chunk cap by construction.

    4 GB / 5 MB minimum = 819 chunks, so the cap cannot be hit by any file the
    API would accept. The rescaling branch in plan_chunks is defensive only;
    this test pins the invariant rather than pretending to exercise it.
    """
    largest_allowed = config.MAX_FILE_BYTES
    chunk_size, count = plan_chunks(largest_allowed)
    assert count <= config.MAX_CHUNK_COUNT
    assert config.MIN_CHUNK_BYTES <= chunk_size <= config.MAX_CHUNK_BYTES


def test_oversized_file_rejected():
    with pytest.raises(ComplianceError, match="4 GB"):
        plan_chunks(5 * 1024 * MB)


@pytest.mark.parametrize("size_mb", [0.5, 3, 4.9, 5, 6, 7, 9, 9.9])
def test_single_chunk_always_declares_whole_file_size(size_mb):
    """Regression: 5-10 MB files yielded count=1 with chunk_size=5MB.

    TikTok requires chunk_size == video_size when total_chunk_count is 1, so
    the mismatch was rejected at init for every clip in that range.
    """
    size = int(size_mb * MB)
    chunk_size, count = plan_chunks(size)
    if count == 1:
        assert chunk_size == size, "single chunk must declare the full file size"


@pytest.mark.parametrize("size_mb", [0.5, 3, 5, 7, 9, 10, 15, 47, 64, 100, 512, 2048])
def test_declared_chunking_covers_file_exactly(size_mb):
    """The declared plan must always describe the real byte layout."""
    size = int(size_mb * MB)
    chunk_size, count = plan_chunks(size)
    ranges = list(iter_chunk_ranges(size, chunk_size, count))

    assert len(ranges) == count
    assert ranges[0][0] == 0
    assert ranges[-1][1] == size - 1
    assert sum(last - first + 1 for first, last in ranges) == size

    for first, last in ranges[:-1]:
        assert last - first + 1 == chunk_size
    final = ranges[-1][1] - ranges[-1][0] + 1
    assert final <= config.MAX_FINAL_CHUNK_BYTES
    if count > 1:
        assert config.MIN_CHUNK_BYTES <= chunk_size <= config.MAX_CHUNK_BYTES


def test_ranges_cover_entire_file_without_gaps():
    size = 47 * MB + 12345
    chunk_size, count = plan_chunks(size)
    ranges = list(iter_chunk_ranges(size, chunk_size, count))

    assert ranges[0][0] == 0
    assert ranges[-1][1] == size - 1  # trailing bytes land in the final chunk
    for (_, prev_last), (next_first, _) in zip(ranges, ranges[1:]):
        assert next_first == prev_last + 1


def test_final_chunk_absorbs_remainder_within_limit():
    size = 47 * MB + 12345
    chunk_size, count = plan_chunks(size)
    ranges = list(iter_chunk_ranges(size, chunk_size, count))
    final_len = ranges[-1][1] - ranges[-1][0] + 1
    assert final_len <= config.MAX_FINAL_CHUNK_BYTES


# ---- compliance validation -----------------------------------------

UNAUDITED = CreatorInfo(
    nickname="test",
    privacy_level_options=["SELF_ONLY"],
    comment_disabled=False,
    duet_disabled=False,
    stitch_disabled=False,
    max_video_post_duration_sec=600,
)

AUDITED = CreatorInfo(
    nickname="test",
    privacy_level_options=["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
    comment_disabled=False,
    duet_disabled=False,
    stitch_disabled=False,
    max_video_post_duration_sec=600,
)


def test_unaudited_client_cannot_post_publicly():
    assert UNAUDITED.can_post_publicly is False
    assert AUDITED.can_post_publicly is True


def test_public_post_rejected_for_unaudited_account(tmp_path):
    req = PostRequest(video_path=tmp_path / "x.mp4", title="hi",
                      privacy_level="PUBLIC_TO_EVERYONE")
    with pytest.raises(ComplianceError, match="unaudited"):
        validate_request(req, UNAUDITED)


def test_overlong_caption_rejected(tmp_path):
    req = PostRequest(video_path=tmp_path / "x.mp4", title="x" * 2500,
                      privacy_level="SELF_ONLY")
    with pytest.raises(ComplianceError, match="2200"):
        validate_request(req, UNAUDITED)


def test_branded_content_cannot_be_private(tmp_path):
    req = PostRequest(video_path=tmp_path / "x.mp4", title="ad",
                      privacy_level="SELF_ONLY", brand_content_toggle=True)
    with pytest.raises(ComplianceError, match="[Bb]randed"):
        validate_request(req, UNAUDITED)


def test_overlong_video_rejected_before_upload(tmp_path):
    req = PostRequest(video_path=tmp_path / "x.mp4", title="long",
                      privacy_level="SELF_ONLY", duration_sec=900.0)
    with pytest.raises(ComplianceError, match="600s"):
        validate_request(req, UNAUDITED)


def test_valid_request_passes(tmp_path):
    req = PostRequest(video_path=tmp_path / "x.mp4", title="ok",
                      privacy_level="PUBLIC_TO_EVERYONE", duration_sec=25.0)
    validate_request(req, AUDITED)  # must not raise


# ---- error classification ------------------------------------------

def test_spam_flag_is_terminal_not_retried():
    assert issubclass(classify("spam_risk_too_many_posts"), TerminalError)


def test_server_error_is_retryable():
    assert issubclass(classify(None, 500), RetryableError)
    assert issubclass(classify("rate_limit_exceeded"), RetryableError)


def test_terminal_errors_are_not_retried():
    calls = []

    @with_retries(max_attempts=4, sleep=lambda _: None)
    def always_terminal():
        calls.append(1)
        raise TerminalError("bad privacy level")

    with pytest.raises(TerminalError):
        always_terminal()
    assert len(calls) == 1  # exactly one attempt


def test_retryable_error_retries_then_succeeds():
    calls = []

    @with_retries(max_attempts=4, sleep=lambda _: None)
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RetryableError("transient")
        return "ok"

    assert flaky() == "ok"
    assert len(calls) == 3


def test_backoff_is_bounded():
    for attempt in range(12):
        assert 0 <= backoff_delay(attempt, cap=300.0) <= 300.0


# ---- rate limiting --------------------------------------------------

def test_token_bucket_blocks_after_limit():
    bucket = TokenBucket(max_requests=3, window_seconds=60)
    assert all(bucket.acquire(block=False) for _ in range(3))
    assert bucket.acquire(block=False) is False
    assert bucket.time_until_slot() > 0


def test_posting_window_enforces_daily_cap(tmp_path):
    limits = RateLimits(posts_per_day=2, min_seconds_between_posts=0)
    window = PostingWindow(limits, tmp_path, posting_hours=())
    now = time.time()

    window.record_post(now - 100)
    window.record_post(now - 50)
    allowed, reason = window.check(now)
    assert not allowed and "daily cap" in reason


def test_posting_window_enforces_spacing(tmp_path):
    limits = RateLimits(posts_per_day=10, min_seconds_between_posts=3600)
    window = PostingWindow(limits, tmp_path, posting_hours=())
    now = time.time()

    window.record_post(now - 60)
    allowed, reason = window.check(now)
    assert not allowed and "spacing" in reason


def test_posting_window_allows_when_clear(tmp_path):
    limits = RateLimits(posts_per_day=5, min_seconds_between_posts=0)
    window = PostingWindow(limits, tmp_path, posting_hours=())
    allowed, reason = window.check()
    assert allowed, reason


def test_next_allowed_time_respects_spacing(tmp_path):
    limits = RateLimits(posts_per_day=10, min_seconds_between_posts=1800)
    window = PostingWindow(limits, tmp_path, posting_hours=())
    now = time.time()
    window.record_post(now)
    assert window.next_allowed_time(now) >= now + 1800


def test_history_survives_restart(tmp_path):
    limits = RateLimits(posts_per_day=1, min_seconds_between_posts=0)
    PostingWindow(limits, tmp_path, ()).record_post()
    # A fresh instance -- as after a crash -- must still see the post.
    allowed, reason = PostingWindow(limits, tmp_path, ()).check()
    assert not allowed and "daily cap" in reason


# ---- queue ----------------------------------------------------------

def _settings(tmp_path) -> Settings:
    return Settings(client_key="k", client_secret="s", state_dir=tmp_path)


def _video(tmp_path, name="clip.mp4", content=b"\x00" * 4096) -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def test_duplicate_enqueue_is_rejected(tmp_path):
    queue = PostQueue(_settings(tmp_path))
    video = _video(tmp_path)

    first = queue.enqueue("main", video, "same caption")
    second = queue.enqueue("main", video, "same caption")

    assert first is not None
    assert second is None, "identical post must not be queued twice"


def test_different_caption_is_a_different_job(tmp_path):
    queue = PostQueue(_settings(tmp_path))
    video = _video(tmp_path)
    assert queue.enqueue("main", video, "caption A") is not None
    assert queue.enqueue("main", video, "caption B") is not None


def test_idempotency_key_differs_by_account(tmp_path):
    video = _video(tmp_path)
    assert idempotency_key(video, "t", "acct1") != idempotency_key(video, "t", "acct2")


def test_claim_next_marks_in_flight_and_is_exclusive(tmp_path):
    queue = PostQueue(_settings(tmp_path))
    queue.enqueue("main", _video(tmp_path), "one")

    job = queue.claim_next()
    assert job is not None
    assert queue.claim_next() is None, "a claimed job must not be handed out twice"


def test_future_scheduled_job_is_not_claimed(tmp_path):
    queue = PostQueue(_settings(tmp_path))
    queue.enqueue("main", _video(tmp_path), "later", not_before=time.time() + 3600)
    assert queue.claim_next() is None


def test_claim_next_is_atomic_under_concurrency(tmp_path):
    """Regression: deferred transactions let two workers claim the same job."""
    import threading

    settings = _settings(tmp_path)
    queue = PostQueue(settings)
    for i in range(8):
        queue.enqueue("main", _video(tmp_path, f"c{i}.mp4", bytes([i]) * 4096), f"t{i}")

    claimed: list[int] = []
    lock = threading.Lock()

    def drain():
        q = PostQueue(settings)  # separate connection, as a real worker would have
        while True:
            job = q.claim_next()
            if job is None:
                return
            with lock:
                claimed.append(job.id)

    threads = [threading.Thread(target=drain) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(claimed) == sorted(set(claimed)), "a job was claimed twice"
    assert len(claimed) == 8


def test_claimed_job_reports_in_flight_status(tmp_path):
    queue = PostQueue(_settings(tmp_path))
    queue.enqueue("main", _video(tmp_path), "one")
    job = queue.claim_next()
    assert job is not None
    assert job.status == "IN_FLIGHT"


def test_explicit_zero_not_before_is_honoured(tmp_path):
    """Regression: `not_before or now` treated an explicit 0 as unset."""
    queue = PostQueue(_settings(tmp_path))
    queue.enqueue("main", _video(tmp_path), "epoch", not_before=0.0)
    assert next(queue.list_jobs()).not_before == 0.0


def test_orphaned_inflight_goes_to_blocked_not_pending(tmp_path):
    """A crashed job may already have published -- it must not auto-retry."""
    queue = PostQueue(_settings(tmp_path))
    queue.enqueue("main", _video(tmp_path), "one")
    job = queue.claim_next()
    assert job is not None

    queue.db.execute(
        "UPDATE jobs SET updated_at=? WHERE id=?", (time.time() - 7200, job.id)
    )
    queue.db.commit()

    assert queue.requeue_stale_inflight(older_than=3600) == 1
    assert [j.status for j in queue.list_jobs()] == ["BLOCKED"]


# ---- posting-time jitter --------------------------------------------

def test_slot_offset_is_stable_across_restarts(tmp_path):
    """A process bounce must not reroll the offset into posting early."""
    limits = RateLimits(jitter_minutes=45)
    when = datetime(2026, 7, 26, 17, 0)
    a = PostingWindow(limits, tmp_path, (17,), jitter_key="acct").slot_offset_minutes(when)
    b = PostingWindow(limits, tmp_path, (17,), jitter_key="acct").slot_offset_minutes(when)
    assert a == b


def test_slot_offset_varies_by_day(tmp_path):
    """Same hour every day at the same minute is the bot signature we avoid."""
    limits = RateLimits(jitter_minutes=45)
    w = PostingWindow(limits, tmp_path, (17,), jitter_key="acct")
    offsets = {w.slot_offset_minutes(datetime(2026, 7, d, 17, 0)) for d in range(1, 29)}
    assert len(offsets) > 5, f"offsets barely vary across a month: {offsets}"
    assert all(0 <= o < 45 for o in offsets)


def test_posting_blocked_before_jittered_slot_opens(tmp_path):
    limits = RateLimits(posts_per_day=10, min_seconds_between_posts=0, jitter_minutes=45)
    w = PostingWindow(limits, tmp_path, (17,), jitter_key="acct")
    day = datetime(2026, 7, 26, 17, 0)
    offset = w.slot_offset_minutes(day)
    if offset == 0:
        pytest.skip("this slot happens to open on the hour")

    before = day.replace(minute=offset - 1).timestamp()
    after = day.replace(minute=offset).timestamp()

    allowed, reason = w.check(before)
    assert not allowed and "jitter" in reason
    assert w.check(after)[0]


def test_jitter_disabled_allows_posting_on_the_hour(tmp_path):
    limits = RateLimits(posts_per_day=10, min_seconds_between_posts=0, jitter_minutes=0)
    w = PostingWindow(limits, tmp_path, (17,))
    assert w.check(datetime(2026, 7, 26, 17, 0).timestamp())[0]


def test_next_allowed_time_lands_on_an_open_slot(tmp_path):
    limits = RateLimits(posts_per_day=10, min_seconds_between_posts=0, jitter_minutes=45)
    w = PostingWindow(limits, tmp_path, (7, 12, 17), jitter_key="acct")
    # 03:00 -- before every slot that day.
    nxt = w.next_allowed_time(datetime(2026, 7, 26, 3, 0).timestamp())
    allowed, reason = w.check(nxt)
    assert allowed, f"next_allowed_time returned a blocked moment: {reason}"
    assert datetime.fromtimestamp(nxt).hour in (7, 12, 17)


# ---- posting mode ----------------------------------------------------

def test_enqueue_defaults_to_direct_mode(tmp_path):
    queue = PostQueue(_settings(tmp_path))
    queue.enqueue("main", _video(tmp_path), "t")
    assert next(queue.list_jobs()).mode == "direct"


def test_enqueue_accepts_inbox_mode(tmp_path):
    queue = PostQueue(_settings(tmp_path))
    queue.enqueue("main", _video(tmp_path), "t", mode="inbox")
    assert next(queue.list_jobs()).mode == "inbox"


def test_unknown_mode_rejected(tmp_path):
    queue = PostQueue(_settings(tmp_path))
    with pytest.raises(ValueError, match="mode must be one of"):
        queue.enqueue("main", _video(tmp_path), "t", mode="telepathy")


def test_existing_database_gains_mode_column(tmp_path):
    """A queue.db written before `mode` existed must keep working."""
    import sqlite3

    state = tmp_path / "state"
    state.mkdir()
    legacy = sqlite3.connect(state / "queue.db")
    legacy.executescript(
        """CREATE TABLE jobs (
             id INTEGER PRIMARY KEY AUTOINCREMENT, idem_key TEXT NOT NULL UNIQUE,
             account TEXT NOT NULL, video_path TEXT NOT NULL, title TEXT NOT NULL,
             privacy_level TEXT NOT NULL, options_json TEXT NOT NULL DEFAULT '{}',
             status TEXT NOT NULL DEFAULT 'PENDING', attempts INTEGER NOT NULL DEFAULT 0,
             not_before REAL NOT NULL DEFAULT 0, publish_id TEXT, last_error TEXT,
             created_at REAL NOT NULL, updated_at REAL NOT NULL);"""
    )
    legacy.execute(
        """INSERT INTO jobs (idem_key, account, video_path, title, privacy_level,
                             created_at, updated_at)
           VALUES ('old','main','/tmp/x.mp4','legacy','SELF_ONLY',0,0)"""
    )
    legacy.commit()
    legacy.close()

    queue = PostQueue(Settings(client_key="k", client_secret="s", state_dir=state))
    job = next(queue.list_jobs())
    assert job.title == "legacy"
    assert job.mode == "direct", "migrated rows must default to the pre-existing behaviour"
