from __future__ import annotations

from pathlib import Path

import pytest

from clipper.config import RenderConfig
from clipper.edit.reframe import TrackPoint, _x_expression, crop_size, reframe_graph
from clipper.edit.render import build_render_plan
from clipper.media.ffmpeg import escape_filter_path
from clipper.models import MediaInfo


def info(width: int = 1920, height: int = 1080, has_audio: bool = True) -> MediaInfo:
    return MediaInfo(
        path=Path("/tmp/source.mp4"),
        duration=600.0,
        width=width,
        height=height,
        fps=30.0,
        has_audio=has_audio,
    )


class TestReframe:
    def test_landscape_crop_is_9_by_16(self):
        w, h = crop_size(info(1920, 1080), 1080, 1920)
        assert h == 1080
        assert w == 608  # 1080 * 9/16, rounded to even
        assert abs(w / h - 9 / 16) < 0.01

    def test_portrait_source_crops_height_not_width(self):
        w, h = crop_size(info(1080, 1920), 1080, 1920)
        assert w == 1080
        assert h == 1920

    def test_crop_never_exceeds_source(self):
        for width, height in [(640, 480), (3840, 2160), (720, 1280), (1000, 1000)]:
            w, h = crop_size(info(width, height), 1080, 1920)
            assert w <= width and h <= height

    def test_center_mode_is_a_linear_chain(self):
        graph = reframe_graph("center", info(), 1080, 1920)
        assert graph.startswith("[0:v]crop=")
        assert graph.endswith("[vref]")
        assert "scale=1080:1920" in graph

    def test_blur_pad_keeps_the_whole_frame(self):
        graph = reframe_graph("blur_pad", info(), 1080, 1920)
        assert "gblur" in graph
        assert "force_original_aspect_ratio=decrease" in graph  # foreground fits, never crops
        assert graph.endswith("[vref]")

    def test_track_without_a_path_falls_back_to_center(self):
        graph = reframe_graph("track", info(), 1080, 1920, track=[])
        assert "x='" not in graph

    def test_track_expression_is_flat_not_nested(self):
        track = [TrackPoint(t=i * 0.5, center_x=500 + i * 20) for i in range(6)]
        expr = _x_expression(track, crop_w=608, src_w=1920)
        assert "if(" not in expr  # flat sum, no nesting depth to blow up
        assert expr.count("gte(t,") == len(track)  # one ramp per interval + tail

    def test_track_expression_stays_inside_the_frame(self):
        # Subject at the far right edge; crop must not run past the source width.
        track = [TrackPoint(t=0.0, center_x=1900.0), TrackPoint(t=1.0, center_x=1920.0)]
        expr = _x_expression(track, crop_w=608, src_w=1920)
        max_x = 1920 - 608
        for value in _numbers_before_multiplication(expr):
            assert value <= max_x + 0.5

    def test_track_expression_clamps_at_zero(self):
        track = [TrackPoint(t=0.0, center_x=10.0), TrackPoint(t=1.0, center_x=0.0)]
        expr = _x_expression(track, crop_w=608, src_w=1920)
        assert "-" not in expr.replace("(t-", "")  # no negative x offsets


def _numbers_before_multiplication(expr: str) -> list[float]:
    import re

    return [float(m) for m in re.findall(r"\*\(?(\d+\.\d)", expr)]


class TestRenderPlan:
    def test_trim_happens_on_the_input_side(self):
        plan = build_render_plan(info(), 10.0, 40.0, Path("out.mp4"), RenderConfig())
        args = plan.args
        # -ss and -t must precede -i, or ffmpeg decodes from zero.
        assert args.index("-ss") < args.index("-i")
        assert args.index("-t") < args.index("-i")
        assert args[args.index("-t") + 1] == "30.000"

    def test_output_is_platform_ready(self):
        plan = build_render_plan(info(), 0.0, 30.0, Path("out.mp4"), RenderConfig())
        joined = " ".join(plan.args)
        assert "-pix_fmt yuv420p" in joined
        assert "-movflags +faststart" in joined
        assert "libx264" in joined

    def test_captions_burn_after_scaling(self):
        plan = build_render_plan(
            info(), 0.0, 30.0, Path("out.mp4"), RenderConfig(), ass_path=Path("subs.ass")
        )
        graph = plan.filter_complex
        assert graph.index("scale=1080:1920") < graph.index("subtitles=")

    def test_logo_overlays_on_top_of_captions(self):
        config = RenderConfig(logo_path="logo.png")
        plan = build_render_plan(
            info(), 0.0, 30.0, Path("out.mp4"), config, ass_path=Path("subs.ass")
        )
        graph = plan.filter_complex
        assert graph.index("subtitles=") < graph.index("overlay=")
        assert plan.args[plan.args.index("-map") + 1] == "[vout]"

    def test_silent_source_gets_a_generated_track(self):
        plan = build_render_plan(info(has_audio=False), 0.0, 30.0, Path("out.mp4"), RenderConfig())
        assert "anullsrc" in " ".join(plan.args)
        assert "[1:a]" in plan.filter_complex

    def test_music_is_ducked_against_the_voice(self):
        config = RenderConfig(music_path="bed.mp3", music_duck=True)
        plan = build_render_plan(info(), 0.0, 30.0, Path("out.mp4"), config)
        assert "sidechaincompress" in plan.filter_complex
        assert "asplit" in plan.filter_complex  # voice used as both mix and key
        assert "-stream_loop" in plan.args  # short beds loop instead of ending early

    def test_music_mix_does_not_halve_the_voice(self):
        config = RenderConfig(music_path="bed.mp3", music_duck=False)
        plan = build_render_plan(info(), 0.0, 30.0, Path("out.mp4"), config)
        assert "normalize=0" in plan.filter_complex

    def test_loudness_normalization_can_be_disabled(self):
        plan = build_render_plan(
            info(), 0.0, 30.0, Path("out.mp4"), RenderConfig(loudnorm=False)
        )
        assert "loudnorm" not in plan.filter_complex

    def test_input_indices_stay_aligned_with_multiple_extra_inputs(self):
        config = RenderConfig(music_path="bed.mp3", logo_path="logo.png")
        plan = build_render_plan(
            info(has_audio=False), 0.0, 30.0, Path("out.mp4"), config
        )
        # 0=video, 1=silence, 2=music, 3=logo
        assert "[1:a]" in plan.filter_complex
        assert "[2:a]" in plan.filter_complex
        assert "[3:v]" in plan.filter_complex


class TestFilterEscaping:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("/tmp/a.ass", "/tmp/a.ass"),
            ("C:/x/a.ass", r"C\:/x/a.ass"),
            ("/tmp/my subs [1].ass", r"/tmp/my subs \[1\].ass"),
            ("/tmp/a,b.ass", r"/tmp/a\,b.ass"),
        ],
    )
    def test_paths_are_escaped_for_libavfilter(self, raw, expected):
        assert escape_filter_path(raw) == expected
