"""The no-API path: CSV exports and saved HTML pages.

This is the fallback that makes the whole system work on day one with zero
accounts and zero API keys — and for SOLD price comps it's actually the more
accurate route, because eBay's free Browse API only exposes active listings.

Three inputs are supported, all dropped into `data/inbox/`:

1. `*.csv` describing items you're considering buying  -> load_candidates_csv()
2. `*.csv` of sold prices (any export, or hand-typed)  -> load_comps_csv()
3. `*.html` — an eBay "Sold items" search results page
   saved with Ctrl+S in your browser                   -> parse_sold_html()

Column headers are matched loosely, so an export from eBay, Vinted, Google
Sheets or a hand-rolled spreadsheet all work without renaming anything.
"""

from __future__ import annotations

import csv
import html
import re
from pathlib import Path
from typing import Iterable, Iterator

from ..models import Candidate

# ---------------------------------------------------------------------------
# Loose header matching
# ---------------------------------------------------------------------------

_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title", "item", "name", "item title", "product", "description short", "listing"),
    "ask_price": (
        "ask_price", "price", "cost", "buy price", "asking price", "current price",
        "start price", "amount", "paid", "sold price", "sale price", "sold for",
        "final price", "price sold", "total price", "item price", "price gbp",
    ),
    "url": ("url", "link", "item url", "listing url", "web url", "href"),
    "source_id": ("source_id", "id", "item id", "item number", "itemid", "sku"),
    "category": ("category", "cat", "type", "department"),
    "brand": ("brand", "make", "manufacturer", "label"),
    "model": ("model", "model no", "style", "style code", "part number", "mpn"),
    "size": ("size", "uk size", "eu size", "clothing size"),
    "colour": ("colour", "color", "colourway", "colorway"),
    "material": ("material", "fabric", "composition"),
    "condition": ("condition", "state", "grade", "item condition"),
    "condition_note": ("condition_note", "condition notes", "condition description"),
    "defects": ("defects", "flaws", "damage", "faults", "issues"),
    "location": ("location", "town", "city", "area", "postcode", "item location"),
    "seller": ("seller", "vendor", "shop", "seller name", "username"),
    "photos": ("photos", "images", "image urls", "photo urls", "picture urls", "image"),
    "weight_kg": ("weight_kg", "weight", "weight kg", "kg"),
    "notes": ("notes", "note", "comment", "comments", "remarks"),
}

_CONDITION_SYNONYMS = {
    "new with tags": "new_with_tags",
    "bnwt": "new_with_tags",
    "brand new": "new_with_tags",
    "new": "new_without_tags",
    "new without tags": "new_without_tags",
    "bnwot": "new_without_tags",
    "new other": "new_without_tags",
    "like new": "excellent",
    "excellent": "excellent",
    "very good": "excellent",
    "used - excellent": "excellent",
    "good": "good",
    "used": "good",
    "used - good": "good",
    "pre-owned": "good",
    "satisfactory": "fair",
    "fair": "fair",
    "worn": "fair",
    "poor": "fair",
    "for parts": "for_parts",
    "spares or repair": "for_parts",
    "not working": "for_parts",
    "faulty": "for_parts",
}

_PRICE_RE = re.compile(r"£\s?([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
_POSTAGE_HINT = re.compile(r"postage|delivery|shipping|p&p|collection", re.I)


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (header or "").strip().lower()).strip()


def _build_header_map(fieldnames: Iterable[str]) -> dict[str, str]:
    """{our_field: actual_csv_header}"""
    normalised = {_norm(f): f for f in fieldnames if f}
    mapping: dict[str, str] = {}
    for field, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in normalised:
                mapping[field] = normalised[alias]
                break
    return mapping


