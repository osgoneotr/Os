"""Durable posting queue.

SQLite-backed rather than in-memory: a posting queue that loses its state on
restart will re-post videos it already published, which is both embarrassing
and a fast route to a spam flag. Every job carries an idempotency key derived
from the video file, so a crash mid-publish cannot produce a duplicate.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from .client import PostRequest, TikTokClient
from .config import Settings
from .errors import TerminalError, TikTokError
from .ratelimit import PostingWindow, TokenBucket
from .retry import backoff_delay

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    idem_key        TEXT NOT NULL UNIQUE,
    account         TEXT NOT NULL,
    video_path      TEXT NOT NULL,
    title           TEXT NOT NULL,
    privacy_level   TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'direct',
    options_json    TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'PENDING',
    attempts        INTEGER NOT NULL DEFAULT 0,
    not_before      REAL NOT NULL DEFAULT 0,
    publish_id      TEXT,
    last_error      TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_ready ON jobs(status, not_before);
"""


class PostMode(str, Enum):
    """How a job reaches TikTok."""

    DIRECT = "direct"  # publishes immediately; needs the audited video.publish scope
    INBOX = "inbox"    # lands as a draft for the creator to finish; no audit needed


class JobStatus(str, Enum):
    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"  # terminal compliance failure; needs human attention


@dataclass
class Job:
    id: int
    idem_key: str
    account: str
    video_path: str
    title: str
    privacy_level: str
    mode: str
    options: dict[str, Any]
    status: str
    attempts: int
    not_before: float
    publish_id: str | None
    last_error: str | None

    def to_request(self) -> PostRequest:
        return PostRequest(
            video_path=Path(self.video_path),
            title=self.title,
            privacy_level=self.privacy_level,
            **self.options,
        )


def idempotency_key(video_path: Path, title: str, account: str) -> str:
    """Stable key from file content + caption + account.

    Hashes the first and last 1 MB plus the size rather than the whole file --
    enough to distinguish renders without reading gigabytes.
    """
    h = hashlib.sha256()
    h.update(account.encode())
    h.update(title.encode())
    size = video_path.stat().st_size
    h.update(str(size).encode())
    with video_path.open("rb") as fh:
        h.update(fh.read(1024 * 1024))
        if size > 2 * 1024 * 1024:
            fh.seek(-1024 * 1024, 2)
            h.update(fh.read())
    return h.hexdigest()[:32]


