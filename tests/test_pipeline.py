"""Tests for the parts where a bug means a duplicate post or a rejected upload."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tiktok_pipeline import config
from tiktok_pipeline.client import CreatorInfo, PostRequest, iter_chunk_ranges, plan_chunks
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

def _client():
    return __import__(
        "tiktok_pipeline.client", fromlist=["TikTokClient"]
    ).TikTokClient.__new__(
        __import__("tiktok_pipeline.client", fromlist=["TikTokClient"]).TikTokClient
    )


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
    client = _client()
    req = PostRequest(video_path=tmp_path / "x.mp4", title="hi",
                      privacy_level="PUBLIC_TO_EVERYONE")
    with pytest.raises(ComplianceError, match="unaudited"):
        client.validate_request(req, UNAUDITED)


def test_overlong_caption_rejected(tmp_path):
    client = _client()
    req = PostRequest(video_path=tmp_path / "x.mp4", title="x" * 2500,
                      privacy_level="SELF_ONLY")
    with pytest.raises(ComplianceError, match="2200"):
        client.validate_request(req, UNAUDITED)


def test_branded_content_cannot_be_private(tmp_path):
    client = _client()
    req = PostRequest(video_path=tmp_path / "x.mp4", title="ad",
                      privacy_level="SELF_ONLY", brand_content_toggle=True)
    with pytest.raises(ComplianceError, match="[Bb]randed"):
        client.validate_request(req, UNAUDITED)


def test_valid_request_passes(tmp_path):
    client = _client()
    req = PostRequest(video_path=tmp_path / "x.mp4", title="ok",
                      privacy_level="PUBLIC_TO_EVERYONE")
    client.validate_request(req, AUDITED)  # must not raise


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
