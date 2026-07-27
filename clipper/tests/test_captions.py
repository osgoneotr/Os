from __future__ import annotations

import re

import pytest

from clipper.config import CaptionConfig
from clipper.edit.captions import (
    CaptionLine,
    _ass_color,
    _ts,
    build_caption_lines,
    render_ass,
)
from clipper.models import Word

from .conftest import words_from_sentence


def test_lines_respect_word_and_char_limits():
    words = words_from_sentence(
        "this is a fairly long run of words that has to be split across lines", 0.0
    )
    config = CaptionConfig(max_words_per_line=4, max_chars_per_line=22, max_line_duration=10)
    lines = build_caption_lines(words, config)
    assert lines
    for line in lines:
        assert len(line.words) <= 4
        assert len(line.text) <= 22 or len(line.words) == 1


def test_line_breaks_at_sentence_end():
    words = words_from_sentence("first thought done.", 0.0) + words_from_sentence(
        "second thought here", 2.0
    )
    lines = build_caption_lines(words, CaptionConfig(max_words_per_line=10, max_chars_per_line=200))
    assert lines[0].text.endswith("done.")


def test_line_breaks_on_long_pause():
    words = [Word("one", 0.0, 0.3), Word("two", 0.3, 0.6), Word("three", 2.0, 2.3)]
    lines = build_caption_lines(words, CaptionConfig(max_words_per_line=10, max_chars_per_line=200))
    assert len(lines) == 2


def test_every_word_survives_line_building():
    words = words_from_sentence(
        "no word should ever be dropped when captions are grouped into lines", 0.0
    )
    lines = build_caption_lines(words, CaptionConfig())
    assert [w.text for line in lines for w in line.words] == [w.text for w in words]


def test_ass_color_is_bgr_with_alpha():
    # #FF0000 is pure red -> ASS stores &H00 0000 FF (alpha, blue, green, red)
    assert _ass_color("#FF0000") == "&H000000FF&"
    assert _ass_color("#00FF00") == "&H0000FF00&"
    assert _ass_color("#123456") == "&H00563412&"


def test_ass_color_rejects_bad_input():
    with pytest.raises(ValueError):
        _ass_color("not-a-colour")


def test_timestamp_format():
    assert _ts(0) == "0:00:00.00"
    assert _ts(75.5) == "0:01:15.50"
    assert _ts(3661.25) == "1:01:01.25"


def test_render_ass_has_required_sections():
    lines = build_caption_lines(words_from_sentence("hello there world", 0.0), CaptionConfig())
    doc = render_ass(lines, CaptionConfig(), 1080, 1920)
    assert "[Script Info]" in doc
    assert "PlayResX: 1080" in doc
    assert "PlayResY: 1920" in doc
    assert "[V4+ Styles]" in doc
    assert "[Events]" in doc
    assert doc.count("Style: Caption") == 1


def test_event_format_matches_field_count_emitted():
    """libass splits Dialogue lines by the Format line's field count and treats
    the remainder as Text. A Format line one field short silently prefixes every
    caption with a stray comma — visible only once burned in."""
    doc = render_ass(
        [CaptionLine([Word("hello", 0.0, 0.5)])], CaptionConfig(karaoke=False), 1080, 1920
    )
    format_line = next(
        ln for ln in doc.splitlines() if ln.startswith("Format:") and "Text" in ln
    )
    declared = len(format_line.split(":", 1)[1].split(","))
    dialogue = next(ln for ln in doc.splitlines() if ln.startswith("Dialogue:"))
    emitted = len(dialogue.split(":", 1)[1].split(",", declared - 1))
    assert declared == 10, "ASS Dialogue format is Layer..MarginV, Effect, Text"
    assert emitted == declared


def test_caption_text_has_no_leading_separator():
    doc = render_ass(
        [CaptionLine([Word("hello", 0.0, 0.5)])], CaptionConfig(karaoke=False), 1080, 1920
    )
    dialogue = next(ln for ln in doc.splitlines() if ln.startswith("Dialogue:"))
    text = dialogue.split(",", 9)[-1]  # 10-field format: text is everything after the 9th comma
    assert text == "hello"


def test_karaoke_emits_one_event_per_word():
    words = words_from_sentence("one two three", 0.0)
    lines = [CaptionLine(words)]
    doc = render_ass(lines, CaptionConfig(karaoke=True), 1080, 1920)
    assert doc.count("Dialogue:") == 3
    # Each event shows the whole line, with exactly one word highlighted.
    for event in [ln for ln in doc.splitlines() if ln.startswith("Dialogue:")]:
        assert event.count("{\\1c") == 2  # highlight on, then back to primary


def test_non_karaoke_emits_one_event_per_line():
    words = words_from_sentence("one two three", 0.0)
    doc = render_ass([CaptionLine(words)], CaptionConfig(karaoke=False), 1080, 1920)
    assert doc.count("Dialogue:") == 1


def test_timestamps_are_rebased_to_clip_start():
    words = words_from_sentence("late in the video", 600.0)
    doc = render_ass([CaptionLine(words)], CaptionConfig(), 1080, 1920, time_offset=600.0)
    starts = re.findall(r"Dialogue: 0,(\d:\d\d:\d\d\.\d\d),", doc)
    assert starts[0] == "0:00:00.00"


def test_events_are_contiguous_so_captions_do_not_blink():
    words = [Word("a", 0.0, 0.2), Word("b", 1.0, 1.2), Word("c", 2.0, 2.2)]
    doc = render_ass([CaptionLine(words)], CaptionConfig(), 1080, 1920)
    times = re.findall(r"Dialogue: 0,(\d:\d\d:\d\d\.\d\d),(\d:\d\d:\d\d\.\d\d),", doc)
    for (_, end), (next_start, _) in zip(times, times[1:]):
        assert end == next_start


def test_braces_in_text_do_not_break_override_blocks():
    doc = render_ass(
        [CaptionLine([Word("{weird}", 0.0, 0.5)])], CaptionConfig(karaoke=False), 1080, 1920
    )
    dialogue = [ln for ln in doc.splitlines() if ln.startswith("Dialogue:")][0]
    assert "{weird}" not in dialogue
    assert "(weird)" in dialogue


def test_uppercase_option():
    doc = render_ass(
        [CaptionLine([Word("shout", 0.0, 0.5)])],
        CaptionConfig(uppercase=True, karaoke=False),
        1080,
        1920,
    )
    assert "SHOUT" in doc