def parse_money(raw: object) -> float | None:
    """'£12.50', '12,50', ' 12.5 ', 'GBP 12' -> 12.5 ; junk -> None

    Deliberately strict about what counts as money. An earlier version pulled
    the first number out of any string, which happily turned the product title
    "North Face Nuptse 700 black" into £700 and poisoned every comp in the run.
    A bare number must now be the *whole* cell; a number embedded in prose is
    only accepted when a currency marker is present.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None

    text = str(raw).strip()
    if not text:
        return None

    has_currency = "£" in text or re.search(r"\bgbp\b", text, re.I) is not None
    cleaned = re.sub(r"(?i)\bgbp\b", "", text).replace("£", "").strip()

    # "12,50" is a decimal comma; "1,234.56" is a thousands separator.
    if re.search(r",\d{2}$", cleaned) and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")

    if has_currency:
        match = re.search(r"[0-9]+(?:\.[0-9]+)?", cleaned)
    else:
        match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*", cleaned)
    if not match:
        return None

    try:
        value = float(match.group(1) if match.lastindex else match.group(0))
    except ValueError:
        return None
    return value if value > 0 else None


def normalise_condition(raw: object) -> str:
    text = _norm(str(raw or ""))
    if not text:
        return "unknown"
    if text in _CONDITION_SYNONYMS:
        return _CONDITION_SYNONYMS[text]
    for key, value in _CONDITION_SYNONYMS.items():
        if key in text:
            return value
    return "unknown"


# ---------------------------------------------------------------------------
# Candidates from CSV
# ---------------------------------------------------------------------------


def load_candidates_csv(
    path: str | Path,
    *,
    default_category: str = "unknown",
    source: str = "csv",
) -> tuple[list[Candidate], list[str]]:
    """Returns (candidates, skip_messages). Malformed rows are skipped with a
    reason rather than blowing up the run."""
    path = Path(path)
    candidates: list[Candidate] = []
    skips: list[str] = []

    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return [], [f"{path.name}: file has no header row"]
        hmap = _build_header_map(reader.fieldnames)
        if "title" not in hmap:
            return [], [
                f"{path.name}: no recognisable title column "
                f"(looked for {', '.join(_ALIASES['title'])}); found {reader.fieldnames}"
            ]

        for line_no, row in enumerate(reader, start=2):
            def cell(field: str, default: str = "") -> str:
                key = hmap.get(field)
                return (row.get(key) or default).strip() if key else default

            title = cell("title")
            if not title:
                skips.append(f"{path.name}:{line_no} skipped — empty title")
                continue

            price = parse_money(cell("ask_price"))
            if price is None:
                skips.append(f"{path.name}:{line_no} skipped — unreadable price for '{title[:40]}'")
                continue

            photos = [p.strip() for p in re.split(r"[|;,]", cell("photos")) if p.strip()]
            weight = parse_money(cell("weight_kg"))

            candidates.append(
                Candidate(
                    title=title,
                    ask_price=price,
                    source=source,
                    source_id=cell("source_id") or f"{path.stem}:{line_no}",
                    url=cell("url"),
                    category=cell("category") or default_category,
                    brand=cell("brand"),
                    model=cell("model"),
                    size=cell("size"),
                    colour=cell("colour"),
                    material=cell("material"),
                    condition=normalise_condition(cell("condition")),
                    condition_note=cell("condition_note"),
                    defects=cell("defects"),
                    location=cell("location"),
                    seller=cell("seller"),
                    photos=photos,
                    weight_kg=weight,
                    notes=cell("notes"),
                    # Anything typed into a spreadsheet by hand is self-reported
                    # until a photo backs it up. The quality gate cares about this.
                    evidence={k: "csv_self_reported" for k in ("brand", "model", "size") if cell(k)},
                )
            )

    return candidates, skips


# ---------------------------------------------------------------------------
# Price comps from CSV
# ---------------------------------------------------------------------------


def find_price_column(fieldnames: Iterable[str]) -> str | None:
    """The column holding the sale price, or None if we can't tell.

    Returning None is a valid, useful answer — guessing a column is how a
    title ends up being read as a price.
    """
    names = [f for f in fieldnames if f]
    hmap = _build_header_map(names)
    if "ask_price" in hmap:
        return hmap["ask_price"]
    # Any header mentioning price/sold, as long as it isn't about postage.
    for name in names:
        normalised = _norm(name)
        if _POSTAGE_HINT.search(normalised):
            continue
        if "price" in normalised or "sold" in normalised or "amount" in normalised:
            return name
    return None


def load_comps_rows(path: str | Path) -> tuple[list[tuple[str, float]], list[str]]:
    """(title, price) pairs from a sold-listings export.

    Titles matter: they let the pricing engine check that a comp is actually
    comparable to the item in hand, instead of averaging a jacket against a
    pair of headphones because they happened to share a file.
    """
    path = Path(path)
    rows: list[tuple[str, float]] = []
    skips: list[str] = []

    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return [], [f"{path.name}: no header row"]

        hmap = _build_header_map(reader.fieldnames)
        price_col = find_price_column(reader.fieldnames)
        title_col = hmap.get("title")

        if not price_col:
            return [], [
                f"{path.name}: no price column found. Rename the sale-price column to "
                f"'price' or 'sold price'. Headers seen: {', '.join(reader.fieldnames)}"
            ]

        for line_no, row in enumerate(reader, start=2):
            value = parse_money(row.get(price_col))
            if value is None:
                skips.append(f"{path.name}:{line_no} skipped — no price in column '{price_col}'")
                continue
            title = (row.get(title_col) or "").strip() if title_col else ""
            rows.append((title, value))

    return rows, skips


def load_comps_csv(path: str | Path) -> tuple[list[float], list[str]]:
    """Just the prices, for callers that don't need titles."""
    rows, skips = load_comps_rows(path)
    return [price for _, price in rows], skips


