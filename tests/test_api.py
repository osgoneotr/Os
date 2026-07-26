"""API-path tests against a stubbed transport.

These cover the code that talks to TikTok without talking to TikTok. Doing it
for real would need live credentials, would post actual videos to a real
account, and would burn the unaudited 5-posts-per-24h cap on every run.

The transport stub records the exact requests made, so these assert on wire
format -- request bodies, Content-Range headers, poll sequencing -- which is
where this layer actually goes wrong.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tiktok_pipeline import config
from tiktok_pipeline.auth import TokenSet, TokenStore
from tiktok_pipeline.client import PostRequest, TikTokClient
from tiktok_pipeline.config import Settings
from tiktok_pipeline.errors import AuthError, ComplianceError, RetryableError, TerminalError

MB = 1024 * 1024


# ---- transport stub -------------------------------------------------

class FakeResponse:
    def __init__(self, status_code: int = 200, body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def ok(data: dict) -> FakeResponse:
    """A TikTok success envelope."""
    return FakeResponse(200, {"data": data, "error": {"code": "ok", "message": ""}})


def api_error(code: str, status: int = 400) -> FakeResponse:
    return FakeResponse(
        status,
        {"data": {}, "error": {"code": code, "message": f"simulated {code}", "log_id": "L1"}},
    )


class FakeSession:
    """Records calls and replays queued responses."""

    def __init__(self):
        self.post_responses: list[FakeResponse] = []
        self.put_responses: list[FakeResponse] = []
        self.posts: list[dict] = []
        self.puts: list[dict] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append({"url": url, "headers": headers or {}, "json": json})
        if not self.post_responses:
            raise AssertionError(f"unexpected POST to {url}")
        return self.post_responses.pop(0)

    def put(self, url, headers=None, data=None, timeout=None):
        self.puts.append({"url": url, "headers": headers or {}, "len": len(data or b"")})
        if not self.put_responses:
            return FakeResponse(201)
        return self.put_responses.pop(0)


@pytest.fixture
def video(tmp_path) -> Path:
    """A 12 MB file -- large enough to force multi-chunk upload."""
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\xab" * (12 * MB))
    return p


@pytest.fixture
def client(tmp_path) -> TikTokClient:
    settings = Settings(client_key="k", client_secret="s", state_dir=tmp_path)
    store = TokenStore(settings)
    store.save(
        "main",
        TokenSet(
            access_token="tok",
            refresh_token="ref",
            expires_at=time.time() + 9999,
            scope="video.publish video.upload",
        ),
    )
    c = TikTokClient(settings, store)
    c.session = FakeSession()
    return c


AUDITED_INFO = {
    "creator_nickname": "me",
    "privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
    "comment_disabled": False,
    "duet_disabled": False,
    "stitch_disabled": False,
    "max_video_post_duration_sec": 600,
}
UNAUDITED_INFO = {**AUDITED_INFO, "privacy_level_options": ["SELF_ONLY"]}


# ---- direct post ----------------------------------------------------

def test_direct_post_sends_expected_wire_format(client, video):
    s = client.session
    s.post_responses = [
        ok(AUDITED_INFO),
        ok({"publish_id": "pub-1", "upload_url": "https://upload.example/1"}),
        ok({"status": "PUBLISH_COMPLETE"}),
    ]

    req = PostRequest(video_path=video, title="hook", privacy_level="PUBLIC_TO_EVERYONE")
    result = client.post_video("main", req)

    assert result["publish_id"] == "pub-1"

    # 1. creator_info must be queried first, before anything is uploaded.
    assert s.posts[0]["url"] == config.CREATOR_INFO_URL

    # 2. init carries post_info and matching source_info.
    init = s.posts[1]
    assert init["url"] == config.VIDEO_INIT_URL
    body = init["json"]
    assert body["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert body["post_info"]["title"] == "hook"
    assert body["source_info"]["source"] == "FILE_UPLOAD"
    assert body["source_info"]["video_size"] == 12 * MB
    # chunk_size * count deliberately does NOT equal video_size: the final
    # chunk absorbs the remainder (spec allows it to exceed chunk_size, up to
    # 128 MB). 12 MB becomes 2 chunks of a declared 5 MB, the last carrying 7.
    assert body["source_info"]["chunk_size"] == 5 * MB
    assert body["source_info"]["total_chunk_count"] == 2

    # 3. status is polled with the publish_id.
    assert s.posts[2]["url"] == config.STATUS_FETCH_URL
    assert s.posts[2]["json"] == {"publish_id": "pub-1"}


def test_auth_header_and_content_type_are_set(client, video):
    s = client.session
    s.post_responses = [ok(AUDITED_INFO)]
    client.query_creator_info("main")

    headers = s.posts[0]["headers"]
    assert headers["Authorization"] == "Bearer tok"
    assert "application/json" in headers["Content-Type"]


def test_commercial_disclosure_flags_default_off(client, video):
    s = client.session
    s.post_responses = [
        ok(AUDITED_INFO),
        ok({"publish_id": "p", "upload_url": "https://u/1"}),
        ok({"status": "PUBLISH_COMPLETE"}),
    ]
    client.post_video("main", PostRequest(video_path=video, title="t",
                                          privacy_level="SELF_ONLY"))
    post_info = s.posts[1]["json"]["post_info"]
    assert post_info["brand_content_toggle"] is False
    assert post_info["brand_organic_toggle"] is False


def test_aigc_flag_is_forwarded(client, video):
    s = client.session
    s.post_responses = [
        ok(AUDITED_INFO),
        ok({"publish_id": "p", "upload_url": "https://u/1"}),
        ok({"status": "PUBLISH_COMPLETE"}),
    ]
    client.post_video("main", PostRequest(video_path=video, title="t",
                                          privacy_level="SELF_ONLY", is_aigc=True))
    assert s.posts[1]["json"]["post_info"]["is_aigc"] is True


def test_unaudited_public_request_fails_before_any_upload(client, video):
    s = client.session
    s.post_responses = [ok(UNAUDITED_INFO)]

    req = PostRequest(video_path=video, title="t", privacy_level="PUBLIC_TO_EVERYONE")
    with pytest.raises(ComplianceError, match="unaudited"):
        client.post_video("main", req)

    assert len(s.posts) == 1, "must not call init after validation fails"
    assert s.puts == [], "must not upload a single byte"


# ---- inbox (draft) flow ---------------------------------------------

def test_inbox_upload_omits_post_info(client, video):
    """TikTok rejects post_info on the inbox endpoint; the creator captions it."""
    s = client.session
    s.post_responses = [
        ok({"publish_id": "inb-1", "upload_url": "https://upload.example/2"}),
        ok({"status": "SEND_TO_USER_INBOX"}),
    ]

    result = client.upload_to_inbox(
        "main", PostRequest(video_path=video, title="draft caption")
    )

    assert result["publish_id"] == "inb-1"
    init = s.posts[0]
    assert init["url"] == config.INBOX_INIT_URL
    assert "post_info" not in init["json"]
    assert init["json"]["source_info"]["video_size"] == 12 * MB


def test_inbox_upload_skips_creator_info(client, video):
    """No privacy level to validate, so the extra call would be wasted quota."""
    s = client.session
    s.post_responses = [
        ok({"publish_id": "i", "upload_url": "https://u/2"}),
        ok({"status": "SEND_TO_USER_INBOX"}),
    ]
    client.upload_to_inbox("main", PostRequest(video_path=video, title="t"))
    assert config.CREATOR_INFO_URL not in [p["url"] for p in s.posts]


def test_send_to_user_inbox_is_terminal_success(client, video):
    """Regression: omitting this status made the draft flow poll until timeout."""
    s = client.session
    s.post_responses = [
        ok({"publish_id": "i", "upload_url": "https://u/2"}),
        ok({"status": "SEND_TO_USER_INBOX"}),
    ]
    result = client.upload_to_inbox("main", PostRequest(video_path=video, title="t"))
    assert result["status"] == "SEND_TO_USER_INBOX"


# ---- chunked transfer -----------------------------------------------

def test_chunks_cover_file_with_correct_content_range(client, video):
    s = client.session
    s.post_responses = [
        ok(AUDITED_INFO),
        ok({"publish_id": "p", "upload_url": "https://u/1"}),
        ok({"status": "PUBLISH_COMPLETE"}),
    ]
    client.post_video("main", PostRequest(video_path=video, title="t",
                                          privacy_level="SELF_ONLY"))

    total = 12 * MB
    assert len(s.puts) == 2, "12 MB at a 5 MB minimum chunk should be 2 chunks"

    ranges = [p["headers"]["Content-Range"] for p in s.puts]
    assert ranges[0] == f"bytes 0-{5 * MB - 1}/{total}"
    # Trailing bytes ride along in the final chunk rather than forming a third.
    assert ranges[-1] == f"bytes {5 * MB}-{total - 1}/{total}"

    assert sum(p["len"] for p in s.puts) == total
    assert all(p["headers"]["Content-Type"] == "video/mp4" for p in s.puts)


def test_upload_failure_raises(client, video):
    s = client.session
    s.post_responses = [
        ok(AUDITED_INFO),
        ok({"publish_id": "p", "upload_url": "https://u/1"}),
    ]
    s.put_responses = [FakeResponse(500, {}, "boom")]

    with pytest.raises(RetryableError, match="Chunk 1/2"):
        client.post_video("main", PostRequest(video_path=video, title="t",
                                              privacy_level="SELF_ONLY"))


def test_small_file_uploads_as_single_chunk(client, tmp_path):
    small = tmp_path / "small.mp4"
    small.write_bytes(b"\x01" * (2 * MB))

    s = client.session
    s.post_responses = [
        ok(AUDITED_INFO),
        ok({"publish_id": "p", "upload_url": "https://u/1"}),
        ok({"status": "PUBLISH_COMPLETE"}),
    ]
    client.post_video("main", PostRequest(video_path=small, title="t",
                                          privacy_level="SELF_ONLY"))

    assert len(s.puts) == 1
    assert s.posts[1]["json"]["source_info"]["chunk_size"] == 2 * MB
    assert s.puts[0]["headers"]["Content-Range"] == f"bytes 0-{2 * MB - 1}/{2 * MB}"


# ---- status polling -------------------------------------------------

def test_polls_until_terminal_status(client):
    s = client.session
    s.post_responses = [
        ok({"status": "PROCESSING_UPLOAD"}),
        ok({"status": "PROCESSING_UPLOAD"}),
        ok({"status": "PUBLISH_COMPLETE"}),
    ]
    result = client.wait_for_publish("main", "p", timeout=30, interval=0)
    assert result["status"] == "PUBLISH_COMPLETE"
    assert len(s.posts) == 3


def test_failed_publish_raises_with_reason(client):
    s = client.session
    s.post_responses = [ok({"status": "FAILED", "fail_reason": "spam_risk_too_many_posts"})]

    with pytest.raises(TerminalError, match="spam_risk_too_many_posts"):
        client.wait_for_publish("main", "p", timeout=30, interval=0)


def test_poll_timeout_is_retryable(client):
    s = client.session
    s.post_responses = [ok({"status": "PROCESSING_UPLOAD"}) for _ in range(50)]
    with pytest.raises(RetryableError, match="Timed out"):
        client.wait_for_publish("main", "p", timeout=0, interval=0)


# ---- error classification over the wire -----------------------------

@pytest.mark.parametrize(
    "code,expected",
    [
        ("access_token_invalid", AuthError),
        ("spam_risk_too_many_posts", TerminalError),
        ("privacy_level_option_mismatch", TerminalError),
        ("reached_active_user_cap", TerminalError),
        ("rate_limit_exceeded", RetryableError),
    ],
)
def test_api_errors_map_to_right_exception(client, code, expected):
    client.session.post_responses = [api_error(code)]
    with pytest.raises(expected):
        client.query_creator_info("main")


def test_server_error_is_retryable(client):
    client.session.post_responses = [FakeResponse(503, {"data": {}, "error": {}})]
    with pytest.raises(RetryableError):
        client.query_creator_info("main")


def test_non_json_response_is_retryable(client):
    class Garbage(FakeResponse):
        def json(self):
            raise ValueError("not json")

    client.session.post_responses = [Garbage(502, None, "<html>gateway</html>")]
    with pytest.raises(RetryableError, match="Non-JSON"):
        client.query_creator_info("main")


def test_log_id_is_surfaced_for_support(client):
    client.session.post_responses = [api_error("spam_risk_text")]
    with pytest.raises(TerminalError) as exc:
        client.query_creator_info("main")
    assert "log_id=L1" in str(exc.value)


# ---- token refresh --------------------------------------------------

def test_expired_token_refreshes_and_persists_rotation(tmp_path, monkeypatch):
    """TikTok rotates the refresh token; dropping the new one forces re-login."""
    settings = Settings(client_key="k", client_secret="s", state_dir=tmp_path)
    store = TokenStore(settings)
    store.save(
        "main",
        TokenSet(
            access_token="old",
            refresh_token="old-refresh",
            expires_at=time.time() - 10,  # already expired
            scope="video.publish",
        ),
    )

    calls = []

    def fake_post(url, headers=None, data=None, timeout=None):
        calls.append(data)
        return FakeResponse(200, {
            "access_token": "new-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 86400,
            "scope": "video.publish",
        })

    monkeypatch.setattr("tiktok_pipeline.auth.requests.post", fake_post)

    refreshed = store.get_valid("main")
    assert refreshed.access_token == "new-access"
    assert calls[0]["grant_type"] == "refresh_token"
    assert calls[0]["refresh_token"] == "old-refresh"

    # The rotated token must be on disk, not just in memory.
    assert json.loads((tmp_path / "tokens" / "main.json").read_text())[
        "refresh_token"
    ] == "rotated-refresh"


def test_revoked_refresh_token_raises_auth_error(tmp_path, monkeypatch):
    settings = Settings(client_key="k", client_secret="s", state_dir=tmp_path)
    store = TokenStore(settings)
    store.save("main", TokenSet("a", "r", time.time() - 10))

    monkeypatch.setattr(
        "tiktok_pipeline.auth.requests.post",
        lambda *a, **kw: FakeResponse(400, {
            "error": "invalid_grant", "error_description": "revoked"
        }),
    )
    with pytest.raises(AuthError, match="re-run login"):
        store.get_valid("main")


def test_valid_token_is_not_refreshed(tmp_path, monkeypatch):
    settings = Settings(client_key="k", client_secret="s", state_dir=tmp_path)
    store = TokenStore(settings)
    store.save("main", TokenSet("live", "r", time.time() + 9999))

    def explode(*a, **kw):
        raise AssertionError("should not refresh a live token")

    monkeypatch.setattr("tiktok_pipeline.auth.requests.post", explode)
    assert store.get_valid("main").access_token == "live"


def test_token_file_is_not_world_readable(tmp_path):
    settings = Settings(client_key="k", client_secret="s", state_dir=tmp_path)
    TokenStore(settings).save("main", TokenSet("a", "r", time.time() + 100))
    mode = (tmp_path / "tokens" / "main.json").stat().st_mode & 0o777
    assert mode == 0o600, f"credentials readable by others (mode {mode:o})"


# ---- dry run --------------------------------------------------------

def test_dry_run_makes_no_upload(tmp_path, video):
    settings = Settings(client_key="k", client_secret="s", state_dir=tmp_path, dry_run=True)
    store = TokenStore(settings)
    store.save("main", TokenSet("t", "r", time.time() + 9999, scope="video.publish"))
    c = TikTokClient(settings, store)
    c.session = FakeSession()
    c.session.post_responses = [ok(AUDITED_INFO)]

    result = c.post_video("main", PostRequest(video_path=video, title="t",
                                              privacy_level="SELF_ONLY"))
    assert result["dry_run"] is True
    assert result["mode"] == "direct"
    assert c.session.puts == []
