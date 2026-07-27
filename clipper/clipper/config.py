"""Configuration. Plain dataclasses so a JSON file, CLI flags and defaults all
merge through the same path — ``Config.load(path, **overrides)``.

Scoring weights live here on purpose: tuning them is the main day-to-day knob
once the pipeline runs, and they should be editable without touching code.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


@dataclass
class ScoringConfig:
    min_duration: float = 18.0
    max_duration: float = 58.0
    """Hard bounds on clip length. 58s keeps clips under every platform's 60s
    tier; raise to ~89 for YouTube Shorts' 3-minute allowance if you prefer."""

    target_duration: float = 32.0
    pause_split: float = 0.55
    """A silence this long between words is treated as a possible cut point even
    mid-sentence — this is what stops clips starting on a half-word."""

    # Composite weights. Relative magnitude is what matters, not the sum.
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "hook": 1.6,  # strength of the first ~3 seconds
            "keyword": 1.2,  # hook lexicon + niche terms
            "energy": 1.0,  # audio loudness relative to the whole video
            "dynamics": 0.7,  # variation in loudness (expressive delivery)
            "pacing": 0.8,  # words-per-minute in a natural band
            "density": 0.6,  # low dead-air ratio
            "completeness": 1.1,  # starts/ends on sentence boundaries
            "arc": 0.5,  # question early, payoff later
            "length": 0.4,  # closeness to target_duration
        }
    )
    max_iou: float = 0.25
    top_k: int = 5
    # Terms specific to your niche, added to the generic hook lexicon.
    niche_keywords: list[str] = field(default_factory=list)


@dataclass
class CaptionConfig:
    enabled: bool = True
    font: str = "DejaVu Sans"
    font_size: int = 72
    """Sized against a 1080x1920 canvas (PlayRes in the generated ASS)."""

    primary_color: str = "#FFFFFF"
    highlight_color: str = "#FFE44D"
    outline_color: str = "#000000"
    outline: float = 4.0
    shadow: float = 1.0
    bold: bool = True
    uppercase: bool = False
    max_words_per_line: int = 4
    max_chars_per_line: int = 22
    max_line_duration: float = 2.2
    margin_v: int = 620
    """Distance from the bottom of the 1920px canvas. ~620 puts captions just
    above centre, clear of the TikTok/Reels bottom UI overlay."""

    karaoke: bool = True
    """Highlight the word currently being spoken."""


@dataclass
class RenderConfig:
    width: int = 1080
    height: int = 1920
    fps: int | None = None  # None = keep source fps
    reframe: str = "track"  # center | blur_pad | track
    """Defaults to subject tracking, which is the right mode for talking-head
    and interview footage. Requires the `track` extra; without OpenCV it warns
    and falls back to a centre crop rather than failing the render."""
    crf: int = 20
    preset: str = "medium"
    audio_bitrate: str = "160k"
    loudnorm: bool = True
    """Normalize to -14 LUFS, which is roughly what the platforms target."""

    music_path: str | None = None
    music_gain_db: float = -18.0
    music_duck: bool = True
    """Sidechain-compress the music against the voice track."""

    logo_path: str | None = None
    logo_width_pct: float = 18.0
    logo_margin_pct: float = 4.0
    logo_position: str = "top_right"  # top_left | top_right | bottom_left | bottom_right


@dataclass
class TranscribeConfig:
    backend: str = "faster-whisper"  # faster-whisper | openai | json
    model: str = "small"
    device: str = "auto"
    compute_type: str = "auto"
    language: str | None = None
    vad_filter: bool = True


@dataclass
class OutputConfig:
    directory: str = "out"
    hashtag_count: int = 8
    base_hashtags: list[str] = field(default_factory=list)
    metadata_generator: str = "heuristic"  # heuristic | llm
    write_subtitles: bool = True
    """Also keep the .ass file, so captions can be restyled without re-running
    transcription."""


@dataclass
class Config:
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    captions: CaptionConfig = field(default_factory=CaptionConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    transcribe: TranscribeConfig = field(default_factory=TranscribeConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    cache_dir: str = ".cache"

    @classmethod
    def load(cls, path: str | Path | None = None, **overrides: Any) -> "Config":
        data: dict[str, Any] = {}
        if path:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        cfg = _from_dict(cls, data)
        _apply_overrides(cfg, overrides)
        return cfg

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


def _from_dict(klass: type, data: dict[str, Any]) -> Any:
    """Build a (possibly nested) dataclass from a partial dict, leaving unset
    fields at their defaults.

    Nested sections are detected from the *default instance* rather than the
    field annotation: ``from __future__ import annotations`` makes every
    ``field.type`` a string, so annotation-based detection silently leaves
    nested sections as raw dicts.
    """
    obj = klass()
    for f in fields(klass):
        if f.name not in data:
            continue
        value = data[f.name]
        current = getattr(obj, f.name)
        if is_dataclass(current) and isinstance(value, dict):
            setattr(obj, f.name, _from_dict(type(current), value))
        else:
            setattr(obj, f.name, value)
    return obj


def _apply_overrides(cfg: Any, overrides: dict[str, Any]) -> None:
    """Apply ``dotted.path=value`` overrides, e.g. ``render.reframe``."""
    for key, value in overrides.items():
        if value is None:
            continue
        target = cfg
        parts = key.split(".")
        for part in parts[:-1]:
            target = getattr(target, part)
        if not hasattr(target, parts[-1]):
            raise KeyError(f"unknown config key: {key}")
        setattr(target, parts[-1], value)
