"""Import an existing transcript instead of running ASR.

Two uses: you already have a transcript from the platform/editor, and — more
often — you want to re-run scoring or restyle captions without paying for
transcription again. Accepts our own cached JSON, Whisper's ``verbose_json``,
and SRT/VTT (segment-level timings only, which is enough for scoring but gives
coarser caption highlighting).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import TranscribeConfig
from ..models import Segment, Transcript, Word

_TS = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{1,3})\s*-->\s*"
    r"(?P<h2>\d{1,2}):(?P<m2>\d{2}):(?P<s2>\d{2})[,.](?P<ms2>\d{1,3})"
)


class ImportedTranscriber:
    def transcribe(self, media_path: Path, config: TranscribeConfig) -> Transcript:
        # `model` carries the path to the transcript file for this backend.
        path = Path(config.model)
        if not path.exists():
            raise FileNotFoundError(
                "transcribe.backend='json' expects transcribe.model to be the path "
                f"to a transcript file; got: {path}"
            )
        return load_transcript_file(path, source=str(media_path))


def load_transcript_file(path: Path, source: str = "") -> Transcript:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if "segments" in data and "duration" in data:
            transcript = Transcript.from_dict(data)
        else:
            transcript = _from_whisper_verbose(data)
    else:
        transcript = _from_srt(text)
    if source:
        transcript.source = source
    return transcript


def _from_whisper_verbose(data: dict) -> Transcript:
    words = [
        Word(str(w.get("word", "")).strip(), float(w["start"]), float(w["end"]))
        for w in data.get("words", [])
        if str(w.get("word", "")).strip()
    ]
    segments: list[Segment] = []
    for seg in data.get("segments", []):
        start, end = float(seg["start"]), float(seg["end"])
        segments.append(
            Segment(
                start=start,
                end=end,
                text=str(seg.get("text", "")).strip(),
                words=[w for w in words if start <= (w.start + w.end) / 2 < end],
            )
        )
    duration = float(data.get("duration") or (segments[-1].end if segments else 0.0))
    return Transcript(
        language=data.get("language", "en"),
        duration=duration,
        segments=segments,
        model="imported",
    )


def _from_srt(text: str) -> Transcript:
    segments: list[Segment] = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        m = _TS.search(block)
        if not m:
            continue
        start = _seconds(m.group("h"), m.group("m"), m.group("s"), m.group("ms"))
        end = _seconds(m.group("h2"), m.group("m2"), m.group("s2"), m.group("ms2"))
        lines = [ln for ln in block.splitlines() if not _TS.search(ln)]
        body = " ".join(ln.strip() for ln in lines if not ln.strip().isdigit()).strip()
        body = re.sub(r"<[^>]+>", "", body)
        if body:
            segments.append(Segment(start=start, end=end, text=body))
    duration = segments[-1].end if segments else 0.0
    return Transcript(language="en", duration=duration, segments=segments, model="imported")


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0
