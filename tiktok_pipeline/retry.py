"""Exponential backoff with jitter.

Jitter is not decoration: without it, a batch of posts that all fail at the
same moment retry in lockstep and reproduce the burst that caused the failure.
"""

from __future__ import annotations

import functools
import logging
import random
import time
from typing import Callable, TypeVar

from .errors import RateLimitedError, RetryableError, TerminalError

log = logging.getLogger(__name__)

T = TypeVar("T")


def backoff_delay(attempt: int, base: float = 2.0, cap: float = 300.0) -> float:
    """Full-jitter backoff: random between 0 and min(cap, base * 2**attempt)."""
    ceiling = min(cap, base * (2**attempt))
    return random.uniform(0, ceiling)


def with_retries(
    max_attempts: int = 5,
    base: float = 2.0,
    cap: float = 300.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Retry RetryableError; let TerminalError through untouched.

    Honours ``retry_after`` when TikTok supplies one on a 429.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            last: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    return fn(*args, **kwargs)
                except TerminalError:
                    raise  # never retry -- the request itself is wrong
                except RetryableError as exc:
                    last = exc
                    if attempt == max_attempts - 1:
                        break
                    if isinstance(exc, RateLimitedError) and exc.retry_after:
                        delay = exc.retry_after
                    else:
                        delay = backoff_delay(attempt, base, cap)
                    log.warning(
                        "%s failed (attempt %d/%d): %s -- retrying in %.1fs",
                        fn.__name__, attempt + 1, max_attempts, exc, delay,
                    )
                    sleep(delay)
            assert last is not None
            raise last

        return wrapper

    return decorator
