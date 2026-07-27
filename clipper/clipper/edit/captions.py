"""Word-timed caption generation as ASS, burned in later by libass.

ASS rather than SRT because short-form captions need styling libass gives us
for free: heavy outline, exact vertical placement inside the safe area, and
per-word colour changes. The "karaoke" mode emits one event per word — the full
line stays on screen while the active word is recoloured, which is the format
that reads as native on TikTok/Reels.

All timings produced here are relative to the *clip* start, since the renderer
trims the source before applying subtitles.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import CaptionConfig
from ..models import Word

LINE_BREAK_GAP = 0.7
"""A pause this long forces a new caption line even mid-sentence."""


@dataclass
class CaptionLine:
    words: list[Word] = field(default_factory=list)

    @property
    def start(self) -> float:
        return self.words[0].start if self.words else 0.0

    @property
    def end(self) -> float:
        return self.words[-1].end if self.words else 0.0

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def build_caption_lines(words: list[Word], config: CaptionConfig) -> list[CaptionLine]:
    """Group words into short on-screen lines.

    Breaks on whichever comes first: word count, character count, elapsed time,
    a sentence end, or a pause.
    """
    lines: list[CaptionLine] = []
    current: list[Word] = []

    for i, word in enumerate(words):
        candidate_len = len(" ".join(w.text for w in current + [word]))
        too_long = len(current) >= config.max_words_per_line
        too_wide = current and candidate_len > config.max_chars_per_line
        too_slow = current and (word.end - current[0].start) > config.max_line_duration

        if current and (too_long or too_wide or too_slow):
            lines.append(CaptionLine(current))
            current = []

        current.append(word)

        next_word = words[i + 1] if i + 1 < len(words) else None
        gap = (next_word.start - word.end) if next_word else 0.0
        if word.ends_sentence() or (next_word and gap >= LINE_BREAK_GAP):
            lines.append(CaptionLine(current))
            current = []

    if current:
        lines.append(CaptionLine(current))
    return [ln for ln in lines if ln.words]


def render_ass(
    lines: list[CaptionLine],
    config: CaptionConfig,
    width: int,
    height: int,
    time_offset: float = 0.0,
) -> str:
    """Serialize caption lines to an ASS document sized for ``width x height``."""
    header = _header(config, width, height)
    events: list[str] = []

    for line in lines:
        if not line.words:
            continue
        line_start = max(0.0, line.start - time_offset)
        line_end = max(line_start, line.end - time_offset)

        if not config.karaoke:
            events.append(
                _dialogue(line_start, line_end, _escape(_case(line.text, config)))
            )
            continue

        for idx, word in enumerate(line.words):
            start = max(0.0, word.start - time_offset)
            # Hold each state until the next word begins so the line never blinks.
            if idx + 1 < len(line.words):
                end = max(start, line.words[idx + 1].start - time_offset)
            else:
                end = max(start, line_end)
            if end <= start:
                continue
            events.append(_dialogue(start, end, _highlight(line, idx, config)))

    return header + "\n".join(events) + "\n"


def _highlight(line: CaptionLine, active: int, config: CaptionConfig) -> str:
    primary = _ass_color(config.primary_color)
    accent = _ass_color(config.highlight_color)
    parts: list[str] = []
    for i, word in enumerate(line.words):
        text = _escape(_case(word.text, config))
        if i == active:
            parts.append(f"{{\\1c{accent}}}{text}{{\\1c{primary}}}")
        else:
            parts.append(text)
    return " ".join(parts)


def _case(text: str, config: CaptionConfig) -> str:
    return text.upper() if config.uppercase else text


def _escape(text: str) -> str:
    """ASS reserves braces for override blocks and backslash for escapes."""
    return (
        text.replace("\\", "/")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", " ")
        .strip()
    )


def _dialogue(start: float, end: float, text: str) -> str:
    return f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{text}"


def _ts(seconds: float) -> str:
    """ASS timestamps are H:MM:SS.cc (centiseconds, single-digit hour)."""
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR& (ASS stores colour as BGR with a leading alpha)."""
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        raise ValueError(f"expected #RRGGBB colour, got {hex_color!r}")
    r, g, b = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha:02X}{b}{g}{r}&".upper()


def _style_color(hex_color: str, alpha: int = 0) -> str:
    """Style lines take the same value without the trailing ampersand."""
    return _ass_color(hex_color, alpha).rstrip("&")


def _header(config: CaptionConfig, width: int, height: int) -> str:
    bold = -1 if config.bold else 0
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{config.font},{config.font_size},{_style_color(config.primary_color)},{_style_color(config.highlight_color)},{_style_color(config.outline_color)},&H80000000,{bold},0,0,0,100,100,0,0,1,{config.outline},{config.shadow},2,80,80,{config.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
