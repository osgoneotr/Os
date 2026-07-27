"""Transcription backend interface.

Word-level timestamps are a hard requirement, not a nice-to-have: they drive
both the cut-point snapping (so clips don't start mid-word) and the karaoke
caption highlight. Any backend added here must supply them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from ..config import TranscribeConfig
from ..models import Transcript


class Transcriber(Protocol):
    def transcribe(self, media_path: Path, config: TranscribeConfig) -> Transcript: ...


_REGISTRY: dict[str, Callable[[], Transcriber]] = {}


def register_transcriber(name: str, factory: Callable[[], Transcriber]) -> None:
    _REGISTRY[name] = factory


def get_transcriber(name: str) -> Transcriber:
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown transcription backend '{name}'. Available: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]()


def _load_faster_whisper() -> Transcriber:
    from .faster_whisper_backend import FasterWhisperTranscriber

    return FasterWhisperTranscriber()


def _load_openai() -> Transcriber:
    from .openai_backend import OpenAITranscriber

    return OpenAITranscriber()


def _load_importer() -> Transcriber:
    from .importer import ImportedTranscriber

    return ImportedTranscriber()


register_transcriber("faster-whisper", _load_faster_whisper)
register_transcriber("openai", _load_openai)
register_transcriber("json", _load_importer)
