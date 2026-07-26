"""Error taxonomy for the pipeline.

The split that matters is retryable vs terminal. Retrying a terminal error
(a rejected privacy level, a spam flag) wastes quota and looks like abuse to
TikTok; not retrying a transient one drops a post on the floor.
"""

from __future__ import annotations


class TikTokError(Exception):
    """Base for all pipeline errors."""

    def __init__(self, message: str, *, code: str | None = None, log_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.log_id = log_id  # TikTok returns a log_id -- include it in support tickets.

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.code:
            parts.append(f"code={self.code}")
        if self.log_id:
            parts.append(f"log_id={self.log_id}")
        return " ".join(parts)


class RetryableError(TikTokError):
    """Transient failure. Safe to retry with backoff."""


class RateLimitedError(RetryableError):
    """HTTP 429 or local limiter rejection."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kw):
        super().__init__(message, **kw)
        self.retry_after = retry_after


class TerminalError(TikTokError):
    """Permanent failure. Do not retry without changing the request."""


class AuthError(TerminalError):
    """Token invalid, expired beyond refresh, or missing a scope."""


class ComplianceError(TerminalError):
    """The request would violate TikTok's posting rules.

    Raised before hitting the network -- e.g. requesting a privacy level the
    creator_info endpoint did not offer, which is the single most common
    Content Posting API failure for unaudited clients.
    """


# TikTok error codes that are worth retrying. Everything else is terminal.
RETRYABLE_CODES = frozenset(
    {
        "rate_limit_exceeded",
        "internal_error",
        "server_error",
        "upload_failed",
    }
)

TERMINAL_CODES = frozenset(
    {
        "access_token_invalid",
        "scope_not_authorized",
        "scope_permission_missed",
        "privacy_level_option_mismatch",
        "spam_risk_too_many_posts",
        "spam_risk_user_banned_from_posting",
        "spam_risk_text",
        "reached_active_user_cap",
        "unaudited_client_can_only_post_to_private_accounts",
        "url_ownership_unverified",
        "file_format_check_failed",
        "duration_check_failed",
        "frame_rate_check_failed",
        "picture_size_check_failed",
        "video_pull_failed",
    }
)


def classify(code: str | None, http_status: int | None = None) -> type[TikTokError]:
    """Map a TikTok error code / HTTP status onto an exception class."""
    if code in TERMINAL_CODES:
        if code in {"access_token_invalid", "scope_not_authorized", "scope_permission_missed"}:
            return AuthError
        return TerminalError
    if code in RETRYABLE_CODES or http_status == 429:
        return RateLimitedError if (code == "rate_limit_exceeded" or http_status == 429) else RetryableError
    if http_status is not None and http_status >= 500:
        return RetryableError
    return TerminalError
