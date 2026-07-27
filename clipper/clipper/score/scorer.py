"""Candidate generation and ranking.

The approach: chop the transcript into *units* at sentence ends and long pauses,
then consider every run of consecutive units whose total length fits the clip
bounds. Because candidates only ever start and end at unit boundaries, no clip
can start mid-word — a failure mode that plagues fixed-stride sliding windows.
Ranked candidates are then de-duplicated by overlap so the top-N are genuinely
different moments.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import ScoringConfig
from ..media.audio import AudioEnvelope
from ..models import Candidate, Transcript, Word, dedupe_by_overlap
from . import features as F


@dataclass
class Unit:
    """A sentence-ish chunk: the atom candidates are built from."""

    start: float
    end: float
    words: list[Word] = field(default_factory=list)
    ends_sentence: bool = False
    gap_after: float = 0.0
    gap_before: float = 0.0

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def duration(self) -> float:
        return self.end - self.start


def build_units(transcript: Transcript, pause_split: float = 0.55) -> list[Unit]:
    words = sorted(transcript.words, key=lambda w: w.start)
    units: list[Unit] = []
    current: list[Word] = []

    for i, word in enumerate(words):
        current.append(word)
        next_word = words[i + 1] if i + 1 < len(words) else None
        gap = (next_word.start - word.end) if next_word else float("inf")
        boundary = word.ends_sentence() or gap >= pause_split or next_word is None
        if boundary:
            units.append(
                Unit(
                    start=current[0].start,
                    end=current[-1].end,
                    words=list(current),
                    ends_sentence=word.ends_sentence(),
                    gap_after=0.0 if next_word is None else max(0.0, gap),
                )
            )
            current = []

    for i, unit in enumerate(units):
        unit.gap_before = units[i - 1].gap_after if i > 0 else float("inf")
    return units


def generate_candidates(units: list[Unit], config: ScoringConfig) -> list[Candidate]:
    """Every contiguous run of units within the duration bounds."""
    candidates: list[Candidate] = []
    for i in range(len(units)):
        for j in range(i, len(units)):
            start = units[i].start
            end = units[j].end
            duration = end - start
            if duration > config.max_duration:
                break
            if duration < config.min_duration:
                continue
            candidates.append(Candidate(start=start, end=end))
    return candidates


def score_candidates(
    candidates: list[Candidate],
    transcript: Transcript,
    units: list[Unit],
    envelope: AudioEnvelope | None,
    config: ScoringConfig,
) -> list[Candidate]:
    niche = {kw.lower(): 0.8 for kw in config.niche_keywords}
    weights = config.weights
    weight_sum = sum(weights.values()) or 1.0

    # A unit opens a sentence if it is the first, or the unit before it ended a
    # sentence, or there was a real pause before it. Precomputed once, then
    # looked up by start time — candidates always begin on a unit boundary.
    starts_sentence_by_time: dict[float, bool] = {}
    for i, unit in enumerate(units):
        prev = units[i - 1] if i > 0 else None
        starts_sentence_by_time[round(unit.start, 3)] = (
            prev is None or prev.ends_sentence or prev.gap_after >= config.pause_split
        )
    by_start = {round(u.start, 3): u for u in units}
    by_end = {round(u.end, 3): u for u in units}

    # Word lookup is linear per candidate otherwise; slice a sorted list instead.
    all_words = sorted(transcript.words, key=lambda w: w.start)
    midpoints = [(w.start + w.end) / 2 for w in all_words]

    for cand in candidates:
        words = _slice_words(all_words, midpoints, cand.start, cand.end)
        start_unit = by_start.get(round(cand.start, 3))
        end_unit = by_end.get(round(cand.end, 3))
        starts_sentence = starts_sentence_by_time.get(round(cand.start, 3), False)

        feats = {
            "hook": F.hook_feature(words, cand.start, niche),
            "keyword": F.keyword_feature(words, niche),
            "energy": F.energy_feature(envelope, cand.start, cand.end) if envelope else 0.5,
            "dynamics": F.dynamics_feature(envelope, cand.start, cand.end) if envelope else 0.5,
            "pacing": F.pacing_feature(words, cand.duration),
            "density": F.density_feature(envelope, cand.start, cand.end) if envelope else 0.5,
            "completeness": F.completeness_feature(
                starts_sentence=starts_sentence,
                ends_sentence=bool(end_unit and end_unit.ends_sentence),
                lead_gap=start_unit.gap_before if start_unit and start_unit.gap_before != float("inf") else 1.0,
                trail_gap=end_unit.gap_after if end_unit else 0.0,
            ),
            "arc": F.arc_feature(words, cand.start, cand.end),
            "length": F.length_feature(cand.duration, config.target_duration),
        }

        cand.features = feats
        cand.score = sum(weights.get(k, 0.0) * v for k, v in feats.items()) / weight_sum
        cand.text = " ".join(w.text for w in words)

    return sorted(candidates, key=lambda c: c.score, reverse=True)


def _slice_words(
    words: list[Word], midpoints: list[float], start: float, end: float
) -> list[Word]:
    from bisect import bisect_left

    i0 = bisect_left(midpoints, start)
    i1 = bisect_left(midpoints, end)
    return words[i0:i1]


def find_clips(
    transcript: Transcript,
    envelope: AudioEnvelope | None,
    config: ScoringConfig,
) -> list[Candidate]:
    """Full ranking pass: units -> candidates -> scores -> NMS -> top-k."""
    units = build_units(transcript, config.pause_split)
    if not units:
        return []
    candidates = generate_candidates(units, config)
    if not candidates:
        return []
    scored = score_candidates(candidates, transcript, units, envelope, config)
    deduped = dedupe_by_overlap(scored, config.max_iou)
    return deduped[: config.top_k]


def pad_to_bounds(
    candidate: Candidate, media_duration: float, lead: float = 0.15, trail: float = 0.35
) -> tuple[float, float]:
    """Small handles around the cut so the first consonant and the last breath
    survive. Trailing is larger because a clipped final word is very audible."""
    start = max(0.0, candidate.start - lead)
    end = min(media_duration, candidate.end + trail) if media_duration else candidate.end + trail
    return start, end
