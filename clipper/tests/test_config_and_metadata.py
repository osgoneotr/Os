from __future__ import annotations

import json

import pytest

from clipper.config import Config, OutputConfig
from clipper.models import Candidate, Transcript
from clipper.output.metadata import build_metadata, extract_keywords
from clipper.output.writer import slugify
from clipper.transcribe.importer import load_transcript_file


class TestConfig:
    def test_defaults_load_without_a_file(self):
        cfg = Config.load(None)
        assert cfg.render.width == 1080
        assert cfg.render.height == 1920
        assert cfg.scoring.top_k == 5

    def test_partial_file_keeps_other_defaults(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_text(json.dumps({"render": {"reframe": "blur_pad"}}))
        cfg = Config.load(path)
        assert cfg.render.reframe == "blur_pad"
        assert cfg.render.crf == 20  # untouched
        assert cfg.captions.enabled is True

    def test_dotted_overrides_beat_the_file(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_text(json.dumps({"scoring": {"top_k": 9}}))
        cfg = Config.load(path, **{"scoring.top_k": 2})
        assert cfg.scoring.top_k == 2

    def test_none_overrides_are_ignored(self):
        cfg = Config.load(None, **{"scoring.top_k": None})
        assert cfg.scoring.top_k == 5

    def test_unknown_override_key_raises(self):
        with pytest.raises(KeyError):
            Config.load(None, **{"scoring.nonsense": 1})

    def test_round_trips_through_disk(self, tmp_path):
        path = tmp_path / "c.json"
        original = Config()
        original.captions.font_size = 96
        original.save(path)
        assert Config.load(path).captions.font_size == 96


class TestMetadata:
    def test_keywords_skip_stopwords(self):
        text = "the onboarding metric is the thing that actually matters for activation"
        keywords = extract_keywords(text)
        assert "the" not in keywords
        assert "onboarding" in keywords

    def test_keywords_are_empty_for_empty_text(self):
        assert extract_keywords("") == []

    def test_hashtags_lead_with_configured_base_tags(self):
        candidate = Candidate(
            start=0, end=30, text="Here's why activation beats signups for growth teams."
        )
        config = OutputConfig(base_hashtags=["Startup", "#growth"], hashtag_count=5)
        meta = build_metadata(candidate, config)
        assert meta.hashtags[:2] == ["#startup", "#growth"]
        assert len(meta.hashtags) <= 5
        assert all(tag.startswith("#") and tag.islower() for tag in meta.hashtags)

    def test_title_is_the_first_sentence_and_bounded(self):
        candidate = Candidate(
            start=0,
            end=30,
            text="A short opener. " + "then a much longer follow up sentence " * 6,
        )
        meta = build_metadata(candidate, OutputConfig())
        assert meta.title == "A short opener"
        assert len(meta.title) <= 80

    def test_llm_generator_falls_back_when_unavailable(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        candidate = Candidate(start=0, end=30, text="Some clip text about growth.")
        meta = build_metadata(candidate, OutputConfig(metadata_generator="llm"))
        assert meta.generator == "heuristic"  # a finished render is not lost to a copy failure


class TestSlug:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Here's Why!", "here-s-why"),
            ("  spaces  ", "spaces"),
            ("!!!", "clip"),
            ("", "clip"),
        ],
    )
    def test_slugify(self, raw, expected):
        assert slugify(raw) == expected


class TestTranscriptIO:
    def test_transcript_round_trips(self, transcript, tmp_path):
        path = tmp_path / "t.json"
        transcript.save(path)
        loaded = Transcript.load(path)
        assert len(loaded.words) == len(transcript.words)
        assert loaded.words[0].text == transcript.words[0].text

    def test_words_between_uses_midpoints(self, transcript):
        window = transcript.words_between(5.0, 10.0)
        assert window
        for word in window:
            assert 5.0 <= (word.start + word.end) / 2 < 10.0

    def test_srt_import(self, tmp_path):
        path = tmp_path / "subs.srt"
        path.write_text(
            "1\n00:00:01,000 --> 00:00:03,500\nHello there\n\n"
            "2\n00:00:04,000 --> 00:00:06,000\nSecond line\n",
            encoding="utf-8",
        )
        transcript = load_transcript_file(path)
        assert len(transcript.segments) == 2
        assert transcript.segments[0].start == 1.0
        assert transcript.segments[0].end == 3.5
        assert transcript.segments[1].text == "Second line"

    def test_segments_without_word_timings_get_synthetic_ones(self, tmp_path):
        path = tmp_path / "subs.srt"
        path.write_text("1\n00:00:00,000 --> 00:00:03,000\none two three\n", encoding="utf-8")
        transcript = load_transcript_file(path)
        words = transcript.words
        assert [w.text for w in words] == ["one", "two", "three"]
        assert words[0].start == 0.0
        assert words[-1].end == pytest.approx(3.0)
