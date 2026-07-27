"""Media probing with an ffprobe-free fallback."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ..models import MediaInfo
from .ffmpeg import ffmpeg_path, ffprobe_path

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")
_VIDEO_RE = re.compile(
    r"Stream #\d+:\d+.*?: Video: (\w+).*?, (\d+)x(\d+).*?, ([\d.]+) fps", re.S
)
_VIDEO_NOFPS_RE = re.compile(r"Stream #\d+:\d+.*?: Video: (\w+).*?, (\d+)x(\d+)")
_AUDIO_RE = re.compile(r"Stream #\d+:\d+.*?: Audio: (\w+)")


def probe(path: str | Path) -> MediaInfo:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"source not found: {p}")
    probe_bin = ffprobe_path()
    if probe_bin:
        try:
            return _probe_with_ffprobe(probe_bin, p)
        except Exception:
            pass  # fall through to the ffmpeg parser
    return _probe_with_ffmpeg(p)


def _probe_with_ffprobe(binary: str, path: Path) -> MediaInfo:
    proc = subprocess.run(
        [
            binary,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise ValueError(f"no video stream in {path}")
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    return MediaInfo(
        path=path,
        duration=duration,
        width=int(video["width"]),
        height=int(video["height"]),
        fps=_parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        has_audio=audio is not None,
        video_codec=video.get("codec_name", ""),
        audio_codec=(audio or {}).get("codec_name", ""),
    )


def _probe_with_ffmpeg(path: Path) -> MediaInfo:
    """Parse ``ffmpeg -i`` stderr. Less precise than ffprobe but keeps the tool
    usable with the static ffmpeg builds that omit ffprobe."""
    proc = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
    )
    err = proc.stderr
    duration = 0.0
    if m := _DURATION_RE.search(err):
        h, mnt, sec = m.groups()
        duration = int(h) * 3600 + int(mnt) * 60 + float(sec)

    fps = 0.0
    if m := _VIDEO_RE.search(err):
        codec, w, h, fps_s = m.groups()
        width, height, fps = int(w), int(h), float(fps_s)
    elif m := _VIDEO_NOFPS_RE.search(err):
        codec, w, h = m.groups()
        width, height = int(w), int(h)
    else:
        raise ValueError(f"could not parse video stream from {path}:\n{err[-2000:]}")

    audio_match = _AUDIO_RE.search(err)
    return MediaInfo(
        path=path,
        duration=duration,
        width=width,
        height=height,
        fps=fps or 30.0,
        has_audio=audio_match is not None,
        video_codec=codec,
        audio_codec=audio_match.group(1) if audio_match else "",
    )


def _parse_rate(rate: str | None) -> float:
    if not rate:
        return 30.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            den_f = float(den)
            return float(num) / den_f if den_f else 30.0
        except ValueError:
            return 30.0
    try:
        return float(rate)
    except ValueError:
        return 30.0
