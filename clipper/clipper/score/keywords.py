"""Lexicons for the heuristic scorer.

These weights encode a simple, testable prior about what makes a spoken segment
work as a short: it opens with a curiosity gap or a direct address, it makes a
concrete claim, and it resolves. It is not a model of virality — it is a
ranking heuristic that beats "cut every 30 seconds", and it is meant to be
replaced by a learned scorer once you have retention data (see v3 in DESIGN.md).
"""

from __future__ import annotations

import re

# Multi-word openers, matched against the first few seconds of a window.
HOOK_PHRASES: dict[str, float] = {
    "here's why": 1.0,
    "here's how": 1.0,
    "here's the thing": 0.9,
    "the truth is": 0.9,
    "the problem is": 0.8,
    "the reason": 0.7,
    "nobody tells you": 1.0,
    "no one talks about": 1.0,
    "most people": 0.8,
    "everyone thinks": 0.9,
    "the biggest mistake": 1.0,
    "the number one": 0.9,
    "what if": 0.8,
    "let me explain": 0.7,
    "this is why": 0.9,
    "that's why": 0.6,
    "the secret": 0.9,
    "i'll show you": 0.8,
    "you need to": 0.7,
    "stop doing": 0.9,
    "never do": 0.8,
    "turns out": 0.7,
    "it turns out": 0.7,
    "believe it or not": 0.8,
    "the crazy part": 0.9,
    "listen to this": 0.7,
}

# Single tokens: curiosity, stakes and concreteness.
KEYWORD_WEIGHTS: dict[str, float] = {
    # curiosity / contrarian
    "secret": 0.8,
    "mistake": 0.8,
    "wrong": 0.7,
    "myth": 0.8,
    "actually": 0.5,
    "surprising": 0.7,
    "nobody": 0.6,
    "truth": 0.7,
    "hack": 0.6,
    "trick": 0.6,
    "counterintuitive": 0.8,
    # stakes / emotion
    "insane": 0.6,
    "crazy": 0.5,
    "incredible": 0.5,
    "brutal": 0.6,
    "disaster": 0.6,
    "expensive": 0.5,
    "free": 0.5,
    "worst": 0.6,
    "best": 0.5,
    "fastest": 0.6,
    "biggest": 0.5,
    # instructional value
    "step": 0.5,
    "steps": 0.5,
    "tip": 0.5,
    "rule": 0.5,
    "framework": 0.6,
    "lesson": 0.5,
    "strategy": 0.5,
    "example": 0.4,
    "because": 0.4,
}

SECOND_PERSON = {"you", "your", "you're", "youre", "yourself"}

# Connectives that signal a payoff rather than a setup.
RESOLUTION_MARKERS = (
    "because",
    "so that",
    "which means",
    "that's why",
    "the reason is",
    "in other words",
    "so the answer",
    "here's what happened",
    "the result",
)

NUMBER_RE = re.compile(r"\b\d+([.,]\d+)?%?\b")
WORD_RE = re.compile(r"[a-z0-9']+")

STOPWORDS = {
    "a", "about", "above", "after", "again", "all", "am", "an", "and", "any", "are",
    "as", "at", "be", "been", "before", "being", "below", "between", "both", "but",
    "by", "can", "did", "do", "does", "doing", "don", "down", "during", "each", "few",
    "for", "from", "further", "had", "has", "have", "having", "he", "her", "here",
    "hers", "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "just",
    "know", "like", "me", "more", "most", "my", "no", "nor", "not", "now", "of", "off",
    "on", "once", "one", "only", "or", "other", "our", "out", "over", "own", "really",
    "right", "said", "same", "say", "see", "she", "should", "so", "some", "such",
    "than", "that", "the", "their", "them", "then", "there", "these", "they", "thing",
    "things", "think", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "want", "was", "way", "we", "well", "were", "what", "when", "where",
    "which", "while", "who", "whom", "why", "will", "with", "would", "you", "your",
    "yeah", "okay", "ok", "gonna", "got", "get", "going", "actually", "basically",
    "kind", "sort", "lot", "much", "even", "also", "make", "made", "take", "come",
    "look", "give", "good", "great", "little", "big", "back", "still", "never",
    "always", "every", "many", "mean", "means", "us", "im", "ive", "dont",
}


def tokenize(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def phrase_score(text: str, phrases: dict[str, float] | None = None) -> float:
    lowered = " " + " ".join(tokenize(text)) + " "
    table = phrases if phrases is not None else HOOK_PHRASES
    return sum(weight for phrase, weight in table.items() if f" {phrase} " in lowered)


def token_score(tokens: list[str], extra: dict[str, float] | None = None) -> float:
    table = dict(KEYWORD_WEIGHTS)
    if extra:
        table.update(extra)
    return sum(table.get(tok, 0.0) for tok in tokens)
