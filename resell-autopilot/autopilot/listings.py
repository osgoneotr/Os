"""Listing copy generation + the authentication / quality gate.

Two jobs:

1. Turn an analysed item into marketplace-ready copy — two titles, a bulleted
   description, ranked keywords, a list price and a floor price.
2. Refuse to write anything the evidence doesn't support. Every attribute that
   appears in a title or description must be traceable to a photo, a structured
   API field, or something you physically confirmed. Anything else gets hedged
   or stripped, and you get told why.

The second job is the important one. An overclaimed listing is how you get a
"not as described" case, a refund, and a defect on a new account.
"""

from __future__ import annotations

import re
from typing import Iterable

from . import config as cfg_mod
from .models import Analysis, Candidate, ListingPackage

# Evidence sources we treat as trustworthy enough to state a fact plainly.
TRUSTED_EVIDENCE = {"photo", "photo_confirmed", "in_hand", "ebay_api", "barcode", "user_confirmed"}

CONDITION_LABELS = {
    "new_with_tags": "New with tags",
    "new_without_tags": "New without tags",
    "excellent": "Excellent used condition",
    "good": "Good used condition",
    "fair": "Fair — visible wear, described below",
    "for_parts": "Spares or repair — sold as untested/faulty",
    "unknown": "Used — condition as per photos",
}

CATEGORY_KEYWORDS = {
    "trainers": ["trainers", "sneakers", "shoes", "running shoes", "streetwear", "uk size"],
    "clothing": ["jacket", "vintage", "streetwear", "menswear", "womenswear", "y2k"],
    "retro_tech": ["retro", "vintage tech", "collectable", "tested", "boxed", "rare"],
    "audio": ["hi-fi", "separates", "vintage audio", "stereo", "speakers", "amplifier"],
    "unknown": ["preloved", "second hand", "bargain"],
}

# Phrases that must never appear unless a specific condition is met.
_CONDITIONAL_CLAIMS = {
    "brand new": lambda c: c.condition == "new_with_tags",
    "never worn": lambda c: c.condition in ("new_with_tags", "new_without_tags"),
    "never used": lambda c: c.condition in ("new_with_tags", "new_without_tags"),
    "fully working": lambda c: c.tested_confirmed,
    "fully tested": lambda c: c.tested_confirmed,
    "works perfectly": lambda c: c.tested_confirmed,
    "in perfect condition": lambda c: c.condition in ("new_with_tags", "new_without_tags"),
}


# ---------------------------------------------------------------------------
# Safety gate — run this BEFORE spending money, not just before listing
# ---------------------------------------------------------------------------


def check_blocked(candidate: Candidate, cfg=None) -> str | None:
    """Return a reason string if we won't handle this item at all."""
    cfg = cfg or cfg_mod.load()
    haystack = " ".join(
        [candidate.title, candidate.model, candidate.notes, candidate.condition_note]
    ).lower()
    for word in cfg.get("safety.blocked_keywords", []) or []:
        if str(word).lower() in haystack:
            return (
                f"Blocked: matched safety keyword '{word}'. This is either a counterfeit "
                "indicator, a restricted item, or something with a safety history we "
                "can't verify (used helmets, car seats, alarms). Not worth the risk."
            )
    return None


def audit_claims(text: str, candidate: Candidate, cfg=None) -> tuple[str, list[str], list[str]]:
    """Strip claims the evidence doesn't support.

    Returns (cleaned_text, stripped_claims, warnings).
    """
    cfg = cfg or cfg_mod.load()
    stripped: list[str] = []
    warnings: list[str] = []
    cleaned = text

    # 1. Claims that are never allowed, regardless of condition.
    for phrase in cfg.get("safety.banned_claims", []) or []:
        phrase = str(phrase)
        if phrase.lower() in _CONDITIONAL_CLAIMS:
            continue  # handled below, where we can check the condition
        pattern = re.compile(re.escape(phrase), re.I)
        if pattern.search(cleaned):
            cleaned = pattern.sub("", cleaned)
            stripped.append(phrase)
            warnings.append(
                f"Removed '{phrase}' — you can't substantiate it, and authenticity claims "
                "you can't prove are the fastest route to a case against you."
            )

    # 2. Claims that are allowed only under specific conditions.
    for phrase, allowed in _CONDITIONAL_CLAIMS.items():
        pattern = re.compile(re.escape(phrase), re.I)
        if pattern.search(cleaned) and not allowed(candidate):
            cleaned = pattern.sub(_softened(phrase), cleaned)
            stripped.append(phrase)
            warnings.append(
                f"Softened '{phrase}' — condition is '{candidate.condition}'"
                + ("" if candidate.tested_confirmed else " and the item isn't confirmed tested")
                + "."
            )

    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, stripped, warnings


def _softened(phrase: str) -> str:
    return {
        "brand new": "unworn",
        "never worn": "no visible signs of wear",
        "never used": "no visible signs of use",
        "fully working": "untested — sold as seen",
        "fully tested": "untested — sold as seen",
        "works perfectly": "untested — sold as seen",
        "in perfect condition": "in the condition shown in the photos",
    }.get(phrase, "")


