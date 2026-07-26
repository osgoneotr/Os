"""Clip preparation: 9:16 conversion, UI safe zones, burned-in captions.

Encoder settings target TikTok's accepted upload formats (MP4/MOV/WEBM,
H.264 + AAC). The safe-zone geometry below is measured against TikTok's
current in-app overlay layout at 1080x1920 and carries deliberate margin --
the UI shifts between app versions and A/B tests, so hugging the exact pixel
boundary is how captions end up under the share button next quarter.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

TARGET_W, TARGET_H = 1080, 1920


@dataclass(frozen=True)
class SafeZone:
    """Regions of the frame TikTok's own UI draws over.

    Nothing load-bearing -- captions, faces, key action -- should land inside
    these margins.
    """

    top: int = 220        # status bar, "Following | For You" tabs, search icon
    bottom: int = 500     # username, caption, sound ticker, progress bar
    right: int = 200      # avatar, like/comment/share/bookmark rail, spinning disc
    left: int = 60        # minor, but text hard against the edge reads as cropped

    @property
    def text_top(self) -> int:
        return self.top

    @property
    def text_bottom(self) -> int:
        return TARGET_H - self.bottom

    @property
    def usable_height(self) -> int:
        return self.text_bottom - self.text_top

    def caption_y(self) -> int:
        """Vertical anchor for burned-in captions.

        Placed slightly below centre: high enough to clear the caption/sound
        overlay, low enough that it doesn't fight the subject's face.
        """
        return int(self.text_top + self.usable_height * 0.62)


DEFAULT_SAFE_ZONE = SafeZone()


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg not found on PATH. Install it: "
            "apt-get install ffmpeg | brew install ffmpeg"
        )


def probe(path: Path) -> dict:
    """Return width, height, duration and fps for a media file."""
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe not found on PATH (ships with ffmpeg).")
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate:format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    stream = data["streams"][0]
    num, _, den = stream["r_frame_rate"].partition("/")
    fps = float(num) / float(den or 1)
    return {
        "width": stream["width"],
        "height": stream["height"],
        "duration": float(data["format"]["duration"]),
        "fps": fps,
    }


def build_vertical_filter(safe: SafeZone = DEFAULT_SAFE_ZONE, blur_pad: bool = True) -> str:
    """Filtergraph converting any aspect ratio to 1080x1920.

    ``blur_pad`` fills the letterbox with a blurred, scaled copy of the source
    instead of black bars. Black bars read as lazily reposted content to
    viewers and cost you retention in the first second.
    """
    if blur_pad:
        return (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_W}:{TARGET_H},gblur=sigma=28[bgb];"
            f"[fg]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    return (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
    )


def _escape_filter_path(path: Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def srt_to_styled_ass(
    srt_path: Path,
    ass_path: Path,
    safe: SafeZone = DEFAULT_SAFE_ZONE,
    font_size: int = 58,
    font_name: str = "Arial Black",
) -> Path:
    """Convert an SRT to ASS with a 1080x1920 script resolution and our style.

    This exists because of a trap: ffmpeg converts SRT to ASS with
    ``PlayResX: 384, PlayResY: 288``, and libass interprets every geometry
    value -- FontSize, MarginV, Outline -- in *that* coordinate space before
    scaling to the video. Passing pixel values via ``force_style`` therefore
    misses by a factor of 1920/288 = 6.67: a 58px font renders ~387px tall,
    and a MarginV meant to sit mid-frame lands several screen-heights off
    the top, silently producing a video with no visible captions at all.

    Rewriting PlayRes to the real frame size makes every value mean pixels.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH.")

    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(srt_path), str(ass_path), "-loglevel", "error"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"SRT -> ASS conversion failed:\n{result.stderr[-1000:]}")

    # ASS colours are &HAABBGGRR: AA=00 is opaque, AA=80 half-transparent.
    margin_h = max(safe.left, safe.right)  # symmetric, so text stays frame-centred
    margin_v = TARGET_H - safe.caption_y()
    style = (
        f"Style: Default,{font_name},{font_size},"
        f"&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,"       # bold on, no italic/underline/strikeout
        f"1,4,2,"                       # BorderStyle=outline, Outline=4px, Shadow=2px
        f"2,"                           # Alignment=2 (bottom-centre)
        f"{margin_h},{margin_h},{margin_v},1"
    )

    out_lines: list[str] = []
    for line in ass_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("PlayResX:"):
            out_lines.append(f"PlayResX: {TARGET_W}")
        elif line.startswith("PlayResY:"):
            out_lines.append(f"PlayResY: {TARGET_H}")
        elif line.startswith("Style: Default,"):
            out_lines.append(style)
        else:
            out_lines.append(line)

    text = "\n".join(out_lines) + "\n"
    # ffmpeg omits PlayRes entirely for some inputs; inject it if missing.
    if "PlayResX:" not in text:
        text = text.replace(
            "[Script Info]",
            f"[Script Info]\nPlayResX: {TARGET_W}\nPlayResY: {TARGET_H}",
            1,
        )
    ass_path.write_text(text, encoding="utf-8")
    return ass_path


