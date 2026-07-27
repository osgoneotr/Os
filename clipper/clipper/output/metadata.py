"""Suggested title, caption and hashtags for each clip.

Two generators. The heuristic one runs offline at zero cost and is the default;
it pulls keywords out of the clip's own transcript and reuses the hook line as
the caption opener, which is what you would write by hand anyway. The LLM one is
opt-in and produces better copy.

Nothing here posts anything. The output is a file for the operator to read,
edit, and paste.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter

from ..config import OutputConfig
from ..models import Candidate, ClipMetadata
from ..score.keywords import STOPWORDS, tokenize

log = logging.getLogger(__name__)

MAX_TITLE_CHARS = 80


def extract_keywords(text: str, limit: int = 8) -> list[str]:
    """Frequency ranking with an early-position bonus.

    Deliberately not TF-IDF: with a single document and no corpus, term
    frequency plus "was it said in the first 20%" is the honest signal.
    """
    tokens = [t for t in tokenize(text) if len(t) > 2 and t not in STOPWORDS]
    if not tokens:
        return []
    head_cutoff = max(1, len(tokens) // 5)
    head = set(tokens[:head_cutoff])

    counts = Counter(tokens)
    scored = {tok: count + (1.5 if tok in head else 0.0) for tok, count in counts.items()}
    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], tokens.index(kv[0])))
    return [tok for tok, _ in ranked[:limit]]


def build_metadata(
    candidate: Candidate,
    config: OutputConfig,
    niche_hashtags: list[str] | None = None,
) -> ClipMetadata:
    if config.metadata_generator == "llm":
        try:
            return _llm_metadata(candidate, config, niche_hashtags or [])
        except Exception as exc:  # fall back rather than fail a finished render
            log.warning("LLM metadata generation failed (%s); using heuristic", exc)
    return _heuristic_metadata(candidate, config, niche_hashtags or [])


def _heuristic_metadata(
    candidate: Candidate, config: OutputConfig, niche_hashtags: list[str]
) -> ClipMetadata:
    text = candidate.text.strip()
    keywords = extract_keywords(text)
    title = _first_sentence(text)
    caption = _compose_caption(title, text)
    hashtags = _compose_hashtags(keywords, config, niche_hashtags)
    return ClipMetadata(
        title=title,
        caption=caption,
        hashtags=hashtags,
        keywords=keywords,
        transcript=text,
        generator="heuristic",
    )


def _first_sentence(text: str, limit: int = MAX_TITLE_CHARS) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", text.strip())[0] if text.strip() else ""
    sentence = sentence.strip(" .")
    if len(sentence) <= limit:
        return sentence
    truncated = sentence[:limit].rsplit(" ", 1)[0]
    return truncated + "…"


def _compose_caption(title: str, text: str) -> str:
    if not text:
        return title
    body = _first_sentence(text, limit=200)
    if title and body.lower().startswith(title.lower().rstrip("…")):
        return body
    return f"{title}\n\n{body}" if title else body


def _compose_hashtags(
    keywords: list[str], config: OutputConfig, niche_hashtags: list[str]
) -> list[str]:
    tags: list[str] = []
    for tag in list(config.base_hashtags) + list(niche_hashtags):
        normalized = "#" + re.sub(r"[^a-z0-9]", "", tag.lower().lstrip("#"))
        if len(normalized) > 1 and normalized not in tags:
            tags.append(normalized)
    for keyword in keywords:
        tag = "#" + re.sub(r"[^a-z0-9]", "", keyword.lower())
        if len(tag) > 3 and tag not in tags:
            tags.append(tag)
    return tags[: config.hashtag_count]


# --- Optional LLM generator -------------------------------------------------

_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "caption", "hashtags"],
    "additionalProperties": False,
}

_PROMPT = """You write short-form video copy. Below is the transcript of one clip.

Write:
- title: a punchy on-screen title, at most 60 characters, no quotes
- caption: 1-3 sentences for the post body, written in the speaker's voice
- hashtags: {count} lowercase hashtags including the leading '#', most specific first

Do not invent facts that are not in the transcript. Do not use emoji.
{niche_line}
Transcript:
\"\"\"{text}\"\"\""""


def _llm_metadata(
    candidate: Candidate, config: OutputConfig, niche_hashtags: list[str]
) -> ClipMetadata:
    from anthropic import Anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    niche_line = (
        f"The account's niche tags are: {', '.join(niche_hashtags)}.\n"
        if niche_hashtags
        else ""
    )
    prompt = _PROMPT.format(
        count=config.hashtag_count, niche_line=niche_line, text=candidate.text.strip()
    )

    client = Anthropic()
    response = client.beta.messages.create(
        model="claude-opus-5",
        max_tokens=2000,
        # Short, well-specified task: low effort keeps it fast and cheap.
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": _SCHEMA}},
        # Server-side fallback: if a safety classifier declines, the API retries
        # on the recommended model instead of returning an unusable response.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": prompt}],
    )

    if response.stop_reason == "refusal":
        raise RuntimeError("model declined to generate metadata for this clip")

    payload = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(payload)
    return ClipMetadata(
        title=str(data["title"]).strip(),
        caption=str(data["caption"]).strip(),
        hashtags=[str(h).strip() for h in data["hashtags"]][: config.hashtag_count],
        keywords=extract_keywords(candidate.text),
        transcript=candidate.text.strip(),
        generator="llm:claude-opus-5",
    )
