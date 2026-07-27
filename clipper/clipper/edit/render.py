"""Clip rendering: one ffmpeg invocation per clip, one explicit filter graph.

Order matters and is deliberate:

1. trim on the *input* side (``-ss`` before ``-i``) so ffmpeg seeks instead of
   decoding from zero — the difference on a 2-hour source is minutes per clip;
2. reframe to 1080x1920 *before* burning captions, so the ASS PlayRes matches
   the final canvas and font sizes mean what they say;
3. overlay branding last, so it sits above the captions;
4. loudness-normalize the voice before mixing music, so the ducking threshold
   behaves the same on every source.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import RenderConfig
from ..media.ffmpeg import escape_filter_path, run_ffmpeg
from ..models import MediaInfo
from .reframe import TrackPoint, reframe_graph

LOUDNESS_TARGET = "I=-14:TP=-1.5:LRA=11"
"""-14 LUFS integrated: roughly where TikTok/Reels/Shorts normalize to, so the
clip is not turned down (or pumped up) on playback."""


@dataclass
class RenderPlan:
    """Everything needed to run — kept separate from execution so `--dry-run`
    can print the exact command."""

    args: list[str]
    output: Path
    filter_complex: str


def build_render_plan(
    info: MediaInfo,
    start: float,
    end: float,
    output: Path,
    config: RenderConfig,
    ass_path: Path | None = None,
    track: list[TrackPoint] | None = None,
) -> RenderPlan:
    duration = max(0.01, end - start)
    out_w, out_h = config.width, config.height

    args: list[str] = ["-y", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(info.path)]
    next_input = 1

    silence_input: int | None = None
    if not info.has_audio:
        args += [
            "-f",
            "lavfi",
            "-t",
            f"{duration:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
        ]
        silence_input = next_input
        next_input += 1

    music_input: int | None = None
    if config.music_path:
        args += ["-stream_loop", "-1", "-i", str(config.music_path)]
        music_input = next_input
        next_input += 1

    logo_input: int | None = None
    if config.logo_path:
        args += ["-i", str(config.logo_path)]
        logo_input = next_input
        next_input += 1

    parts: list[str] = [reframe_graph(config.reframe, info, out_w, out_h, "0:v", "vref", track)]
    video_label = "vref"

    if ass_path is not None:
        escaped = escape_filter_path(ass_path)
        parts.append(f"[{video_label}]subtitles=filename='{escaped}'[vsub]")
        video_label = "vsub"

    if logo_input is not None:
        logo_w = max(2, int(out_w * config.logo_width_pct / 100))
        margin = int(out_w * config.logo_margin_pct / 100)
        parts.append(f"[{logo_input}:v]scale={logo_w}:-1[logo]")
        parts.append(f"[{video_label}][logo]overlay={_logo_xy(config.logo_position, margin)}[vout]")
        video_label = "vout"

    audio_source = f"{silence_input}:a" if silence_input is not None else "0:a"
    audio_label = _audio_graph(parts, audio_source, music_input, config)

    filter_complex = ";".join(parts)
    args += ["-filter_complex", filter_complex, "-map", f"[{video_label}]", "-map", f"[{audio_label}]"]

    if config.fps:
        args += ["-r", str(config.fps)]

    args += [
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        str(config.crf),
        "-preset",
        config.preset,
        "-c:a",
        "aac",
        "-b:a",
        config.audio_bitrate,
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-shortest",
        str(output),
    ]
    return RenderPlan(args=args, output=output, filter_complex=filter_complex)


def _audio_graph(
    parts: list[str], source: str, music_input: int | None, config: RenderConfig
) -> str:
    voice = "avoice"
    chain = "aresample=48000"
    if config.loudnorm:
        chain += f",loudnorm={LOUDNESS_TARGET}"
    parts.append(f"[{source}]{chain}[{voice}]")

    if music_input is None:
        return voice

    if config.music_duck:
        # Split the voice: one copy is mixed, the other keys the compressor.
        parts.append(f"[{voice}]asplit=2[amain][akey]")
        parts.append(f"[{music_input}:a]aresample=48000,volume={config.music_gain_db}dB[amus]")
        parts.append(
            "[amus][akey]sidechaincompress="
            "threshold=0.03:ratio=12:attack=15:release=350:makeup=1[aduck]"
        )
        parts.append("[amain][aduck]amix=inputs=2:duration=first:normalize=0[amixed]")
    else:
        parts.append(f"[{music_input}:a]aresample=48000,volume={config.music_gain_db}dB[amus]")
        parts.append(f"[{voice}][amus]amix=inputs=2:duration=first:normalize=0[amixed]")
    return "amixed"


def _logo_xy(position: str, margin: int) -> str:
    return {
        "top_left": f"{margin}:{margin}",
        "top_right": f"W-w-{margin}:{margin}",
        "bottom_left": f"{margin}:H-h-{margin}",
        "bottom_right": f"W-w-{margin}:H-h-{margin}",
    }.get(position, f"W-w-{margin}:{margin}")


def render_clip(
    info: MediaInfo,
    start: float,
    end: float,
    output: Path,
    config: RenderConfig,
    ass_path: Path | None = None,
    track: list[TrackPoint] | None = None,
    dry_run: bool = False,
) -> RenderPlan:
    plan = build_render_plan(info, start, end, output, config, ass_path, track)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not dry_run:
        run_ffmpeg(plan.args)
    return plan
