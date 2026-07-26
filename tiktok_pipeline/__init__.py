"""TikTok-first automated posting pipeline.

See README.md for the compliance summary -- read the go/no-go section before
building on this.
"""

from .config import Settings, RateLimits
from .client import TikTokClient, PostRequest, CreatorInfo
from .queue import PostQueue, QueueWorker, JobStatus
from .prep import prepare_clip, validate_for_tiktok, SafeZone

__version__ = "0.1.0"

__all__ = [
    "Settings",
    "RateLimits",
    "TikTokClient",
    "PostRequest",
    "CreatorInfo",
    "PostQueue",
    "QueueWorker",
    "JobStatus",
    "prepare_clip",
    "validate_for_tiktok",
    "SafeZone",
]