def build_caption_filter(ass_path: Path) -> str:
    """Filter string burning a prepared ASS file into the video.

    Takes an ASS produced by :func:`srt_to_styled_ass` -- the styling lives in
    the file, not in ``force_style``, so the values are in real pixels.

    Burned-in captions matter twice: most feed viewing starts muted, and
    TikTok's own auto-captions can land in the overlay zone where they get
    covered.
    """
    return f"ass='{_escape_filter_path(ass_path)}'"


def prepare_clip(
    src: Path,
    dst: Path,
    srt: Path | None = None,
    safe: SafeZone = DEFAULT_SAFE_ZONE,
    blur_pad: bool = True,
    max_duration: float | None = None,
    crf: int = 20,
    fps: int | None = None,
    font_size: int = 58,
    font_name: str = "Arial Black",
) -> Path:
    """Render a TikTok-ready MP4.

    Output: 1080x1920, H.264 high/yuv420p, AAC 128k 44.1kHz stereo, faststart.
    yuv420p and +faststart are the two settings that most often cause an
    otherwise-valid file to be rejected or to stall on playback.

    ``fps`` defaults to None, preserving the source frame rate. Pass an int to
    resample. TikTok accepts 23-60 fps, so 60 fps sources are best left alone.
    """
    _require_ffmpeg()
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    if not 1 <= crf <= 51:
        # crf=0 is lossless, which x264 rejects under -profile:v high with an
        # opaque "high profile doesn't support lossless" error. TikTok
        # re-encodes on ingest anyway, so lossless would only waste bandwidth.
        raise ValueError(f"crf must be between 1 and 51 (got {crf}); 18-23 is the useful range")

    chain = build_vertical_filter(safe, blur_pad)
    tmp_ass: Path | None = None
    if srt is not None:
        srt = Path(srt)
        if not srt.exists():
            raise FileNotFoundError(f"Subtitle file not found: {srt}")
        if srt.suffix.lower() == ".ass":
            chain = f"{chain},{build_caption_filter(srt)}"
        else:
            tmp_ass = dst.with_suffix(".styled.ass")
            srt_to_styled_ass(srt, tmp_ass, safe, font_size, font_name)
            chain = f"{chain},{build_caption_filter(tmp_ass)}"

    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if blur_pad:
        # The split/overlay graph needs -filter_complex. Naming the final node
        # [v] lets us map video and audio explicitly -- using -map at all
        # disables ffmpeg's automatic stream selection, so both must be listed.
        cmd += ["-filter_complex", f"{chain}[v]", "-map", "[v]", "-map", "0:a?"]
    else:
        cmd += ["-vf", chain]
    if max_duration:
        cmd += ["-t", str(max_duration)]

    cmd += [
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
        "-preset", "medium", "-crf", str(crf),
        "-pix_fmt", "yuv420p",
    ]
    if fps is not None:
        # Only resample when asked. Forcing 30 unconditionally would throw away
        # half the frames of 60 fps gameplay footage, which is exactly the
        # source material this pipeline is aimed at.
        cmd += ["-r", str(fps), "-g", str(fps * 2)]

    cmd += [
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart",
        str(dst),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if tmp_ass is not None:
        tmp_ass.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr[-2000:]}")
    return dst


def validate_for_tiktok(path: Path, max_duration: float = 600.0) -> list[str]:
    """Return a list of spec problems. Empty list means the file looks good."""
    problems: list[str] = []
    info = probe(path)

    if (info["width"], info["height"]) != (TARGET_W, TARGET_H):
        problems.append(
            f"Resolution is {info['width']}x{info['height']}, expected {TARGET_W}x{TARGET_H}"
        )
    if info["duration"] > max_duration:
        problems.append(f"Duration {info['duration']:.1f}s exceeds {max_duration:.0f}s")
    if info["duration"] < 3:
        problems.append(f"Duration {info['duration']:.1f}s is under the 3s minimum")
    if not (23 <= info["fps"] <= 61):
        problems.append(f"Frame rate {info['fps']:.2f} is outside the 23-60 fps range")

    size_gb = path.stat().st_size / 1e9
    if size_gb > 4:
        problems.append(f"File is {size_gb:.2f} GB, over the 4 GB upload limit")

    return problems
