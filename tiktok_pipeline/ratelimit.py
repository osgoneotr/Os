"""Client-side rate limiting and posting-window logic.

Two independent gates:

  * ``TokenBucket`` -- short-horizon request pacing, so a burst of retries
    can't hammer the publish endpoints.
  * ``PostingWindow`` -- long-horizon editorial pacing (posts/day, minimum
    gap between posts, allowed hours). This is the one that keeps the account
    healthy; the API would happily let you post more often than is wise.

State is persisted so limits survive a process restart -- an in-memory-only
limiter resets to "full quota" every crash, which is exactly when you least
want it to.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from .config import RateLimits


class TokenBucket:
    """Sliding-window request limiter. Thread-safe."""

    def __init__(self, max_requests: int, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._hits and now - self._hits[0] >= self.window:
            self._hits.popleft()

    def time_until_slot(self) -> float:
        """Seconds to wait before a request would be allowed. 0 if allowed now."""
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            if len(self._hits) < self.max_requests:
                return 0.0
            return self.window - (now - self._hits[0])

    def acquire(self, block: bool = True) -> bool:
        """Consume a slot. Returns False if unavailable and block=False."""
        while True:
            wait = self.time_until_slot()
            if wait <= 0:
                with self._lock:
                    now = time.monotonic()
                    self._prune(now)
                    if len(self._hits) < self.max_requests:
                        self._hits.append(now)
                        return True
                continue  # lost the race, re-evaluate
            if not block:
                return False
            time.sleep(min(wait, 5.0))


class PostingWindow:
    """Enforces posts/day, minimum spacing, allowed hours, and slot jitter."""

    def __init__(
        self,
        limits: RateLimits,
        state_dir: Path,
        posting_hours: tuple[int, ...],
        jitter_key: str = "default",
    ):
        self.limits = limits
        self.posting_hours = posting_hours
        self.jitter_key = jitter_key
        self.path = Path(state_dir) / "posting_history.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def slot_offset_minutes(self, when: datetime) -> int:
        """Minutes into the hour that this slot unlocks.

        Derived by hashing the date and hour, so it is stable across restarts
        (a process bounce cannot reroll it into posting early) but differs from
        day to day. Posting at exactly :00 daily is a bot signature; this is the
        cheapest way to not look like one.
        """
        if self.limits.jitter_minutes <= 0:
            return 0
        seed = f"{self.jitter_key}:{when:%Y-%m-%d}:{when.hour}"
        digest = hashlib.sha256(seed.encode()).digest()
        return int.from_bytes(digest[:4], "big") % self.limits.jitter_minutes

    def _load(self) -> list[float]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text()).get("posted_at", [])
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, stamps: list[float]) -> None:
        cutoff = time.time() - 7 * 86400
        self.path.write_text(json.dumps({"posted_at": [s for s in stamps if s > cutoff]}))

    def record_post(self, when: float | None = None) -> None:
        with self._lock:
            stamps = self._load()
            stamps.append(when if when is not None else time.time())
            self._save(stamps)

    def check(self, now: float | None = None) -> tuple[bool, str]:
        """Return (allowed, reason). Reason explains the block when False."""
        now = now if now is not None else time.time()
        stamps = self._load()

        recent = [s for s in stamps if now - s < 86400]
        if len(recent) >= self.limits.posts_per_day:
            return False, (
                f"daily cap reached ({len(recent)}/{self.limits.posts_per_day} in last 24h)"
            )

        if stamps:
            gap = now - max(stamps)
            if gap < self.limits.min_seconds_between_posts:
                remaining = int(self.limits.min_seconds_between_posts - gap)
                return False, f"minimum spacing not met ({remaining}s remaining)"

        when = datetime.fromtimestamp(now)
        if self.posting_hours:
            if when.hour not in self.posting_hours:
                return False, (
                    f"outside posting hours (now {when.hour:02d}:00, "
                    f"allowed {self.posting_hours})"
                )
            offset = self.slot_offset_minutes(when)
            if when.minute < offset:
                return False, (
                    f"slot opens at {when.hour:02d}:{offset:02d} "
                    f"(jitter); now {when.hour:02d}:{when.minute:02d}"
                )

        return True, "ok"

    def next_allowed_time(self, now: float | None = None) -> float:
        """Earliest timestamp at which a post would pass ``check``."""
        now = now if now is not None else time.time()
        stamps = self._load()
        candidate = now

        if stamps:
            candidate = max(candidate, max(stamps) + self.limits.min_seconds_between_posts)

        recent = sorted(s for s in stamps if now - s < 86400)
        if len(recent) >= self.limits.posts_per_day:
            # Wait until the oldest post in the window falls out of it.
            overflow = len(recent) - self.limits.posts_per_day
            candidate = max(candidate, recent[overflow] + 86400)

        if self.posting_hours:
            for _ in range(48):  # scan forward at most two days
                dt = datetime.fromtimestamp(candidate)
                if dt.hour in self.posting_hours:
                    slot_open = dt.replace(
                        minute=self.slot_offset_minutes(dt), second=0, microsecond=0
                    ).timestamp()
                    if candidate <= slot_open:
                        candidate = slot_open
                        break
                    # Past this slot's jittered opening but still inside the
                    # hour: it is open now, so the candidate already stands.
                    if dt.minute >= self.slot_offset_minutes(dt):
                        break
                candidate = dt.replace(minute=0, second=0, microsecond=0).timestamp() + 3600

        return candidate