def evidence_warnings(candidate: Candidate, cfg=None) -> list[str]:
    """Flag attributes we're about to state that nothing actually backs up."""
    cfg = cfg or cfg_mod.load()
    warnings: list[str] = []
    needed = cfg.get("safety.attributes_needing_evidence", []) or []
    unconfirmed: list[str] = []

    for attribute in needed:
        attribute = str(attribute)
        value = getattr(candidate, attribute.replace(" ", "_"), None)
        if attribute == "working condition":
            if candidate.category in ("retro_tech", "audio") and not candidate.tested_confirmed:
                warnings.append(
                    "Working condition is unverified. Either power the item on and set "
                    "tested_confirmed, or list it explicitly as untested."
                )
            continue
        if not value:
            continue
        if candidate.evidence.get(attribute, "") not in TRUSTED_EVIDENCE:
            unconfirmed.append(f"{attribute}='{value}'")

    if unconfirmed:
        # One line, not one per attribute — five near-identical warnings train
        # you to skim past all of them.
        warnings.append(
            f"Unconfirmed from photos or structured data: {', '.join(unconfirmed)}. "
            "The description says these come from the item's labels rather than asserting "
            "them. Photograph the label to state them plainly."
        )
    if not candidate.photos:
        warnings.append("No photos attached. Nothing in the copy can be photo-verified yet.")
    return warnings


def _is_evidenced(candidate: Candidate, attribute: str) -> bool:
    return candidate.evidence.get(attribute, "") in TRUSTED_EVIDENCE


# ---------------------------------------------------------------------------
# Copy generation
# ---------------------------------------------------------------------------


def build_titles(candidate: Candidate, channel: str, cfg=None) -> tuple[str, str]:
    """(seo_title, style_title), both within the channel's character limit."""
    cfg = cfg or cfg_mod.load()
    limit = int(cfg.channel(channel).get("title_max_chars", 80))

    category_word = {
        "trainers": "Trainers",
        "clothing": "",
        "retro_tech": "",
        "audio": "",
    }.get(candidate.category, "")

    # SEO title: the words a buyer types, in the order they type them.
    seo_parts = [
        candidate.brand,
        candidate.model,
        category_word,
        candidate.colour,
        f"UK {candidate.size}" if candidate.size and candidate.category == "trainers" else candidate.size,
        _short_condition(candidate.condition),
    ]
    seo = _assemble(seo_parts, limit) or _truncate(candidate.title, limit)

    # Style title: how the item is described in the wild.
    era = _era_word(candidate)
    style_parts = [
        era,
        candidate.brand,
        candidate.model,
        candidate.colour,
        _vibe_word(candidate),
        f"Size {candidate.size}" if candidate.size else "",
    ]
    style = _assemble(style_parts, limit) or seo
    if style == seo:
        style = _truncate(f"{era} {candidate.title}".strip(), limit)

    return seo, style


def _assemble(parts: Iterable[str], limit: int) -> str:
    """Join parts, dropping the least important trailing ones until it fits."""
    kept = [str(p).strip() for p in parts if p and str(p).strip()]
    # Drop duplicate words while preserving order (e.g. brand repeated in model).
    seen: set[str] = set()
    deduped: list[str] = []
    for part in kept:
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(part)

    while deduped:
        joined = " ".join(deduped)
        if len(joined) <= limit:
            return joined
        deduped.pop()
    return ""


def _truncate(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].strip()


def _short_condition(condition: str) -> str:
    return {
        "new_with_tags": "BNWT",
        "new_without_tags": "New",
        "excellent": "VGC",
        "good": "",
        "fair": "Worn",
        "for_parts": "Spares/Repair",
    }.get(condition, "")


def _era_word(candidate: Candidate) -> str:
    text = f"{candidate.title} {candidate.notes}".lower()
    for token, word in (("90s", "Vintage 90s"), ("80s", "Vintage 80s"), ("y2k", "Y2K"),
                        ("retro", "Retro"), ("vintage", "Vintage")):
        if token in text:
            return word
    return "Retro" if candidate.category in ("retro_tech", "audio") else ""


def _vibe_word(candidate: Candidate) -> str:
    return {
        "trainers": "Streetwear",
        "clothing": "Streetwear",
        "retro_tech": "Collectable",
        "audio": "Hi-Fi Separate",
    }.get(candidate.category, "")


