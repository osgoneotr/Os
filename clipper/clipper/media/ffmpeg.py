"""Thin subprocess wrapper around ffmpeg/ffprobe.

Deliberately not moviepy: we want one ffmpeg invocation per clip with an
explicit filter graph. That is faster, avoids a second generation of re-encode
loss, and — more importantly — the failing command is printable and pasteable
when a render misbehaves.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    def __init__(self, args: list[str], returncode: int, stderr: str):
        self.args_list = args
        self.returncode = returncode
        self.stderr = stderr
        tail = "\n".join(stderr.strip().splitlines()[-25:])
        super().__init__(
            f"ffmpeg exited {returncode}\n"
            f"command: {' '.join(args)}\n"
            f"--- stderr (tail) ---\n{tail}"
        )


@functools.lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    """Resolve ffmpeg: explicit env var, then PATH, then the static build from
    imageio-ffmpeg if that extra is installed."""
    env = os.environ.get("FFMPEG_BINARY")
    if env and Path(env).exists():
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - depends on environment
        raise FileNotFoundError(
            "ffmpeg not found. Install it (apt install ffmpeg / brew install ffmpeg), "
            "set FFMPEG_BINARY, or `pip install 'clipper[ffmpeg]'` for a bundled build."
        ) from exc


@functools.lru_cache(maxsize=1)
def ffprobe_path() -> str | None:
    """ffprobe is optional — the static imageio build does not ship it, so
    probing falls back to parsing ``ffmpeg -i`` output."""
    env = os.environ.get("FFPROBE_BINARY")
    if env and Path(env).exists():
        return env
    return shutil.which("ffprobe")


def run_ffmpeg(args: list[str], quiet: bool = True) -> str:
    """Run ffmpeg with the given args (without the binary itself). Returns stderr,
    which is where ffmpeg writes all its diagnostics."""
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin"]
    if quiet:
        cmd += ["-loglevel", "error"]
    cmd += args
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.returncode, proc.stderr)
    return proc.stderr


def run_ffmpeg_pipe(args: list[str]) -> bytes:
    """Run ffmpeg writing raw bytes to stdout (used for PCM extraction)."""
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error"] + args
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.returncode, proc.stderr.decode("utf-8", "replace"))
    return proc.stdout


def escape_filter_path(path: str | Path) -> str:
    r"""Escape a path for use inside a filter argument (e.g. ``subtitles=``).

    libavfilter parses ``:`` as an option separator and ``\`` / ``'`` as escapes,
    so a Windows path or any path with a colon breaks the graph unless escaped.
    """
    s = str(path)
    s = s.replace("\\", "\\\\")
    s = s.replace(":", r"\:")
    s = s.replace("'", r"\'")
    s = s.replace("[", r"\[").replace("]", r"\]")
    s = s.replace(",", r"\,")
    return s
