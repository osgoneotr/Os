"""Hosted transcription via the OpenAI Whisper endpoint.

Useful when the machine running the pipeline can't host a model. Two practical
constraints are handled here: the 25 MB upload limit (we compress to mono Opus
and chunk on time), and the fact that word timestamps must be requested
explicitly via ``timestamp_granularities``.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

from ..config import TranscribeConfig
from ..media.ffmpeg import run_ffmpeg
from ..media.probe import probe
from ..models import Segment, Transcript, Word

CHUNK_SECONDS = 600.0  # 10 min of mono Opus at 24 kbps is ~1.8 MB


class OpenAITranscriber:
    def transcribe(self, media_path: Path, config: TranscribeConfig) -> Transcript:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "openai is not installed. Run `pip install 'clipper[openai]'`."
            ) from exc
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set")

        client = OpenAI()
        info = probe(media_path)
        model = config.model if config.model.startswith("whisper") else "whisper-1"

        segments: list[Segment] = []
        n_chunks = max(1, math.ceil(info.duration / CHUNK_SECONDS))
        language = config.language

        with tempfile.TemporaryDirectory() as tmp:
            for i in range(n_chunks):
                offset = i * CHUNK_SECONDS
                chunk_path = Path(tmp) / f"chunk_{i:03d}.ogg"
                run_ffmpeg(
                    [
                        "-y",
                        "-ss",
                        f"{offset:.3f}",
                        "-t",
                        f"{CHUNK_SECONDS:.3f}",
                        "-i",
                        str(media_path),
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-c:a",
                        "libopus",
                        "-b:a",
                        "24k",
                        str(chunk_path),
                    ]
                )
                if not chunk_path.exists() or chunk_path.stat().st_size == 0:
                    continue
                with chunk_path.open("rb") as fh:
                    response = client.audio.transcriptions.create(
                        model=model,
                        file=fh,
                        response_format="verbose_json",
                        timestamp_granularities=["word", "segment"],
                        language=language,
                    )
                data = _as_dict(response)
                language = language or data.get("language")
                segments.extend(_parse_chunk(data, offset))

        return Transcript(
            language=language or "en",
            duration=info.duration,
            segments=segments,
            source=str(media_path),
            model=f"openai:{model}",
        )


def _as_dict(response: object) -> dict:
    if hasattr(response, "model_dump"):
        return response.model_dump()  # type: ignore[attr-defined]
    if isinstance(response, str):
        return json.loads(response)
    return dict(response)  # type: ignore[arg-type]


def _parse_chunk(data: dict, offset: float) -> list[Segment]:
    words = [
        Word(
            text=str(w.get("word", "")).strip(),
            start=float(w["start"]) + offset,
            end=float(w["end"]) + offset,
        )
        for w in data.get("words", [])
        if str(w.get("word", "")).strip()
    ]
    out: list[Segment] = []
    for seg in data.get("segments", []):
        start = float(seg["start"]) + offset
        end = float(seg["end"]) + offset
        out.append(
            Segment(
                start=start,
                end=end,
                text=str(seg.get("text", "")).strip(),
                words=[w for w in words if start <= (w.start + w.end) / 2 < end],
            )
        )
    if not out and words:
        # Some responses omit segments; fall back to one segment for the chunk.
        out.append(
            Segment(
                start=words[0].start,
                end=words[-1].end,
                text=" ".join(w.text for w in words),
                words=words,
            )
        )
    return out