def build_description(candidate: Candidate, analysis: Analysis, channel: str, cfg=None) -> str:
    """Bulleted description: attributes, condition, defects, postage, honesty note."""
    cfg = cfg or cfg_mod.load()
    lines: list[str] = []

    headline = " ".join(p for p in (candidate.brand, candidate.model) if p) or candidate.title
    lines.append(headline)
    lines.append("")

    unevidenced: list[str] = []

    def bullet(label: str, value: str, attribute: str | None = None) -> None:
        if not value:
            return
        # One disclaimer at the end reads better than the same parenthetical
        # after every line, and says exactly the same thing.
        if attribute and not _is_evidenced(candidate, attribute):
            unevidenced.append(label.lower())
        lines.append(f"• {label}: {value}")

    bullet("Brand", candidate.brand, "brand")
    bullet("Model", candidate.model, "model")
    bullet("Size", candidate.size, "size")
    bullet("Colour", candidate.colour, "colour")
    bullet("Material", candidate.material, "material")
    bullet("Condition", CONDITION_LABELS.get(candidate.condition, "Used"))

    if candidate.condition_note:
        lines.append(f"• Notes: {candidate.condition_note}")

    if candidate.defects:
        lines.append(f"• Flaws: {candidate.defects}")
    else:
        lines.append("• Flaws: none found beyond the wear shown in the photos")

    if candidate.category in ("retro_tech", "audio"):
        # Say "untested" in the word buyers actually search for, and price it
        # accordingly — this is the sentence that prevents the dispute.
        lines.append(
            "• Tested: yes, powered on and checked"
            if candidate.tested_confirmed
            else "• Tested: no — sold as untested/spares, priced accordingly"
        )

    if unevidenced:
        lines.append("")
        lines.append(
            f"The {', '.join(unevidenced)} above {'is' if len(unevidenced) == 1 else 'are'} "
            "taken from the item's own labels — please check the photos and ask if anything "
            "is unclear."
        )

    lines.append("")
    if channel == "vinted":
        lines.append("Posted within 1 working day of your order. Bundle discounts available.")
    elif channel == "facebook":
        lines.append(f"Collection from {candidate.location or cfg.get('business.base_location')}. Cash on collection.")
    else:
        lines.append("Dispatched within 1 working day, tracked. UK only.")

    lines.append("Photos are of the actual item you'll receive.")
    return "\n".join(lines).strip()


def build_keywords(candidate: Candidate, limit: int = 20) -> list[str]:
    """10–20 ranked tags, most valuable first (brand/model beats generic nouns)."""
    ranked: list[str] = []

    def add(*values: str) -> None:
        for value in values:
            value = " ".join(str(value or "").split()).lower()
            if value and value not in ranked:
                ranked.append(value)

    if candidate.brand and candidate.model:
        add(f"{candidate.brand} {candidate.model}")
    add(candidate.brand, candidate.model)
    if candidate.brand and candidate.colour:
        add(f"{candidate.brand} {candidate.colour}")
    if candidate.size:
        add(f"size {candidate.size}", f"uk {candidate.size}" if candidate.category == "trainers" else "")
    add(candidate.colour, candidate.material)
    era = _era_word(candidate)
    if era:
        add(era)
    for keyword in CATEGORY_KEYWORDS.get(candidate.category, CATEGORY_KEYWORDS["unknown"]):
        # Never tag an item "tested" when we've just written "untested" in the
        # description — the tags are part of the listing, not decoration.
        if keyword == "tested" and not candidate.tested_confirmed:
            continue
        if keyword == "boxed" and "box" not in f"{candidate.title} {candidate.notes}".lower():
            continue
        add(keyword)
    # Title words are the last resort — least specific, so lowest rank.
    add(*[w for w in re.findall(r"[A-Za-z0-9']{4,}", candidate.title)][:6])

    return [k for k in ranked if k][:limit]


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_package(analysis: Analysis, *, channel: str | None = None, cfg=None) -> ListingPackage:
    """Full listing package for one item, with the quality gate applied."""
    cfg = cfg or cfg_mod.load()
    candidate = analysis.candidate
    channel = channel or analysis.economics.channel

    blocked = check_blocked(candidate, cfg)
    if blocked:
        return ListingPackage(
            sku=candidate.sku,
            channel=channel,
            title_seo="",
            title_style="",
            description="",
            keywords=[],
            list_price=0.0,
            min_price=0.0,
            quality_gate="fail",
            warnings=[blocked],
        )

    seo, style = build_titles(candidate, channel, cfg)
    description = build_description(candidate, analysis, channel, cfg)

    warnings = evidence_warnings(candidate, cfg)
    stripped: list[str] = []
    for text_name, text in (("title_seo", seo), ("title_style", style), ("description", description)):
        cleaned, claims, claim_warnings = audit_claims(text, candidate, cfg)
        if text_name == "title_seo":
            seo = cleaned
        elif text_name == "title_style":
            style = cleaned
        else:
            description = cleaned
        stripped.extend(claims)
        warnings.extend(claim_warnings)

    limit = int(cfg.channel(channel).get("title_max_chars", 80))
    seo, style = _truncate(seo, limit), _truncate(style, limit)

    if not seo:
        warnings.append("Could not build a title — the item has no brand, model or usable title text.")

    gate = "fail" if not seo else ("pass_with_warnings" if warnings else "pass")

    return ListingPackage(
        sku=candidate.sku,
        channel=channel,
        title_seo=seo,
        title_style=style,
        description=description,
        keywords=build_keywords(candidate),
        list_price=analysis.economics.list_price,
        min_price=analysis.economics.min_price,
        category_hint=candidate.category,
        quality_gate=gate,
        warnings=_dedupe(warnings),
        stripped_claims=_dedupe(stripped),
    )


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out
