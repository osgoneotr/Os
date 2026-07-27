"""Local transcription via faster-whisper (CTranslate2 Whisper).

Default choice: it runs offline, costs nothing per minute, and returns word
timings. On CPU the `small` model runs roughly 3-6x faster than realtime; on a
CUDA GPU with `compute_type=float16`, `large-v3` is comfortably realtime.
"""

from __future__ import annotations

from pathlib import Path

from ..config import TranscribeConfig
from ..models import Segment, Transcript, Word


class FasterWhisperTranscriber:
    def transcribe(self, media_path: Path, config: TranscribeConfig) -> Transcript:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "faster-whisper is not installed. Run `pip install 'clipper[whisper]'` "
                "or set transcribe.backend to 'openai' / 'json'."
            ) from exc

        device = config.device
        compute_type = config.compute_type
        if device == "auto":
            device, compute_type = _autodetect(compute_type)

        model = WhisperModel(config.model, device=device, compute_type=compute_type)
        segments_iter, info = model.transcribe(
            str(media_path),
            language=config.language,
            word_timestamps=True,
            vad_filter=config.vad_filter,
        )

        segments: list[Segment] = []
        for seg in segments_iter:
            words = [
                Word(
                    text=w.word.strip(),
                    start=float(w.start),
                    end=float(w.end),
                    probability=float(getattr(w, "probability", 1.0) or 1.0),
                )
                for w in (seg.words or [])
                if w.word and w.word.strip()
            ]
            segments.append(
                Segment(
                    start=float(seg.start),
                    end=float(seg.end),
                    text=seg.text.strip(),
                    words=words,
                )
            )

        return Transcript(
            language=info.language,
            duration=float(info.duration),
            segments=segments,
            source=str(media_path),
            model=f"faster-whisper:{config.model}",
        )


def _autodetect(compute_type: str) -> tuple[str, str]:
    try:
        import torch  # noqa: F401  (only present in CUDA setups)

        if torch.cuda.is_available():
            return "cuda", ("float16" if compute_type == "auto" else compute_type)
    except Exception:
        pass
    return "cpu", ("int8" if compute_type == "auto" else compute_type)
