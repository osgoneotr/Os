"""Small retrying HTTP client.

Uses `requests` when it's installed and falls back to the standard library,
so the toolkit works on a bare Python install with nothing pip-installed.
"""

from __future__ import annotations

import json as jsonlib
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import config as cfg_mod

try:  # optional
    import requests  # type: ignore

    _HAS_REQUESTS = True
except Exception:  # pragma: no cover - depends on the machine
    _HAS_REQUESTS = False


class HttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


# Retrying these is pointless — the request itself is wrong.
NON_RETRYABLE = {400, 401, 403, 404, 422}


class HttpClient:
    def __init__(self, cfg=None):
        self.cfg = cfg or cfg_mod.load()
        self.timeout = self.cfg.get("http.timeout_seconds", 20)
        self.max_retries = self.cfg.get("http.max_retries", 4)
        self.backoff = self.cfg.get("http.backoff_seconds", [2, 4, 8, 16])
        self.user_agent = self.cfg.get("http.user_agent", "resell-autopilot/1.0")
        self._session = requests.Session() if _HAS_REQUESTS else None

    # -- public ---------------------------------------------------------

    def get_json(self, url: str, *, params: dict | None = None, headers: dict | None = None) -> Any:
        body = self.request("GET", url, params=params, headers=headers)
        return jsonlib.loads(body) if body else None

    def post_json(
        self,
        url: str,
        *,
        data: dict | str | None = None,
        headers: dict | None = None,
    ) -> Any:
        body = self.request("POST", url, data=data, headers=headers)
        return jsonlib.loads(body) if body else None

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        data: dict | str | None = None,
        headers: dict | None = None,
    ) -> str:
        headers = {"User-Agent": self.user_agent, **(headers or {})}
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                return self._once(method, url, params, data, headers)
            except HttpError as exc:
                # A 4xx we caused isn't going to fix itself on retry.
                if exc.status in NON_RETRYABLE:
                    raise
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
            except Exception as exc:  # requests.RequestException et al
                last_error = exc

            if attempt < self.max_retries:
                delay = self.backoff[min(attempt, len(self.backoff) - 1)]
                time.sleep(delay)

        raise HttpError(f"{method} {url} failed after {self.max_retries + 1} attempts: {last_error}")

    # -- internals ------------------------------------------------------

    def _once(self, method, url, params, data, headers) -> str:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"

        if _HAS_REQUESTS and self._session is not None:
            kwargs: dict[str, Any] = {"headers": headers, "timeout": self.timeout}
            if isinstance(data, dict):
                kwargs["data"] = data
            elif isinstance(data, str):
                kwargs["data"] = data.encode("utf-8")
            resp = self._session.request(method, url, **kwargs)
            if resp.status_code >= 400:
                raise HttpError(
                    f"HTTP {resp.status_code} from {url}", resp.status_code, resp.text[:500]
                )
            return resp.text

        body_bytes: bytes | None = None
        if isinstance(data, dict):
            body_bytes = urllib.parse.urlencode(data).encode("utf-8")
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif isinstance(data, str):
            body_bytes = data.encode("utf-8")

        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise HttpError(f"HTTP {exc.code} from {url}", exc.code, detail) from exc