class PostQueue:
    def __init__(self, settings: Settings):
        self.settings = settings
        db_dir = Path(settings.state_dir)
        db_dir.mkdir(parents=True, exist_ok=True)
        # isolation_level=None puts the driver in autocommit mode so we can
        # issue BEGIN IMMEDIATE by hand. claim_next needs the write lock held
        # across its SELECT and UPDATE; a deferred transaction (the default)
        # takes no lock on the SELECT, letting two workers claim the same job.
        self.db = sqlite3.connect(
            db_dir / "queue.db", check_same_thread=False, isolation_level=None
        )
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Additive migrations for databases created by an earlier version.

        CREATE TABLE IF NOT EXISTS silently leaves an existing table alone, so
        a queue.db from before a column was added would otherwise fail on every
        read with 'no such column'.
        """
        existing = {row["name"] for row in self.db.execute("PRAGMA table_info(jobs)")}
        for column, ddl in [
            ("mode", "ALTER TABLE jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'direct'"),
        ]:
            if column not in existing:
                self.db.execute(ddl)

    # ---- enqueue / inspect -------------------------------------------

    def enqueue(
        self,
        account: str,
        video_path: Path,
        title: str,
        privacy_level: str = "SELF_ONLY",
        mode: str = PostMode.DIRECT.value,
        not_before: float | None = None,
        **options: Any,
    ) -> int | None:
        """Add a job. Returns None if this exact post is already queued."""
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(video_path)
        if mode not in {m.value for m in PostMode}:
            raise ValueError(
                f"mode must be one of {[m.value for m in PostMode]} (got {mode!r})"
            )

        key = idempotency_key(video_path, title, account)
        now = time.time()
        try:
            cur = self.db.execute(
                """INSERT INTO jobs
                   (idem_key, account, video_path, title, privacy_level, mode,
                    options_json, not_before, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    key, account, str(video_path), title, privacy_level, mode,
                    json.dumps(options),
                    now if not_before is None else not_before,
                    now, now,
                ),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            log.info("Skipping duplicate post (idem_key=%s): %s", key, video_path.name)
            return None

    def _row_to_job(self, row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            idem_key=row["idem_key"],
            account=row["account"],
            video_path=row["video_path"],
            title=row["title"],
            privacy_level=row["privacy_level"],
            mode=row["mode"],
            options=json.loads(row["options_json"]),
            status=row["status"],
            attempts=row["attempts"],
            not_before=row["not_before"],
            publish_id=row["publish_id"],
            last_error=row["last_error"],
        )

    def claim_next(self, now: float | None = None) -> Job | None:
        """Atomically take the next due job and mark it IN_FLIGHT.

        Holds the write lock for the whole read-then-claim so concurrent
        workers cannot both take the same row.
        """
        now = now if now is not None else time.time()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                """SELECT * FROM jobs
                   WHERE status = ? AND not_before <= ?
                   ORDER BY not_before ASC, id ASC LIMIT 1""",
                (JobStatus.PENDING.value, now),
            ).fetchone()
            if row is None:
                self.db.execute("ROLLBACK")
                return None
            self.db.execute(
                "UPDATE jobs SET status=?, updated_at=? WHERE id=?",
                (JobStatus.IN_FLIGHT.value, now, row["id"]),
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        job = self._row_to_job(row)
        job.status = JobStatus.IN_FLIGHT.value  # row was read pre-update
        return job

    def mark(
        self,
        job_id: int,
        status: JobStatus,
        *,
        publish_id: str | None = None,
        error: str | None = None,
        not_before: float | None = None,
        bump_attempts: bool = False,
    ) -> None:
        sets = ["status=?", "updated_at=?"]
        vals: list[Any] = [status.value, time.time()]
        if publish_id is not None:
            sets.append("publish_id=?")
            vals.append(publish_id)
        if error is not None:
            sets.append("last_error=?")
            vals.append(error[:1000])
        if not_before is not None:
            sets.append("not_before=?")
            vals.append(not_before)
        if bump_attempts:
            sets.append("attempts=attempts+1")
        vals.append(job_id)
        self.db.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", vals)

    def list_jobs(self, status: str | None = None) -> Iterator[Job]:
        sql = "SELECT * FROM jobs"
        args: tuple = ()
        if status:
            sql += " WHERE status=?"
            args = (status,)
        sql += " ORDER BY not_before ASC"
        for row in self.db.execute(sql, args):
            yield self._row_to_job(row)

    def requeue_stale_inflight(self, older_than: float = 3600) -> int:
        """Recover jobs orphaned by a crash mid-publish.

        These are moved to BLOCKED, not PENDING -- an IN_FLIGHT job may already
        have published, so re-running it blind risks a duplicate post. A human
        should check the account and either requeue or drop it.
        """
        cutoff = time.time() - older_than
        cur = self.db.execute(
            "UPDATE jobs SET status=?, last_error=? WHERE status=? AND updated_at < ?",
            (
                JobStatus.BLOCKED.value,
                "Orphaned in flight -- verify on TikTok whether it published before requeueing.",
                JobStatus.IN_FLIGHT.value,
                cutoff,
            ),
        )
        return cur.rowcount


class QueueWorker:
    """Drains the queue, respecting both rate gates."""

    def __init__(self, settings: Settings, client: TikTokClient | None = None):
        self.settings = settings
        self.queue = PostQueue(settings)
        self.client = client or TikTokClient(settings)
        self.bucket = TokenBucket(settings.rate_limits.requests_per_minute)
        self.window = PostingWindow(
            settings.rate_limits,
            Path(settings.state_dir),
            settings.posting_hours,
            jitter_key=settings.client_key,
        )
        self.max_attempts = 5

    def run_once(self) -> bool:
        """Attempt one job. Returns True if a job was processed."""
        allowed, reason = self.window.check()
        if not allowed:
            log.info("Posting window closed: %s", reason)
            return False

        job = self.queue.claim_next()
        if job is None:
            return False

        log.info(
            "Processing job %d (%s): %s", job.id, job.mode, Path(job.video_path).name
        )
        self.bucket.acquire(block=True)

        try:
            if job.mode == PostMode.INBOX.value:
                result = self.client.upload_to_inbox(job.account, job.to_request())
            else:
                result = self.client.post_video(job.account, job.to_request())
        except TerminalError as exc:
            # Wrong request, not bad luck. Park it for a human.
            log.error("Job %d blocked: %s", job.id, exc)
            self.queue.mark(job.id, JobStatus.BLOCKED, error=str(exc), bump_attempts=True)
            return True
        except TikTokError as exc:
            attempts = job.attempts + 1
            if attempts >= self.max_attempts:
                log.error("Job %d failed permanently after %d attempts: %s", job.id, attempts, exc)
                self.queue.mark(job.id, JobStatus.FAILED, error=str(exc), bump_attempts=True)
            else:
                delay = backoff_delay(attempts)
                log.warning("Job %d failed (%s); retrying in %.0fs", job.id, exc, delay)
                self.queue.mark(
                    job.id,
                    JobStatus.PENDING,
                    error=str(exc),
                    not_before=time.time() + delay,
                    bump_attempts=True,
                )
            return True

        self.queue.mark(
            job.id, JobStatus.PUBLISHED, publish_id=result.get("publish_id"), bump_attempts=True
        )
        self.window.record_post()
        if job.mode == PostMode.INBOX.value:
            log.info(
                "Job %d delivered to inbox (publish_id=%s) -- open TikTok to finish it",
                job.id, result.get("publish_id"),
            )
        else:
            log.info("Job %d published (publish_id=%s)", job.id, result.get("publish_id"))
        return True

    def run_forever(self, poll_interval: int = 60) -> None:
        self.queue.requeue_stale_inflight()
        log.info("Worker started; polling every %ds", poll_interval)
        while True:
            try:
                if not self.run_once():
                    time.sleep(poll_interval)
            except KeyboardInterrupt:
                log.info("Worker stopped")
                return
            except Exception:
                log.exception("Unexpected worker error; continuing")
                time.sleep(poll_interval)