# ---------------------------------------------------------------------------
# Price comps from a saved eBay "Sold items" HTML page
# ---------------------------------------------------------------------------


def parse_sold_html(source: str | Path) -> tuple[list[float], list[str]]:
    """Extract sold prices from a saved eBay search-results page.

    Accepts a file path or raw HTML. Handles the two shapes eBay uses:
    a single price ("£24.99") and a range ("£19.99 to £29.99" — we take the
    midpoint). Postage lines are excluded so they don't drag the median down.

    Deliberately regex-based rather than DOM-based: eBay's class names change
    every few months, and a parser that only looks for money-shaped text next
    to the word-free noise survives that better than one keyed on `s-item__price`.
    """
    text = Path(source).read_text(encoding="utf-8", errors="replace") if _looks_like_path(source) else str(source)

    prices: list[float] = []
    notes: list[str] = []

    for chunk in _price_bearing_chunks(text):
        if _POSTAGE_HINT.search(chunk):
            continue
        found = [parse_money(m) for m in _PRICE_RE.findall(chunk)]
        found = [f for f in found if f is not None]
        if not found:
            continue
        if len(found) >= 2 and " to " in chunk.lower():
            prices.append(round((found[0] + found[1]) / 2, 2))
        else:
            prices.append(found[0])

    if not prices:
        notes.append(
            "No prices found in the HTML. Check you saved the search RESULTS page "
            "(with the 'Sold items' filter on) rather than a single listing page."
        )
    return prices, notes


def _looks_like_path(source: str | Path) -> bool:
    if isinstance(source, Path):
        return True
    return len(source) < 400 and "\n" not in source and Path(source).exists()


def _price_bearing_chunks(document: str) -> Iterator[str]:
    """Yield short text snippets that contain a £ amount.

    We strip tags first, then walk the visible text in small windows so a
    postage line can't be confused with the item price beside it.
    """
    # Drop script/style bodies — they're full of £-shaped noise in templates.
    cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", document)
    cleaned = re.sub(r"(?s)<[^>]+>", "\n", cleaned)
    cleaned = html.unescape(cleaned)
    for line in cleaned.split("\n"):
        line = line.strip()
        if line and "£" in line and len(line) < 200:
            yield line
