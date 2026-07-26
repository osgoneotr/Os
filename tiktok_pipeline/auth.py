"""OAuth token storage and refresh.

TikTok access tokens are short-lived (24h) and refresh tokens last 365 days but
rotate on every refresh -- if you drop a rotated refresh token you have to send
the user through the consent screen again. This module persists both and always
writes the new refresh token back.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from .config import Settings, TOKEN_URL
from .errors import AuthError, RetryableError

# Refresh this many seconds before actual expiry, so a long upload started
# with a "valid" token doesn't expire mid-flight.
REFRESH_SKEW_SECONDS = 300


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: float
    open_id: str = ""
    scope: str = ""

    @property
    def expired(self) -> bool:
        return time.time() >= (self.expires_at - REFRESH_SKEW_SECONDS)

    def has_scope(self, scope: str) -> bool:
        return scope in {s.strip() for s in self.scope.replace(",", " ").split()}


class TokenStore:
    """Persists a TokenSet to disk, one file per TikTok account.

    Files are written 0600. This holds credentials that can post to a real
    account -- do not commit the state directory.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.dir = Path(settings.state_dir) / "tokens"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, account: str) -> Path:
        safe = "".join(c for c in account if c.isalnum() or c in "-_")
        if not safe:
            raise ValueError(f"account name {account!r} has no usable characters")
        return self.dir / f"{safe}.json"

    def load(self, account: str) -> TokenSet:
        path = self._path(account)
        if not path.exists():
            raise AuthError(
                f"No stored token for account {account!r}. Run: "
                f"python -m tiktok_pipeline.cli login --account {account}"
            )
        return TokenSet(**json.loads(path.read_text()))

    def save(self, account: str, tokens: TokenSet) -> None:
        path = self._path(account)
        path.write_text(json.dumps(asdict(tokens), indent=2))
        path.chmod(0o600)

    def get_valid(self, account: str) -> TokenSet:
        """Return a live access token, refreshing it if needed."""
        tokens = self.load(account)
        if tokens.expired:
            tokens = self.refresh(account, tokens)
        return tokens

    def refresh(self, account: str, tokens: TokenSet) -> TokenSet:
        resp = requests.post(
            TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": self.settings.client_key,
                "client_secret": self.settings.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token,
            },
            timeout=self.settings.request_timeout,
        )
        if resp.status_code >= 500:
            raise RetryableError(f"Token endpoint returned {resp.status_code}")

        body = resp.json()
        if "error" in body and body.get("error"):
            raise AuthError(
                f"Refresh failed: {body.get('error_description') or body['error']}. "
                "The refresh token may have been revoked or rotated out -- re-run login.",
                code=body.get("error"),
                log_id=body.get("log_id"),
            )

        refreshed = TokenSet(
            access_token=body["access_token"],
            # Critical: TikTok rotates the refresh token. Persist the new one.
            refresh_token=body.get("refresh_token", tokens.refresh_token),
            expires_at=time.time() + int(body.get("expires_in", 86400)),
            open_id=body.get("open_id", tokens.open_id),
            scope=body.get("scope", tokens.scope),
        )
        self.save(account, refreshed)
        return refreshed


def exchange_code(settings: Settings, code: str, redirect_uri: str) -> TokenSet:
    """Trade an authorization code for a token set (one-time, during login)."""
    resp = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": settings.client_key,
            "client_secret": settings.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=settings.request_timeout,
    )
    body = resp.json()
    if body.get("error"):
        raise AuthError(
            f"Code exchange failed: {body.get('error_description') or body['error']}",
            code=body.get("error"),
            log_id=body.get("log_id"),
        )
    return TokenSet(
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
        expires_at=time.time() + int(body.get("expires_in", 86400)),
        open_id=body.get("open_id", ""),
        scope=body.get("scope", ""),
    )
