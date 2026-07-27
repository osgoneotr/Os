"""Tests for clip preparation.

These shell out to real ffmpeg. The caption tests exist because this stage
fails *silently*: a bad subtitle style produces a perfectly valid video with
no captions in it and a zero exit code, so only inspecting pixels catches it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tiktok_pipeline.prep import (
    DEFAULT_SAFE_ZONE,
    TARGET_H,
    TARGET_W,
    build_vertical_filter,
    prepare_clip,
    probe,
    srt_to_styled_ass,
    validate_for_tiktok,
)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)

SRT = """1
00:00:00,500 --> 00:00:02,500
first hook line

2
00:00:02,600 --> 00:00:04,500
second line here
"""


@pytest.fixture
def srt_file(tmp_path) -> Path:
    p = tmp_path / "caps.srt"
    p.write_text(SRT)
    return p


@pytest.fixture
def source_16x9(tmp_path) -> Path:
    """A 5s 1920x1080 60fps clip with audio -- the landscape gameplay case."""
    dst = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=60:duration=5",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(dst), "-loglevel", "error",
        ],
        check=True,
    )
    return dst


# Lossy encoding perturbs even untouched regions, so exact frame comparison
# gives false positives. Measured noise floor between two renders of the same
# source is <= 7/255; burned-in text registers ~223. 40 sits well clear of both.
PIXEL_NOISE_FLOOR = 40.0


def _band_delta(a: Path, b: Path, top: int, height: int) -> float:
    """Max per-pixel luma difference between two videos within a band."""
    out = subprocess.run(
        [
            "ffmpeg", "-i", str(a), "-i", str(b), "-t", "3",
            "-filter_complex",
            f"[0:v]crop={TARGET_W}:{height}:0:{top}[x];"
            f"[1:v]crop={TARGET_W}:{height}:0:{top}[y];"
            f"[x][y]blend=all_mode=difference,signalstats,"
            f"metadata=print:key=lavfi.signalstats.YMAX",
            "-f", "null", "-", "-loglevel", "info",
        ],
        capture_output=True, text=True, check=True,
    )
    values = [float(v) for v in re.findall(r"YMAX=(\d+\.?\d*)", out.stderr + out.stdout)]
    assert values, "signalstats produced no readings"
    return max(values)


# ---- ASS generation (the actual bug) --------------------------------

@needs_ffmpeg
def test_generated_ass_uses_real_frame_resolution(srt_file, tmp_path):
    """Regression: ffmpeg defaults SRT->ASS to PlayRes 384x288.

    libass reads FontSize and MarginV in script coordinates, so pixel values
    against a 288-tall script miss by 1920/288 = 6.67x -- captions land far
    off-screen and the render silently contains none.
    """
    ass = srt_to_styled_ass(srt_file, tmp_path / "out.ass")
    text = ass.read_text()

    assert f"PlayResX: {TARGET_W}" in text
    assert f"PlayResY: {TARGET_H}" in text
    assert "PlayResY: 288" not in text


@needs_ffmpeg
def test_generated_ass_style_places_captions_in_safe_zone(srt_file, tmp_path):
    ass = srt_to_styled_ass(srt_file, tmp_path / "out.ass")
    style = next(l for l in ass.read_text().splitlines() if l.startswith("Style: Default,"))
    fields = style.split(",")

    margin_l, margin_r, margin_v = int(fields[-4]), int(fields[-3]), int(fields[-2])
    assert margin_l == margin_r, "asymmetric margins would shift captions off centre"
    assert margin_r >= DEFAULT_SAFE_ZONE.right, "captions must clear the button rail"
    # MarginV is measured from the bottom; the caption anchor must sit above
    # the bottom overlay band.
    assert TARGET_H - margin_v <= DEFAULT_SAFE_ZONE.text_bottom
    assert TARGET_H - margin_v >= DEFAULT_SAFE_ZONE.text_top


@needs_ffmpeg
def test_srt_events_survive_conversion(srt_file, tmp_path):
    ass = srt_to_styled_ass(srt_file, tmp_path / "out.ass")
    text = ass.read_text()
    assert "first hook line" in text
    assert "second line here" in text
    assert text.count("Dialogue:") == 2


# ---- end-to-end render ----------------------------------------------

@needs_ffmpeg
def test_output_meets_tiktok_spec(source_16x9, tmp_path):
    out = prepare_clip(source_16x9, tmp_path / "out.mp4")
    assert validate_for_tiktok(out) == []

    info = probe(out)
    assert (info["width"], info["height"]) == (TARGET_W, TARGET_H)


@needs_ffmpeg
def test_source_frame_rate_preserved_by_default(source_16x9, tmp_path):
    """Regression: a hardcoded -r 30 halved 60fps gameplay footage."""
    out = prepare_clip(source_16x9, tmp_path / "out.mp4")
    assert round(probe(out)["fps"]) == 60


@needs_ffmpeg
def test_explicit_fps_resamples(source_16x9, tmp_path):
    out = prepare_clip(source_16x9, tmp_path / "out30.mp4", fps=30)
    assert round(probe(out)["fps"]) == 30


@needs_ffmpeg
def test_audio_survives_blur_pad_path(source_16x9, tmp_path):
    """Regression: -map 0:a? without an explicit video map dropped the video."""
    out = prepare_clip(source_16x9, tmp_path / "out.mp4", blur_pad=True)
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert "video" in streams and "audio" in streams


@needs_ffmpeg
def test_captions_are_actually_burned_in(source_16x9, srt_file, tmp_path):
    """The one that matters: compare pixels, not exit codes.

    ffmpeg exits 0 whether or not libass drew anything, so the only honest
    check is that the caption band differs from an uncaptioned render.
    """
    plain = prepare_clip(source_16x9, tmp_path / "plain.mp4")
    capped = prepare_clip(source_16x9, tmp_path / "capped.mp4", srt=srt_file)

    delta = _band_delta(
        plain, capped,
        DEFAULT_SAFE_ZONE.text_top,
        DEFAULT_SAFE_ZONE.text_bottom - DEFAULT_SAFE_ZONE.text_top,
    )
    assert delta > PIXEL_NOISE_FLOOR, (
        f"caption band changed by only {delta}/255 -- libass drew nothing"
    )


@needs_ffmpeg
def test_captions_stay_out_of_the_ui_overlay_bands(source_16x9, srt_file, tmp_path):
    """Captions must not bleed into the top bar or bottom caption/sound area."""
    plain = prepare_clip(source_16x9, tmp_path / "plain.mp4")
    capped = prepare_clip(source_16x9, tmp_path / "capped.mp4", srt=srt_file)

    for name, top, height in [
        ("top bar", 0, DEFAULT_SAFE_ZONE.top),
        ("bottom overlay", DEFAULT_SAFE_ZONE.text_bottom, DEFAULT_SAFE_ZONE.bottom),
    ]:
        delta = _band_delta(plain, capped, top, height)
        assert delta <= PIXEL_NOISE_FLOOR, (
            f"captions intruded into the {name} region (delta {delta}/255)"
        )


@needs_ffmpeg
def test_missing_subtitle_file_raises(source_16x9, tmp_path):
    with pytest.raises(FileNotFoundError):
        prepare_clip(source_16x9, tmp_path / "out.mp4", srt=tmp_path / "nope.srt")


@needs_ffmpeg
def test_temp_ass_is_cleaned_up(source_16x9, srt_file, tmp_path):
    out = prepare_clip(source_16x9, tmp_path / "out.mp4", srt=srt_file)
    assert not out.with_suffix(".styled.ass").exists()


# ---- pure filter-graph checks (no ffmpeg needed) --------------------

def test_blur_pad_graph_labels_output_for_explicit_mapping():
    """filter_complex output must be mappable alongside audio."""
    graph = build_vertical_filter(blur_pad=True)
    assert "split=2" in graph and "overlay=" in graph
    assert graph.endswith("setsar=1")


def test_black_bar_graph_is_a_simple_vf_chain():
    graph = build_vertical_filter(blur_pad=False)
    assert "split" not in graph  # must be usable with -vf
    assert f"pad={TARGET_W}:{TARGET_H}" in graph


def test_safe_zone_caption_anchor_is_inside_usable_band():
    sz = DEFAULT_SAFE_ZONE
    assert sz.text_top < sz.caption_y() < sz.text_bottom
    assert sz.usable_height == sz.text_bottom - sz.text_top


@needs_ffmpeg
def test_lossless_crf_rejected_with_clear_message(source_16x9, tmp_path):
    """crf=0 fails deep inside x264 with an opaque profile error; catch it early."""
    with pytest.raises(ValueError, match="crf must be between"):
        prepare_clip(source_16x9, tmp_path / "out.mp4", crf=0)


# ---- batch workflow --------------------------------------------------

@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """cli.main() builds Settings from the environment, so point it at tmp_path."""
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "k")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "s")
    monkeypatch.setenv("TIKTOK_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


@pytest.fixture
def tiny_source(tmp_path):
    """Factory for short clips -- keeps batch tests from dominating runtime."""
    # 4s: TikTok rejects anything under 3s, and validate_for_tiktok enforces it,
    # so a 1s fixture would fail validation rather than test the batch logic.
    def make(name: str, size: str = "640x360", seconds: int = 4) -> Path:
        dst = tmp_path / "raw" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi",
             "-i", f"testsrc2=size={size}:rate=30:duration={seconds}",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dst), "-loglevel", "error"],
            check=True,
        )
        return dst
    return make


@needs_ffmpeg
def test_batch_preps_and_queues_every_clip(cli_env, tiny_source, tmp_path):
    from tiktok_pipeline.cli import main

    tiny_source("a.mp4")
    tiny_source("b.mp4")
    out = tmp_path / "out"

    rc = main([
        "batch", "--account", "main",
        "--src-dir", str(tmp_path / "raw"), "--out-dir", str(out),
    ])
    assert rc == 0
    assert {p.name for p in out.glob("*.mp4")} == {"a.mp4", "b.mp4"}

    from tiktok_pipeline.config import Settings
    from tiktok_pipeline.queue import PostQueue
    jobs = list(PostQueue(Settings(client_key="k", client_secret="s",
                                   state_dir=tmp_path / "state")).list_jobs())
    assert len(jobs) == 2
    assert all(j.mode == "inbox" for j in jobs), "batch must default to the no-audit route"


@needs_ffmpeg
def test_batch_uses_title_sidecar(cli_env, tiny_source, tmp_path):
    from tiktok_pipeline.cli import main
    from tiktok_pipeline.config import Settings
    from tiktok_pipeline.queue import PostQueue

    src = tiny_source("hooky.mp4")
    src.with_suffix(".txt").write_text("  real caption here #niche  ")

    main(["batch", "--account", "main", "--src-dir", str(src.parent),
          "--out-dir", str(tmp_path / "out")])

    job = next(PostQueue(Settings(client_key="k", client_secret="s",
                                  state_dir=tmp_path / "state")).list_jobs())
    assert job.title == "real caption here #niche", "sidecar text must be stripped and used"


@needs_ffmpeg
def test_batch_falls_back_to_filename_for_title(cli_env, tiny_source, tmp_path):
    from tiktok_pipeline.cli import main
    from tiktok_pipeline.config import Settings
    from tiktok_pipeline.queue import PostQueue

    src = tiny_source("no_sidecar.mp4")
    main(["batch", "--account", "main", "--src-dir", str(src.parent),
          "--out-dir", str(tmp_path / "out")])

    job = next(PostQueue(Settings(client_key="k", client_secret="s",
                                  state_dir=tmp_path / "state")).list_jobs())
    assert job.title == "no_sidecar"


@needs_ffmpeg
def test_batch_rerun_is_idempotent(cli_env, tiny_source, tmp_path):
    """Re-running must not re-render or double-queue -- duplicates get posted."""
    from tiktok_pipeline.cli import main
    from tiktok_pipeline.config import Settings
    from tiktok_pipeline.queue import PostQueue

    tiny_source("a.mp4")
    args = ["batch", "--account", "main", "--src-dir", str(tmp_path / "raw"),
            "--out-dir", str(tmp_path / "out")]
    main(args)
    main(args)

    jobs = list(PostQueue(Settings(client_key="k", client_secret="s",
                                   state_dir=tmp_path / "state")).list_jobs())
    assert len(jobs) == 1


@needs_ffmpeg
def test_batch_survives_one_bad_clip(cli_env, tiny_source, tmp_path):
    """A corrupt file must not abort the whole batch."""
    from tiktok_pipeline.cli import main
    from tiktok_pipeline.config import Settings
    from tiktok_pipeline.queue import PostQueue

    tiny_source("good.mp4")
    (tmp_path / "raw" / "broken.mp4").write_bytes(b"this is not a video")

    rc = main(["batch", "--account", "main", "--src-dir", str(tmp_path / "raw"),
               "--out-dir", str(tmp_path / "out")])
    assert rc == 1, "a failure must be reported in the exit code"

    jobs = list(PostQueue(Settings(client_key="k", client_secret="s",
                                   state_dir=tmp_path / "state")).list_jobs())
    assert [Path(j.video_path).name for j in jobs] == ["good.mp4"]


@needs_ffmpeg
def test_batch_picks_up_matching_srt(cli_env, tiny_source, tmp_path):
    from tiktok_pipeline.cli import main

    src = tiny_source("capped.mp4", seconds=4)
    (src.parent / "capped.srt").write_text(
        "1\n00:00:00,200 --> 00:00:02,000\nburned in\n"
    )
    out = tmp_path / "out"
    main(["batch", "--account", "main", "--src-dir", str(src.parent), "--out-dir", str(out)])

    plain = prepare_clip(src, tmp_path / "plain.mp4")
    delta = _band_delta(
        plain, out / "capped.mp4",
        DEFAULT_SAFE_ZONE.text_top,
        DEFAULT_SAFE_ZONE.text_bottom - DEFAULT_SAFE_ZONE.text_top,
    )
    assert delta > PIXEL_NOISE_FLOOR, "sidecar .srt was not burned in"


def test_batch_rejects_missing_directory(cli_env, tmp_path, capsys):
    from tiktok_pipeline.cli import main
    rc = main(["batch", "--account", "main", "--src-dir", str(tmp_path / "nope"),
               "--out-dir", str(tmp_path / "out")])
    assert rc == 1
