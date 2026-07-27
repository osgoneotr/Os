from __future__ import annotations

import numpy as np
import pytest

from clipper.config import ScoringConfig
from clipper.media.audio import rms_envelope
from clipper.models import Candidate, Word, dedupe_by_overlap
from clipper.score import features as F
from clipper.score.scorer import build_units, find_clips, generate_candidates

from .conftest import words_from_sentence


def test_units_break_on_sentence_ends(transcript):
    units = build_units(transcript, pause_split=0.55)
    assert len(units) == len(transcript.segments)
    assert all(u.ends_sentence for u in units)


def test_units_break_on_long_pause_without_punctuation():
    words = [
        Word("one", 0.0, 0.3),
        Word("two", 0.3, 0.6),
        Word("three", 2.0, 2.3),  # 1.4s gap, no punctuation
    ]
    transcript = type(
        "T", (), {"words": words}
    )()  # build_units only needs .words
    units = build_units(transcript, pause_split=0.55)
    assert len(units) == 2
    assert units[0].text == "one two"


def test_candidates_respect_duration_bounds(transcript):
    config = ScoringConfig(min_duration=8.0, max_duration=20.0)
    units = build_units(transcript, config.pause_split)
    candidates = generate_candidates(units, config)
    assert candidates
    for cand in candidates:
        assert 8.0 <= cand.duration <= 20.0


def test_candidates_start_and_end_on_unit_boundaries(transcript):
    config = ScoringConfig(min_duration=8.0, max_duration=25.0)
    units = build_units(transcript, config.pause_split)
    starts = {round(u.start, 3) for u in units}
    ends = {round(u.end, 3) for u in units}
    for cand in generate_candidates(units, config):
        assert round(cand.start, 3) in starts
        assert round(cand.end, 3) in ends


def test_best_clip_is_the_hook_section(transcript):
    config = ScoringConfig(min_duration=10.0, max_duration=30.0, top_k=1)
    clips = find_clips(transcript, None, config)
    assert clips
    best = clips[0].text.lower()
    assert "here's why" in best
    assert "40 percent" in best


def test_dedupe_drops_overlapping_windows():
    a = Candidate(start=0, end=30, score=0.9)
    b = Candidate(start=1, end=31, score=0.8)  # ~93% IoU with a
    c = Candidate(start=60, end=90, score=0.7)
    kept = dedupe_by_overlap([a, b, c], max_iou=0.25)
    assert [k.start for k in kept] == [0, 60]


def test_top_k_results_are_distinct_moments(transcript):
    config = ScoringConfig(min_duration=8.0, max_duration=25.0, top_k=3, max_iou=0.25)
    clips = find_clips(transcript, None, config)
    for i, first in enumerate(clips):
        for second in clips[i + 1 :]:
            assert first.overlap(second) <= 0.25


class TestFeatures:
    def test_hook_rewards_curiosity_phrase(self):
        strong = words_from_sentence("Here's why most people get this completely wrong", 0.0)
        weak = words_from_sentence("So anyway it was a pretty normal sort of day", 0.0)
        assert F.hook_feature(strong, 0.0) > F.hook_feature(weak, 0.0)

    def test_hook_only_reads_the_opening(self):
        """A hook phrase 30 seconds in should not rescue a flat opening."""
        late = words_from_sentence("nothing much happened at all really", 0.0)
        late += words_from_sentence("here's why most people get this wrong", 30.0)
        early = words_from_sentence("here's why most people get this wrong", 0.0)
        assert F.hook_feature(early, 0.0) > F.hook_feature(late, 0.0)

    def test_pacing_prefers_natural_speed(self):
        # 45 words over 15s = 180 wpm, inside the ideal band.
        natural = [Word("w", i / 3, i / 3 + 0.3) for i in range(45)]
        # 10 words over 15s = 40 wpm, below the floor.
        slow = [Word("w", i * 1.5, i * 1.5 + 0.3) for i in range(10)]
        assert F.pacing_feature(natural, 15.0) == pytest.approx(1.0)
        assert F.pacing_feature(slow, 15.0) == 0.0

    def test_completeness_penalizes_mid_sentence_end(self):
        clean = F.completeness_feature(True, True, lead_gap=1.0, trail_gap=1.0)
        broken = F.completeness_feature(False, False, lead_gap=0.0, trail_gap=0.0)
        assert clean == pytest.approx(1.0)
        assert broken == 0.0

    def test_energy_is_relative_to_the_whole_video(self):
        sample_rate = 16000
        quiet = np.full(sample_rate * 10, 0.02, dtype=np.float32)
        loud = np.full(sample_rate * 10, 0.4, dtype=np.float32)
        envelope = rms_envelope(np.concatenate([quiet, loud]), sample_rate)
        assert F.energy_feature(envelope, 10.0, 20.0) > F.energy_feature(envelope, 0.0, 10.0)

    def test_silence_ratio_detects_dead_air(self):
        sample_rate = 16000
        speech = np.full(sample_rate * 5, 0.3, dtype=np.float32)
        silence = np.zeros(sample_rate * 5, dtype=np.float32)
        envelope = rms_envelope(np.concatenate([speech, silence]), sample_rate)
        assert F.density_feature(envelope, 0.0, 5.0) > 0.9
        assert F.density_feature(envelope, 5.0, 10.0) < 0.1

    def test_arc_rewards_question_then_payoff(self):
        words = words_from_sentence("why does this keep happening to everyone", 0.0)
        words += words_from_sentence("because the metric measures intent not value", 5.0)
        flat = words_from_sentence("we talked about several unrelated topics today", 0.0)
        flat += words_from_sentence("and then the meeting simply ended early", 5.0)
        assert F.arc_feature(words, 0.0, 10.0) > F.arc_feature(flat, 0.0, 10.0)

    def test_length_feature_peaks_at_target(self):
        assert F.length_feature(30.0, 30.0) == pytest.approx(1.0)
        assert F.length_feature(35.0, 30.0) == pytest.approx(1.0)  # inside the ±40% band
        assert F.length_feature(90.0, 30.0) < 0.5
