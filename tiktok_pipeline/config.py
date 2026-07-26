"""Configuration for the TikTok posting pipeline.

All limits here are conservative defaults. TikTok does not publish rate limits
for the Content Posting API in its public rate-limit table (only /v2/user/info/,
/v2/video/query/ and /v2/video/list/ are listed at 600/min), so the posting
limits below are deliberately low and configurable rather than scraped values.
Tune them only if TikTok tells you your quota directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

API_BASE = "https://open.tiktokapis.com/v2"

CREATOR_INFO_URL = f"{API_BASE}/post/publish/creator_info/query/"
VIDEO_INIT_URL = f"{API_BASE}/post/publish/video/init/"
STATUS_FETCH_URL = f"{API_BASE}/post/publish/status/fetch/"
TOKEN_URL = f"{API_BASE}/oauth/token/"

# Scopes needed for the full pipeline. video.publish is the audited one.
REQUIRED_SCOPES = ("user.info.basic", "video.publish", "video.upload")

# Media transfer rules, from the Content Posting API media transfer guide.
MIN_CHUNK_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_CHUNK_BYTES = 64 * 1024 * 1024  # 64 MB
MAX_FINAL_CHUNK_BYTES = 128 * 1024 * 1024  # last chunk may run to 128 MB
MAX_CHUNK_COUNT = 1000
MAX_FILE_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB
UPLOAD_URL_TTL_SECONDS = 3600  # upload_url expires one hour after issuance

# Caption limit is 2200 UTF-16 runes.
MAX_TITLE_RUNES = 2200

PRIVACY_LEVELS = (
    "PUBLIC_TO_EVERYONE",
    "MUTUAL_FOLLOW_FRIENDS",
    "FOLLOWER_OF_CREATOR",
    "SELF_ONLY",
)


@dataclass(frozen=True)
class RateLimits:
    """Posting throughput caps enforced client-side.

    ``requests_per_minute`` guards the publish endpoints. ``posts_per_day``
    is your own editorial cap, not a TikTok-published number -- posting more
    than a handful of times a day from one account is a spam signal regardless
    of what the API permits.
    """

    requests_per_minute: int = 6
    posts_per_day: int = 5
    min_seconds_between_posts: int = 45 * 60


@dataclass(frozen=True)
class Settings:
    client_key: str
    client_secret: str
    state_dir: Path = field(default=Path("./.tiktok_state"))
    rate_limits: RateLimits = field(default_factory=RateLimits)
    # Wall-clock hours (local time) the scheduler is allowed to post in.
    posting_hours: tuple[int, ...] = (7, 12, 17, 20, 22)
    request_timeout: int = 60
    dry_run: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        key = os.environ.get("TIKTOK_CLIENT_KEY")
        secret = os.environ.get("TIKTOK_CLIENT_SECRET")
        if not key or not secret:
            raise RuntimeError(
                "TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET must be set. "
                "Copy .env.example to .env and fill it in."
            )
        return cls(
            client_key=key,
            client_secret=secret,
            state_dir=Path(os.environ.get("TIKTOK_STATE_DIR", "./.tiktok_state")),
            dry_run=os.environ.get("TIKTOK_DRY_RUN", "").lower() in {"1", "true", "yes"},
        )
