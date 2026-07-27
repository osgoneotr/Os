"""End-to-end runs against a real ffmpeg and a real generated video.

These are the tests that catch the failures unit tests cannot: a filter graph
that parses in isolation but not in context, an ASS file libass rejects, a
subtitle path that breaks the graph on escaping.
"""

from __future__ import annotations

import json

import pytest

from clipper.config import Config
from clipper.media.audio import envelope_for
from clipper.media.probe import probe
from clipper.pipeline import run

from .conftest import needs_ffmpeg

pytestmark = needs_ffmpeg


@pytest.fixture
def config(tmp_path, transcript) -> Config:
    """Score against the fixture transcript so the test doesn't need Whisper."""
    transcript_path = tmp_path / "transcript.json"
    transcript.save(transcript_path)
    cfg = Config()
    cfg.transcribe.backend = "json"
    cfg.transcribe.model = str(transcript_path)
    cfg.scoring.min_duration = 8.0
    cfg.scoring.max_duration = 20.0
    cfg.scoring.top_k = 2
    cfg.render.preset = "ultrafast"
    cfg.render.crf = 30
    cfg.output.directory = str(tmp_path / "out")
    cfg.cache_dir = str(tmp_path / "cache")
    return cfg


@needs_ffmpeg
def test_probe_reads_real_media(sample_video):
    info = probe(sample_video)
    assert info.width == 1280
    assert info.height == 720
    assert info.has_audio
    assert info.duration == pytest.approx(40.0, abs=1.0)


@needs_ffmpeg
def test_audio_envelope_from_real_media(sample_video):
    envelope = envelope_for(sample_video)
    assert envelope.rms.size > 0
    assert envelope.mean > 0  # the generated tone is audible


@needs_ffmpeg
def test_full_pipeline_produces_playable_clips(sample_video, config):
    result = run(sample_video, config)

    assert result.clips, "expected at least one clip"
    for clip in result.clips:
        assert clip.video_path.exists()
        assert clip.video_path.stat().st_size > 1000

        rendered = probe(clip.video_path)
        assert (rendered.width, rendered.height) == (1080, 1920)
        assert rendered.has_audio
        assert rendered.duration == pytest.approx(clip.candidate.duration, abs=1.5)


@needs_ffmpeg
def test_pipeline_writes_review_sidecars(sample_video, config):
    result = run(sample_video, config)
    out = result.output_dir

    assert (out / "index.md").exists()
    for clip in result.clips:
        stem = clip.video_path.stem
        assert (out / f"{stem}.md").exists()

        sidecar = json.loads((out / f"{stem}.json").read_text())
        assert sidecar["candidate"]["score"] > 0
        assert sidecar["metadata"]["hashtags"]
        assert sidecar["metadata"]["transcript"]


@needs_ffmpeg
def test_burned_captions_survive_a_path_with_spaces(sample_video, config, tmp_path):
    """libavfilter treats ':' and '[' as syntax — a directory with awkward
    characters is the classic way the subtitles filter breaks."""
    config.output.directory = str(tmp_path / "out dir [test]")
    result = run(sample_video, config)
    assert result.clips
    assert result.clips[0].video_path.exists()


@needs_ffmpeg
@pytest.mark.parametrize("mode", ["center", "blur_pad"])
def test_reframe_modes_render(sample_video, config, mode):
    config.render.reframe = mode
    config.scoring.top_k = 1
    result = run(sample_video, config)
    assert result.clips
    assert probe(result.clips[0].video_path).width == 1080


@needs_ffmpeg
def test_track_mode_degrades_to_center_without_opencv(sample_video, config):
    config.render.reframe = "track"
    config.scoring.top_k = 1
    result = run(sample_video, config)  # must not raise even if cv2 is absent
    assert result.clips
    assert probe(result.clips[0].video_path).height == 1920


@needs_ffmpeg
def test_music_bed_is_mixed_in(sample_video, config, tmp_path):
    from clipper.media.ffmpeg import run_ffmpeg

    music = tmp_path / "bed.m4a"
    run_ffmpeg(
        ["-y", "-f", "lavfi", "-i", "sine=frequency=880", "-t", "5", str(music)]
    )
    config.render.music_path = str(music)
    config.scoring.top_k = 1
    result = run(sample_video, config)  # 5s bed under a ~15s clip must loop, not truncate
    assert result.clips
    rendered = probe(result.clips[0].video_path)
    assert rendered.duration == pytest.approx(result.clips[0].candidate.duration, abs=1.5)


@needs_ffmpeg
def test_transcription_is_cached_between_runs(sample_video, config):
    run(sample_video, config)
    cache_files = list((config.cache_dir and __import__("pathlib").Path(config.cache_dir)).rglob("*.json"))
    assert cache_files, "expected a cached transcript"

    # Break the source transcript; a second run must still work from cache.
    __import__("pathlib").Path(config.transcribe.model).unlink()
    result = run(sample_video, config)
    assert result.clips


@needs_ffmpeg
def test_dry_run_skips_encoding(sample_video, config):
    result = run(sample_video, config, dry_run=True)
    assert result.clips
    assert not result.clips[0].video_path.exists()
    # Sidecars are still written, so you can review the plan.
    assert (result.output_dir / "index.md").exists()
