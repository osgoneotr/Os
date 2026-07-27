"""Landscape -> 9:16 reframing.

Three modes, in increasing order of cost:

* ``center``   — static centre crop. Correct for anything already framed centrally.
* ``blur_pad`` — fit the full frame, fill the rest with a blurred, zoomed copy.
  The safe choice when the source frame matters (screen shares, b-roll, two-shots).
* ``track``    — sample the clip, follow the speaker's face (or failing that, the
  motion centroid), and drive the crop's x position with a smoothed path.

``track`` needs OpenCV; without it we log a warning and fall back to ``center``
rather than failing the render.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from ..media.ffmpeg import run_ffmpeg_pipe
from ..models import MediaInfo

log = logging.getLogger(__name__)

SAMPLE_FPS = 2.0
SAMPLE_WIDTH = 320
SMOOTH_WINDOW = 5
"""Samples in the moving average. At 2 fps this is a 2.5s window — enough to
kill jitter without the crop lagging a real position change."""


@dataclass
class TrackPoint:
    t: float
    center_x: float
    """Centre of interest in *source* pixel coordinates."""


def even(value: float) -> int:
    """H.264 needs even dimensions."""
    return int(round(value / 2) * 2)


def crop_size(info: MediaInfo, out_w: int, out_h: int) -> tuple[int, int]:
    """Largest crop of the source with the output's aspect ratio."""
    target_ar = out_w / out_h
    src_ar = info.width / info.height
    if src_ar > target_ar:
        crop_h = info.height
        crop_w = even(info.height * target_ar)
    else:
        crop_w = info.width
        crop_h = even(info.width / target_ar)
    return min(crop_w, info.width), min(crop_h, info.height)


def reframe_graph(
    mode: str,
    info: MediaInfo,
    out_w: int,
    out_h: int,
    in_label: str = "0:v",
    out_label: str = "vref",
    track: list[TrackPoint] | None = None,
) -> str:
    """Return the filter-graph fragment that turns ``in_label`` into ``out_label``."""
    if mode == "blur_pad":
        return (
            f"[{in_label}]split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h},gblur=sigma=28[bg];"
            f"[fgsrc]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:"
            f"flags=lanczos[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[{out_label}]"
        )

    crop_w, crop_h = crop_size(info, out_w, out_h)
    y = even((info.height - crop_h) / 2)

    if mode == "track" and track:
        x_expr = _x_expression(track, crop_w, info.width)
        crop = f"crop={crop_w}:{crop_h}:x='{x_expr}':y={y}"
    else:
        if mode == "track":
            log.warning("track mode requested but no tracking path available; centring")
        x = even((info.width - crop_w) / 2)
        crop = f"crop={crop_w}:{crop_h}:{x}:{y}"

    return (
        f"[{in_label}]{crop},scale={out_w}:{out_h}:flags=lanczos,setsar=1[{out_label}]"
    )


def _x_expression(track: list[TrackPoint], crop_w: int, src_w: int) -> str:
    """Piecewise-linear x(t) as a flat sum of gated ramps.

    A sum of ``gte(t,a)*lt(t,b)*(...)`` terms rather than nested ``if()`` calls:
    ffmpeg evaluates it per frame either way, but the flat form has no nesting
    depth to blow up on a 60s clip sampled twice a second.
    """
    max_x = max(0, src_w - crop_w)
    points = [(p.t, min(max(p.center_x - crop_w / 2, 0), max_x)) for p in track]
    if len(points) == 1:
        return f"{points[0][1]:.1f}"

    terms: list[str] = []
    for (t0, x0), (t1, x1) in zip(points, points[1:]):
        span = max(1e-3, t1 - t0)
        slope = (x1 - x0) / span
        terms.append(
            f"gte(t,{t0:.3f})*lt(t,{t1:.3f})*({x0:.1f}+{slope:.3f}*(t-{t0:.3f}))"
        )
    t_last, x_last = points[-1]
    terms.append(f"gte(t,{t_last:.3f})*{x_last:.1f}")
    terms.append(f"lt(t,{points[0][0]:.3f})*{points[0][1]:.1f}")
    return "+".join(terms)


def compute_tracking_path(
    info: MediaInfo,
    start: float,
    end: float,
    sample_fps: float = SAMPLE_FPS,
) -> list[TrackPoint]:
    """Sample the clip and estimate a horizontal centre of interest per sample.

    Returns an empty list if OpenCV is unavailable or nothing usable is found,
    which callers treat as "fall back to centre crop".
    """
    try:
        import cv2
    except ImportError:
        log.warning(
            "opencv not installed; `pip install 'clipper[track]'` to enable track mode"
        )
        return []

    duration = max(0.0, end - start)
    if duration <= 0:
        return []

    height = even(SAMPLE_WIDTH * info.height / info.width)
    raw = run_ffmpeg_pipe(
        [
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(info.path),
            "-an",
            "-vf",
            f"fps={sample_fps},scale={SAMPLE_WIDTH}:{height}",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "-",
        ]
    )
    frame_size = SAMPLE_WIDTH * height
    n_frames = len(raw) // frame_size
    if n_frames == 0:
        return []
    frames = np.frombuffer(raw[: n_frames * frame_size], dtype=np.uint8).reshape(
        n_frames, height, SAMPLE_WIDTH
    )

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    scale_back = info.width / SAMPLE_WIDTH
    centers: list[float] = []
    previous: np.ndarray | None = None

    for frame in frames:
        center = _face_center(cascade, frame)
        if center is None:
            center = _motion_center(previous, frame)
        if center is None:
            center = centers[-1] / scale_back if centers else SAMPLE_WIDTH / 2
        centers.append(center * scale_back)
        previous = frame

    smoothed = _smooth(centers, SMOOTH_WINDOW)
    return [
        TrackPoint(t=i / sample_fps, center_x=float(x)) for i, x in enumerate(smoothed)
    ]


def _face_center(cascade, frame: np.ndarray) -> float | None:
    faces = cascade.detectMultiScale(frame, scaleFactor=1.15, minNeighbors=5, minSize=(24, 24))
    if len(faces) == 0:
        return None
    # Largest face wins — in a two-shot that is usually the active speaker.
    x, _, w, _ = max(faces, key=lambda f: f[2] * f[3])
    return float(x + w / 2)


def _motion_center(previous: np.ndarray | None, frame: np.ndarray) -> float | None:
    if previous is None:
        return None
    diff = np.abs(frame.astype(np.int16) - previous.astype(np.int16)).sum(axis=0)
    total = diff.sum()
    if total < 1e-6:
        return None
    columns = np.arange(diff.size)
    return float((columns * diff).sum() / total)


def _smooth(values: list[float], window: int) -> list[float]:
    if len(values) < 2 or window <= 1:
        return values
    kernel = np.ones(window) / window
    padded = np.pad(np.asarray(values, dtype=np.float64), (window // 2, window // 2), mode="edge")
    return list(np.convolve(padded, kernel, mode="valid")[: len(values)])
