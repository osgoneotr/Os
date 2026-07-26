"""TikTok Content Posting API client.

Implements the documented Direct Post flow:

    1. POST /v2/post/publish/creator_info/query/   -- MANDATORY precondition
    2. POST /v2/post/publish/video/init/           -- get publish_id + upload_url
    3. PUT  <upload_url>                           -- chunked media transfer
    4. POST /v2/post/publish/status/fetch/         -- poll until terminal

Step 1 is not optional and not merely advisory. The privacy_level you send in
step 2 must be one of the options step 1 returned; for an unaudited client
TikTok does not return any public option, so hardcoding PUBLIC_TO_EVERYONE
fails with privacy_level_option_mismatch. Querying first and validating
locally turns that into a clear error before you burn quota.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

import requests

from . import config
from .auth import TokenStore
from .errors import ComplianceError, RetryableError, classify

# Terminal states worth stopping on. SEND_TO_USER_INBOX is the success state
# for the video.upload (draft) flow -- it never becomes PUBLISH_COMPLETE,
# because the creator finishes the post by hand, so omitting it here would make
# the recommended semi-automated path poll until it times out.
TERMINAL_STATUSES = {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX", "FAILED"}

MIME_BY_SUFFIX = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


@dataclass
class CreatorInfo:
    """Response from creator_info/query -- the source of truth for what you may post."""

    nickname: str
    privacy_level_options: list[str]
    comment_disabled: bool
    duet_disabled: bool
    stitch_disabled: bool
    max_video_post_duration_sec: int

    @property
    def can_post_publicly(self) -> bool:
        """True only if TikTok offers a non-private option.

        If this is False you are either unaudited, or the account itself is
        private. Both force SELF_ONLY.
        """
        return any(p != "SELF_ONLY" for p in self.privacy_level_options)

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "CreatorInfo":
        return cls(
            nickname=data.get("creator_nickname", ""),
            privacy_level_options=list(data.get("privacy_level_options", [])),
            comment_disabled=bool(data.get("comment_disabled", False)),
            duet_disabled=bool(data.get("duet_disabled", False)),
            stitch_disabled=bool(data.get("stitch_disabled", False)),
            max_video_post_duration_sec=int(data.get("max_video_post_duration_sec", 0)),
        )


@dataclass
class PostRequest:
    video_path: Path
    title: str
    privacy_level: str = "SELF_ONLY"
    disable_comment: bool = False
    disable_duet: bool = False
    disable_stitch: bool = False
    video_cover_timestamp_ms: int = 1000
    # Commercial disclosure. Both default OFF, as TikTok's UX guidelines require.
    brand_content_toggle: bool = False
    brand_organic_toggle: bool = False
    # Set True for AI-generated / synthetic content. See README -- for
    # AI-assisted "brainrot" output this is the honest setting and it does not
    # suppress reach the way an undisclosed-AI takedown does.
    is_aigc: bool = False
    # Optional. When set, checked against the creator's max duration locally so
    # an over-length video fails before it uploads rather than after.
    duration_sec: float | None = None


def plan_chunks(file_size: int) -> tuple[int, int]:
    """Return (chunk_size, total_chunk_count) satisfying TikTok's transfer rules.

    Rules: chunks are >= 5 MB and <= 64 MB, the final chunk may run up to
    128 MB, and there is a hard cap of 1000 chunks. Files under 5 MB must go
    as a single whole-file chunk.
    """
    if file_size <= 0:
        raise ValueError("file_size must be positive")
    if file_size > config.MAX_FILE_BYTES:
        raise ComplianceError(
            f"File is {file_size / 1e9:.2f} GB; TikTok's FILE_UPLOAD limit is 4 GB."
        )

    if file_size < config.MIN_CHUNK_BYTES:
        # Whole-file upload: chunk_size == file size, exactly one chunk.
        return file_size, 1

    chunk_size = config.MIN_CHUNK_BYTES
    count = file_size // chunk_size  # trailing bytes ride along in the final chunk

    if count > config.MAX_CHUNK_COUNT:
        # Grow chunk_size so we stay within 1000 chunks.
        chunk_size = math.ceil(file_size / config.MAX_CHUNK_COUNT)
        chunk_size = min(chunk_size, config.MAX_CHUNK_BYTES)
        count = file_size // chunk_size

    if count == 1:
        # A single chunk must declare the whole file as its size. Files from
        # 5 MB up to just under 10 MB land here: 5 MB <= size < 2 * 5 MB gives
        # count == 1, and declaring chunk_size=5MB against a 9 MB video is an
        # init-time rejection. The lone chunk is also the final chunk, so it
        # may run to 128 MB -- well clear of the <10 MB sizes that reach here.
        chunk_size = file_size

    return int(chunk_size), int(count)


def iter_chunk_ranges(file_size: int, chunk_size: int, count: int) -> Iterator[tuple[int, int]]:
    """Yield inclusive (first_byte, last_byte) pairs for Content-Range headers."""
    for i in range(count):
        first = i * chunk_size
        last = file_size - 1 if i == count - 1 else first + chunk_size - 1
        yield first, last


def validate_request(req: PostRequest, info: CreatorInfo) -> None:
    """Fail fast on anything TikTok would reject, before any upload happens.

    Module-level so it can be tested without constructing a client or touching
    the network -- every check here is pure.
    """
    if req.privacy_level not in info.privacy_level_options:
        raise ComplianceError(
            f"privacy_level {req.privacy_level!r} is not offered for this account. "
            f"TikTok returned: {info.privacy_level_options}. "
            + (
                "No public option means your client is unaudited or the account "
                "is set to private -- posting will be SELF_ONLY regardless."
                if not info.can_post_publicly
                else ""
            )
        )

    if len(req.title) > config.MAX_TITLE_RUNES:
        raise ComplianceError(
            f"Caption is {len(req.title)} runes; limit is {config.MAX_TITLE_RUNES}."
        )

    if req.brand_content_toggle and req.privacy_level == "SELF_ONLY":
        raise ComplianceError("Branded content cannot be posted with SELF_ONLY visibility.")

    if info.max_video_post_duration_sec and req.duration_sec:
        if req.duration_sec > info.max_video_post_duration_sec:
            raise ComplianceError(
                f"Video is {req.duration_sec:.1f}s but this creator's limit is "
                f"{info.max_video_post_duration_sec}s."
            )


class TikTokClient:
    def __init__(self, settings: config.Settings, tokens: TokenStore | None = None):
        self.settings = settings
        self.tokens = tokens or TokenStore(settings)
        self.session = requests.Session()

    # ---- plumbing ----------------------------------------------------

    def _headers(self, account: str) -> dict[str, str]:
        token = self.tokens.get_valid(account)
        return {
            "Authorization": f"Bearer {token.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def _post(self, url: str, account: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self.session.post(
            url,
            headers=self._headers(account),
            json=payload,
            timeout=self.settings.request_timeout,
        )
        try:
            body = resp.json()
        except ValueError:
            raise RetryableError(f"Non-JSON response from {url} (HTTP {resp.status_code})")

        err = body.get("error") or {}
        code = err.get("code")
        # TikTok signals success with code "ok"; anything else is a real error.
        if code and code != "ok":
            exc = classify(code, resp.status_code)
            raise exc(
                err.get("message") or f"{url} failed",
                code=code,
                log_id=err.get("log_id"),
            )
        if resp.status_code >= 400:
            raise classify(None, resp.status_code)(f"HTTP {resp.status_code} from {url}")

        return body.get("data", {})

    # ---- API surface -------------------------------------------------

    def query_creator_info(self, account: str) -> CreatorInfo:
        """Step 1. Always call this before posting."""
        return CreatorInfo.parse(self._post(config.CREATOR_INFO_URL, account, {}))

    # Kept as a method for call-site convenience; delegates to the pure function.
    validate_request = staticmethod(validate_request)

    def init_video_post(self, account: str, req: PostRequest, file_size: int) -> dict[str, Any]:
        """Step 2. Returns {publish_id, upload_url}."""
        chunk_size, chunk_count = plan_chunks(file_size)
        payload = {
            "post_info": {
                "title": req.title,
                "privacy_level": req.privacy_level,
                "disable_duet": req.disable_duet,
                "disable_comment": req.disable_comment,
                "disable_stitch": req.disable_stitch,
                "video_cover_timestamp_ms": req.video_cover_timestamp_ms,
                "brand_content_toggle": req.brand_content_toggle,
                "brand_organic_toggle": req.brand_organic_toggle,
                "is_aigc": req.is_aigc,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": chunk_size,
                "total_chunk_count": chunk_count,
            },
        }
        data = self._post(config.VIDEO_INIT_URL, account, payload)
        data["_chunk_size"] = chunk_size
        data["_chunk_count"] = chunk_count
        return data

    def upload_video(
        self,
        upload_url: str,
        path: Path,
        chunk_size: int,
        chunk_count: int,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Step 3. PUT each chunk with a Content-Range header."""
        file_size = path.stat().st_size
        suffix = path.suffix.lower()
        if suffix not in MIME_BY_SUFFIX:
            raise ComplianceError(
                f"Unsupported container {suffix!r}. TikTok accepts "
                f"{', '.join(sorted(MIME_BY_SUFFIX))}."
            )
        mime = MIME_BY_SUFFIX[suffix]

        with path.open("rb") as fh:
            for idx, (first, last) in enumerate(
                iter_chunk_ranges(file_size, chunk_size, chunk_count), start=1
            ):
                fh.seek(first)
                blob = fh.read(last - first + 1)
                resp = self.session.put(
                    upload_url,
                    headers={
                        "Content-Type": mime,
                        "Content-Length": str(len(blob)),
                        "Content-Range": f"bytes {first}-{last}/{file_size}",
                    },
                    data=blob,
                    timeout=self.settings.request_timeout,
                )
                # 201 = final chunk accepted, 206 = partial accepted.
                if resp.status_code not in (200, 201, 206):
                    exc = classify(None, resp.status_code)
                    raise exc(
                        f"Chunk {idx}/{chunk_count} failed with HTTP {resp.status_code}: "
                        f"{resp.text[:200]}"
                    )
                if on_progress:
                    on_progress(idx, chunk_count)

    def fetch_status(self, account: str, publish_id: str) -> dict[str, Any]:
        """Step 4 (single poll)."""
        return self._post(config.STATUS_FETCH_URL, account, {"publish_id": publish_id})

    def wait_for_publish(
        self,
        account: str,
        publish_id: str,
        timeout: int = 600,
        interval: int = 10,
    ) -> dict[str, Any]:
        """Poll until the post reaches a terminal state or the timeout expires.

        TikTok's UX guidelines require surfacing processing status rather than
        firing and forgetting, and it is also the only way to learn that a post
        was silently rejected post-upload.
        """
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.fetch_status(account, publish_id)
            status = last.get("status", "")
            if status in TERMINAL_STATUSES:
                if status == "FAILED":
                    reason = last.get("fail_reason", "unknown")
                    raise classify(reason)(
                        f"Publish failed: {reason}", code=reason
                    )
                return last
            time.sleep(interval)

        raise RetryableError(
            f"Timed out after {timeout}s waiting on publish_id={publish_id}; "
            f"last status={last.get('status', 'unknown')}"
        )

    # ---- orchestration ----------------------------------------------

    def post_video(
        self,
        account: str,
        req: PostRequest,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        """Full flow with the mandatory precondition and validation baked in."""
        if not req.video_path.exists():
            raise ComplianceError(f"Video not found: {req.video_path}")

        info = self.query_creator_info(account)
        validate_request(req, info)

        file_size = req.video_path.stat().st_size

        if self.settings.dry_run:
            chunk_size, chunk_count = plan_chunks(file_size)
            return {
                "dry_run": True,
                "creator": info.nickname,
                "privacy_level": req.privacy_level,
                "chunks": chunk_count,
                "chunk_size": chunk_size,
                "bytes": file_size,
            }

        init = self.init_video_post(account, req, file_size)
        publish_id = init["publish_id"]
        self.upload_video(
            init["upload_url"],
            req.video_path,
            init["_chunk_size"],
            init["_chunk_count"],
            on_progress=on_progress,
        )
        result = self.wait_for_publish(account, publish_id)
        result["publish_id"] = publish_id
        return result
