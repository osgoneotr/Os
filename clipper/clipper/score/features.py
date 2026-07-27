"""Per-window feature extraction.

Every function returns a value in roughly [0, 1] so the composite score is a
plain weighted mean and the weights in ``ScoringConfig`` are directly
comparable. Acoustic features are expressed relative to the whole video, so a
quietly-mixed podcast and a loud one score the same way.
"""

from __future__ import annotations

from ..media.audio import AudioEnvelope
from ..models import Word
from .keywords import (
    NUMBER_RE,
    RESOLUTION_MARKERS,
    SECOND_PERSON,
    phrase_score,
    token_score,
    tokenize,
)

HOOK_WINDOW = 3.0
"""Seconds at the head of a clip that decide whether anyone watches the rest."""


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def ratio_to_unit(value: float, midpoint: float = 1.0) -> float:
    """Map a non-negative ratio to [0, 1) with 0.5 at ``midpoint``.

    Used for "relative to the rest of the video" features, where the interesting
    signal is 'above average' but a 10x outlier shouldn't dominate the sum.
    """
    if value <= 0:
        return 0.0
    return value / (value + midpoint)


def triangular(value: float, low: float, ideal_low: float, ideal_high: float, high: float) -> float:
    """1.0 inside [ideal_low, ideal_high], falling linearly to 0 at low/high."""
    if value <= low or value >= high:
        return 0.0
    if ideal_low <= value <= ideal_high:
        return 1.0
    if value < ideal_low:
        return (value - low) / max(1e-6, ideal_low - low)
    return (high - value) / max(1e-6, high - ideal_high)


def hook_feature(words: list[Word], start: float, niche: dict[str, float] | None = None) -> float:
    """How strong the opening line is: curiosity phrases, direct address,
    a concrete number, or a question."""
    head = [w for w in words if w.start < start + HOOK_WINDOW]
    if not head:
        return 0.0
    text = " ".join(w.text for w in head)
    tokens = tokenize(text)
    if not tokens:
        return 0.0

    score = 0.0
    score += clamp(phrase_score(text) / 1.5) * 0.45
    score += clamp(token_score(tokens, niche) / 1.5) * 0.2
    if any(t in SECOND_PERSON for t in tokens):
        score += 0.15
    if NUMBER_RE.search(text):
        score += 0.1
    if "?" in text:
        score += 0.1
    return clamp(score)


def keyword_feature(words: list[Word], niche: dict[str, float] | None = None) -> float:
    """Lexicon density over the whole window, per 100 words so long windows
    aren't automatically favoured."""
    tokens = tokenize(" ".join(w.text for w in words))
    if not tokens:
        return 0.0
    raw = phrase_score(" ".join(tokens)) + token_score(tokens, niche)
    per_hundred = raw * 100.0 / len(tokens)
    return ratio_to_unit(per_hundred, midpoint=6.0)


def energy_feature(envelope: AudioEnvelope, start: float, end: float) -> float:
    """Window loudness relative to the video's mean loudness."""
    mean, _, _ = envelope.window_stats(start, end)
    if envelope.mean <= 0:
        return 0.5
    return ratio_to_unit(mean / envelope.mean, midpoint=1.0)


def dynamics_feature(envelope: AudioEnvelope, start: float, end: float) -> float:
    """Loudness variation relative to the video's own variation. Flat delivery
    scores low; emphasis, laughter and pauses-for-effect score high."""
    _, std, _ = envelope.window_stats(start, end)
    if envelope.std <= 0:
        return 0.5
    return ratio_to_unit(std / envelope.std, midpoint=1.0)


def pacing_feature(words: list[Word], duration: float) -> float:
    """Words per minute, scored against a natural speaking band."""
    if duration <= 0 or not words:
        return 0.0
    wpm = len(words) * 60.0 / duration
    return triangular(wpm, low=70, ideal_low=140, ideal_high=210, high=300)


def density_feature(envelope: AudioEnvelope, start: float, end: float) -> float:
    """1 - dead-air ratio. Penalizes segments that are mostly silence."""
    return clamp(1.0 - envelope.silence_ratio(start, end))


def completeness_feature(
    starts_sentence: bool, ends_sentence: bool, lead_gap: float, trail_gap: float
) -> float:
    """Whether the window begins and ends on a real boundary. Weighted toward
    the ending: a clip that stops mid-sentence reads as broken, while one that
    starts mid-thought can still work as an in-media-res hook."""
    score = 0.0
    score += 0.35 if starts_sentence else clamp(lead_gap / 0.8) * 0.2
    score += 0.5 if ends_sentence else clamp(trail_gap / 0.8) * 0.25
    score += 0.15 if (starts_sentence and ends_sentence) else 0.0
    return clamp(score)


def arc_feature(words: list[Word], start: float, end: float) -> float:
    """Setup-then-payoff shape: a question or tension marker early, a resolution
    marker later."""
    if not words or end <= start:
        return 0.0
    midpoint = start + (end - start) * 0.45
    head = " ".join(w.text for w in words if w.end <= midpoint).lower()
    tail = " ".join(w.text for w in words if w.start > midpoint).lower()
    if not head or not tail:
        return 0.0

    score = 0.0
    if "?" in head or any(head.startswith(q) for q in ("what", "why", "how", "when")):
        score += 0.4
    if phrase_score(head) > 0:
        score += 0.2
    if any(marker in tail for marker in RESOLUTION_MARKERS):
        score += 0.3
    if NUMBER_RE.search(tail):
        score += 0.1
    return clamp(score)


def length_feature(duration: float, target: float) -> float:
    """Soft preference for the target length; ±40% of target stays near 1.0."""
    if target <= 0:
        return 1.0
    deviation = abs(duration - target) / target
    return clamp(1.0 - max(0.0, deviation - 0.4) / 0.9)
