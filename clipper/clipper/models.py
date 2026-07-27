"""Core domain objects passed between pipeline stages.

Everything here is a plain dataclass with explicit ``to_dict``/``from_dict`` so
transcripts and candidate lists can be cached on disk as JSON. Transcription is
by far the most expensive stage, so caching it is what makes iterating on the
scoring weights bearable.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*$")


@dataclass
class Word:
    """One transcribed word with its timing, in seconds from source start."""

    text: str
    start: float
    end: float
    probability: float = 1.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def ends_sentence(self) -> bool:
        return bool(SENTENCE_END.search(self.text.strip()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Word":
        return cls(
            text=d["text"],
            start=float(d["start"]),
            end=float(d["end"]),
            probability=float(d.get("probability", 1.0)),
        )


@dataclass
class Segment:
    """An ASR segment (roughly an utterance). Words may be empty if the backend
    did not return word-level timings, in which case cut-point snapping falls
    back to segment boundaries."""

    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "words": [w.to_dict() for w in self.words],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Segment":
        return cls(
            start=float(d["start"]),
            end=float(d["end"]),
            text=d["text"],
            words=[Word.from_dict(w) for w in d.get("words", [])],
        )


@dataclass
class Transcript:
    language: str
    duration: float
    segments: list[Segment] = field(default_factory=list)
    source: str = ""
    model: str = ""

    @property
    def words(self) -> list[Word]:
        out: list[Word] = []
        for seg in self.segments:
            if seg.words:
                out.extend(seg.words)
            else:
                # Synthesize evenly spaced words so downstream code has a
                # uniform interface even with a word-timing-less backend.
                tokens = seg.text.split()
                if not tokens:
                    continue
                step = (seg.end - seg.start) / len(tokens)
                for i, tok in enumerate(tokens):
                    out.append(
                        Word(tok, seg.start + i * step, seg.start + (i + 1) * step, 0.5)
                    )
        return out

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()

    def words_between(self, start: float, end: float) -> list[Word]:
        """Words whose midpoint falls inside [start, end)."""
        return [w for w in self.words if start <= (w.start + w.end) / 2 < end]

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "duration": self.duration,
            "source": self.source,
            "model": self.model,
            "segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transcript":
        return cls(
            language=d.get("language", "en"),
            duration=float(d.get("duration", 0.0)),
            source=d.get("source", ""),
            model=d.get("model", ""),
            segments=[Segment.from_dict(s) for s in d.get("segments", [])],
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Transcript":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass
class Candidate:
    """A scored window of the source video that could become a clip."""

    start: float
    end: float
    score: float = 0.0
    features: dict[str, float] = field(default_factory=dict)
    text: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start

    def overlap(self, other: "Candidate") -> float:
        """Intersection-over-union with another candidate, for de-duplication."""
        inter = max(0.0, min(self.end, other.end) - max(self.start, other.start))
        union = max(self.end, other.end) - min(self.start, other.start)
        return inter / union if union > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "score": round(self.score, 4),
            "features": {k: round(v, 4) for k, v in self.features.items()},
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Candidate":
        return cls(
            start=float(d["start"]),
            end=float(d["end"]),
            score=float(d.get("score", 0.0)),
            features=dict(d.get("features", {})),
            text=d.get("text", ""),
        )


@dataclass
class ClipMetadata:
    """Human-review payload written next to each rendered clip. Nothing here is
    posted anywhere; it exists so the operator can copy/paste."""

    title: str
    caption: str
    hashtags: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    transcript: str = ""
    generator: str = "heuristic"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClipResult:
    index: int
    candidate: Candidate
    video_path: Path
    metadata: ClipMetadata
    subtitle_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "video": str(self.video_path),
            "subtitles": str(self.subtitle_path) if self.subtitle_path else None,
            "candidate": self.candidate.to_dict(),
            "metadata": self.metadata.to_dict(),
        }


@dataclass
class MediaInfo:
    """Result of probing the source file."""

    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    video_codec: str = ""
    audio_codec: str = ""

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0

    @property
    def is_vertical(self) -> bool:
        return self.aspect < 1.0


def dedupe_by_overlap(
    candidates: Iterable[Candidate], max_iou: float = 0.25
) -> list[Candidate]:
    """Greedy non-maximum suppression: keep the highest scoring candidate, drop
    anything overlapping it beyond ``max_iou``, repeat.

    Without this the top-N is almost always N near-identical windows sliding one
    sentence at a time over the single best moment in the video.
    """
    ordered = sorted(candidates, key=lambda c: c.score, reverse=True)
    kept: list[Candidate] = []
    for cand in ordered:
        if all(cand.overlap(k) <= max_iou for k in kept):
            kept.append(cand)
    return kept
