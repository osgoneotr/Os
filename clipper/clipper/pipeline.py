"""Orchestration: source video in, ranked vertical clips out.

Stage order and why:

    probe -> audio envelope -> transcript -> score -> [per clip] captions ->
    tracking -> render -> metadata -> sidecars

Transcription is cached against the source file's identity, because it is the
only expensive stage that does not change when you re-tune scoring weights or
caption styling — and re-tuning is most of the work in practice.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .edit.captions import build_caption_lines, render_ass
from .edit.reframe import TrackPoint, compute_tracking_path
from .edit.render import render_clip
from .media.audio import AudioEnvelope, envelope_for
from .media.probe import probe
from .models import Candidate, ClipResult, MediaInfo, Transcript
from .output.metadata import build_metadata
from .output.writer import clip_basename, write_clip_outputs, write_index
from .score.scorer import find_clips, pad_to_bounds
from .transcribe.base import get_transcriber

log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    info: MediaInfo
    transcript: Transcript
    candidates: list[Candidate]
    clips: list[ClipResult]
    output_dir: Path


def analyze(source: Path, config: Config) -> tuple[MediaInfo, Transcript, AudioEnvelope | None, list[Candidate]]:
    """Everything up to (but not including) rendering.

    Split out so `clipper score` can show what would be cut without spending
    minutes on encodes.
    """
    info = probe(source)
    log.info(
        "source: %dx%d @ %.2ffps, %.1fs, audio=%s",
        info.width,
        info.height,
        info.fps,
        info.duration,
        info.has_audio,
    )

    envelope = envelope_for(source) if info.has_audio else None
    if envelope is None:
        log.warning("no audio stream; acoustic features will be neutral")

    transcript = _transcribe_cached(source, config)
    log.info("transcript: %d segments, %d words", len(transcript.segments), len(transcript.words))

    candidates = find_clips(transcript, envelope, config.scoring)
    log.info("selected %d candidate(s)", len(candidates))
    return info, transcript, envelope, candidates


def run(source: Path, config: Config, dry_run: bool = False) -> PipelineResult:
    info, transcript, _, candidates = analyze(source, config)

    output_dir = Path(config.output.directory) / source.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(config.cache_dir) / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    results: list[ClipResult] = []
    for index, candidate in enumerate(candidates, start=1):
        start, end = pad_to_bounds(candidate, info.duration)
        metadata = build_metadata(candidate, config.output, config.scoring.niche_keywords)
        stem = clip_basename(index, metadata.title or f"clip-{index}")
        video_path = output_dir / f"{stem}.mp4"

        ass_path: Path | None = None
        if config.captions.enabled:
            ass_path = _write_subtitles(
                transcript, start, end, config, output_dir if config.output.write_subtitles else work_dir, stem
            )

        track: list[TrackPoint] = []
        if config.render.reframe == "track":
            track = compute_tracking_path(info, start, end)

        log.info(
            "clip %d/%d  %.1fs-%.1fs  score=%.3f  -> %s",
            index,
            len(candidates),
            start,
            end,
            candidate.score,
            video_path.name,
        )
        render_clip(info, start, end, video_path, config.render, ass_path, track, dry_run=dry_run)

        result = ClipResult(
            index=index,
            candidate=candidate,
            video_path=video_path,
            metadata=metadata,
            subtitle_path=ass_path if config.output.write_subtitles else None,
        )
        write_clip_outputs(result, output_dir)
        results.append(result)

    if results:
        write_index(results, output_dir, source)
    return PipelineResult(info, transcript, candidates, results, output_dir)


def _write_subtitles(
    transcript: Transcript,
    start: float,
    end: float,
    config: Config,
    directory: Path,
    stem: str,
) -> Path:
    words = transcript.words_between(start, end)
    lines = build_caption_lines(words, config.captions)
    ass = render_ass(lines, config.captions, config.render.width, config.render.height, start)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.ass"
    path.write_text(ass, encoding="utf-8")
    return path


def _transcribe_cached(source: Path, config: Config) -> Transcript:
    cache_dir = Path(config.cache_dir) / "transcripts"
    cache_path = cache_dir / f"{_source_key(source, config)}.json"
    if cache_path.exists():
        log.info("using cached transcript %s", cache_path)
        return Transcript.load(cache_path)

    log.info("transcribing with %s (%s)", config.transcribe.backend, config.transcribe.model)
    transcriber = get_transcriber(config.transcribe.backend)
    transcript = transcriber.transcribe(source, config.transcribe)
    transcript.save(cache_path)
    return transcript


def _source_key(source: Path, config: Config) -> str:
    """Identity of (this file, this ASR config). Size+mtime beats hashing the
    bytes of a multi-gigabyte video for something re-run this often."""
    stat = source.stat()
    raw = "|".join(
        [
            str(source.resolve()),
            str(stat.st_size),
            str(int(stat.st_mtime)),
            config.transcribe.backend,
            config.transcribe.model,
            config.transcribe.language or "auto",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
