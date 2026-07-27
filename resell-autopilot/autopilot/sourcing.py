"""The sourcing engine: candidates in, analysed deals out.

Keeps the scripts thin. Handles the two things that are fiddly in practice:

1. Resolving comps for a search phrase from whichever source is available
   (eBay API / sold-listings CSV / saved HTML / nothing), with caching so a
   50-item run doesn't make 50 identical API calls.
2. Running every candidate through safety -> comps -> pricing -> verdict
   without one bad row killing the whole run.
"""

from __future__ import annotations

import re
import traceback
from pathlib import Path
from typing import Iterable, Sequence

import yaml

from . import config as cfg_mod
from . import listings as listings_mod
from . import pricing as pricing_mod
from .models import Analysis, Candidate, Comps
from .sources import tabular
from .sources.ebay_browse import EbayAuthError, EbayBrowseClient


class CompsResolver:
    """Finds price evidence for a search phrase, cheapest source first.

    Priority order:
      1. A sold-comps file you supplied (most accurate — real sold prices).
      2. eBay Marketplace Insights, if you've been approved for it.
      3. eBay Browse active listings, calibrated down (an estimate).
      4. Nothing — the item comes back as REVIEW, never as a confident BUY.
    """

    def __init__(self, cfg=None, *, comps_file: str | Path | None = None, use_api: bool = True):
        self.cfg = cfg or cfg_mod.load()
        if comps_file:
            self.comps_file: Path | None = Path(comps_file)
        else:
            # No --comps given: fall back to the standing comps folder, so
            # sold-listing pages you've saved get used without extra flags.
            default_dir = self.cfg.get("paths.comps")
            candidate_dir = self.cfg.path("comps") if default_dir else None
            self.comps_file = (
                candidate_dir
                if candidate_dir and candidate_dir.is_dir() and any(candidate_dir.iterdir())
                else None
            )
        self.use_api = use_api
        self._cache: dict[str, Comps] = {}
        self._client: EbayBrowseClient | None = None
        self._api_disabled_reason: str | None = None
        self._file_rows: list[tuple[str, float]] | None = None
        self._file_has_titles = False
        self._untitled_warning_shown = False

    # -- api client, created lazily so the CSV path needs no credentials ---

    @property
    def client(self) -> EbayBrowseClient | None:
        if not self.use_api or self._api_disabled_reason:
            return None
        if self._client is None:
            client = EbayBrowseClient(self.cfg)
            if not client.configured:
                self._api_disabled_reason = (
                    "eBay API credentials not set — using file comps only. "
                    "Add EBAY_CLIENT_ID/EBAY_CLIENT_SECRET to .env to enable live comps."
                )
                return None
            self._client = client
        return self._client

    @property
    def api_note(self) -> str | None:
        return self._api_disabled_reason

    # -- resolution -------------------------------------------------------

    def resolve(self, query: str, category: str = "unknown") -> Comps:
        key = query.strip().lower()
        if key in self._cache:
            return self._cache[key]

        comps = self._resolve_uncached(query, category)
        self._cache[key] = comps
        return comps

    def _resolve_uncached(self, query: str, category: str) -> Comps:
        if self.comps_file is not None:
            prices, note = self._prices_from_file(query)
            if prices:
                comps = pricing_mod.build_comps(
                    query, prices, source="ebay_sold_csv", category=category, cfg=self.cfg
                )
                if note:
                    comps.warnings.insert(0, note)
                return comps
            if note:
                # The file exists but doesn't cover this item. Say so and stop —
                # averaging unrelated comps is worse than having none.
                empty = Comps(query=query, source="none")
                empty.confidence = "none"
                empty.warnings.append(note)
                return empty

        client = self.client
        if client is not None:
            # Real sold data first, if the account has it.
            try:
                prices, urls = client.sold_price_samples(query)
                if prices:
                    return pricing_mod.build_comps(
                        query, prices, source="ebay_sold_api", category=category,
                        sample_urls=urls, cfg=self.cfg,
                    )
            except EbayAuthError:
                pass  # not approved for Marketplace Insights; fall through
            except Exception as exc:
                print(f"[comps] sold-price lookup failed for '{query}': {exc}")

            try:
                prices, urls = client.price_samples(query)
                if prices:
                    return pricing_mod.build_comps(
                        query, prices, source="ebay_browse_active", category=category,
                        sample_urls=urls, cfg=self.cfg,
                    )
            except EbayAuthError as exc:
                self._api_disabled_reason = str(exc)
            except Exception as exc:
                print(f"[comps] active-listing lookup failed for '{query}': {exc}")

        empty = Comps(query=query, source="none")
        empty.confidence = "none"
        empty.warnings.append(
            f"No comps available for '{query}'. Supply a sold-listings export with --comps, "
            "or set eBay API credentials. Item returned as REVIEW, not BUY."
        )
        return empty

    def _prices_from_file(self, query: str) -> tuple[list[float], str | None]:
        """Prices from the supplied comps file that are relevant to `query`."""
        rows = self._load_file_rows()
        if not rows:
            return [], f"Comps file {self.comps_file.name if self.comps_file else ''} yielded no prices."

        if not self._file_has_titles:
            note = None
            if not self._untitled_warning_shown:
                self._untitled_warning_shown = True
                note = (
                    f"Comps file has no title column, so the same {len(rows)} price(s) are applied "
                    "to EVERY item in this run. That is only correct if the file covers one product. "
                    "For a mixed batch, export sold listings with titles included."
                )
            return [price for _, price in rows], note

        matched = [price for title, price in rows if _is_comparable(query, title)]
        if len(matched) >= 2:
            return matched, None
        return [], (
            f"Comps file has {len(rows)} row(s) but only {len(matched)} match '{query}'. "
            "Not enough comparable sold items — export sold listings for this specific product."
        )

    def _load_file_rows(self) -> list[tuple[str, float]]:
        if self._file_rows is not None:
            return self._file_rows

        path = self.comps_file
        assert path is not None
        if not path.exists():
            print(f"[comps] not found: {path}")
            self._file_rows = []
            return []

        # A directory means "here is everything I've saved" — merge the lot and
        # let per-item title matching sort out which comps belong to which item.
        files = (
            sorted(p for p in path.iterdir() if p.suffix.lower() in (".csv", ".html", ".htm"))
            if path.is_dir()
            else [path]
        )
        if not files:
            print(f"[comps] no .csv/.html files in {path}")
            self._file_rows = []
            return []

        rows: list[tuple[str, float]] = []
        for file in files:
            if file.suffix.lower() in (".html", ".htm"):
                prices, notes = tabular.parse_sold_html(file)
                # A saved eBay results page is one search, so its filename is
                # the best title we have for matching against.
                label = file.stem.replace("_", " ").replace("-", " ")
                file_rows = [(label, price) for price in prices]
            else:
                file_rows, notes = tabular.load_comps_rows(file)

            for note in notes[:3]:
                print(f"[comps] {note}")
            print(f"[comps] {file.name}: {len(file_rows)} price(s)")
            rows.extend(file_rows)

        self._file_has_titles = any(title for title, _ in rows)
        if self._file_has_titles:
            print(f"[comps] {len(rows)} comp(s) loaded, matched per item by title")
        self._file_rows = rows
        return rows


# ---------------------------------------------------------------------------
# Candidate gathering
# ---------------------------------------------------------------------------


def load_seed_queries(path: str | Path) -> tuple[list[str], dict]:
    """Flatten a seeds YAML file into a list of search phrases."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    queries: list[str] = []
    for group in (data.get("queries") or {}).values():
        if isinstance(group, list):
            queries.extend(str(q) for q in group)
    return queries, data


def gather_from_ebay(
    queries: Sequence[str],
    *,
    category: str,
    max_price: float | None = None,
    cfg=None,
) -> list[Candidate]:
    cfg = cfg or cfg_mod.load()
    client = EbayBrowseClient(cfg)
    if not client.configured:
        raise EbayAuthError(
            "eBay API credentials not set. Either add EBAY_CLIENT_ID/EBAY_CLIENT_SECRET to "
            "resell-autopilot/.env, or use --source csv with an exported file."
        )

    max_price = max_price if max_price is not None else float(cfg.get("business.max_buy_price_gbp", 25.0))
    found: dict[str, Candidate] = {}
    for query in queries:
        try:
            for candidate in client.search_candidates(query, category=category, max_price=max_price):
                found.setdefault(candidate.sku, candidate)
        except Exception as exc:
            print(f"[source] query '{query}' failed: {exc}")
    return list(found.values())


def gather_from_files(
    paths: Iterable[str | Path], *, category: str, cfg=None
) -> tuple[list[Candidate], list[str]]:
    candidates: list[Candidate] = []
    skips: list[str] = []
    for path in paths:
        path = Path(path)
        if not path.exists():
            skips.append(f"{path}: not found")
            continue
        if path.suffix.lower() != ".csv":
            skips.append(f"{path.name}: not a CSV — candidate import needs CSV")
            continue
        loaded, messages = load_candidates_csv_safe(path, category)
        candidates.extend(loaded)
        skips.extend(messages)
    return candidates, skips


def load_candidates_csv_safe(path: Path, category: str) -> tuple[list[Candidate], list[str]]:
    try:
        return tabular.load_candidates_csv(path, default_category=category)
    except Exception as exc:
        return [], [f"{path.name}: could not be read ({exc})"]


def inbox_csvs(cfg=None) -> list[Path]:
    cfg = cfg or cfg_mod.load()
    inbox = cfg.path("inbox")
    return sorted(inbox.glob("*.csv")) if inbox.exists() else []


# ---------------------------------------------------------------------------
# Analysis pass
# ---------------------------------------------------------------------------


def analyse_candidates(
    candidates: Sequence[Candidate],
    resolver: CompsResolver,
    *,
    cfg=None,
    channel: str | None = None,
) -> tuple[list[Analysis], list[str]]:
    """Price every candidate. Returns (analyses, error_messages).

    One bad candidate never stops the run — it's logged and skipped.
    """
    cfg = cfg or cfg_mod.load()
    analyses: list[Analysis] = []
    errors: list[str] = []

    for candidate in candidates:
        try:
            blocked = listings_mod.check_blocked(candidate, cfg)
            if blocked:
                # Still produce a row so the block is visible in the ledger.
                comps = Comps(query=candidate.search_phrase, source="none")
                comps.confidence = "none"
                analyses.append(
                    pricing_mod.analyse(candidate, comps, channel=channel, cfg=cfg, blocked_reason=blocked)
                )
                continue

            comps = resolver.resolve(candidate.search_phrase, candidate.category)
            if channel:
                analyses.append(pricing_mod.analyse(candidate, comps, channel=channel, cfg=cfg))
            else:
                analyses.append(pricing_mod.best_channel(candidate, comps, cfg=cfg))
        except Exception as exc:
            errors.append(f"{candidate.sku} '{candidate.title[:40]}': {exc}")
            if cfg_mod.env("AUTOPILOT_DEBUG"):
                traceback.print_exc()

    return analyses, errors


def rank(analyses: Sequence[Analysis], *, top: int | None = None) -> list[Analysis]:
    """BUY first, then REVIEW, each ordered by net profit. PASS/BLOCKED last."""
    order = {"BUY": 0, "REVIEW": 1, "PASS": 2, "BLOCKED": 3}
    ranked = sorted(
        analyses,
        key=lambda a: (order.get(a.verdict.action, 9), -a.economics.net_profit),
    )
    return ranked[:top] if top else ranked


def infer_attributes(candidate: Candidate, cfg=None) -> Candidate:
    """Best-effort brand/size extraction from a free-text title.

    Anything inferred is marked as `title_inferred` evidence, which the quality
    gate treats as unverified — so it gets hedged in the copy rather than
    stated as fact. Guessing is fine; guessing silently is not.
    """
    text = candidate.title

    if not candidate.brand:
        for brand in _KNOWN_BRANDS:
            if re.search(rf"\b{re.escape(brand)}\b", text, re.I):
                candidate.brand = brand
                candidate.evidence.setdefault("brand", "title_inferred")
                break

    if not candidate.size:
        match = re.search(r"\b(?:uk|size)\s*([0-9]{1,2}(?:\.5)?)\b", text, re.I)
        if match:
            candidate.size = match.group(1)
            candidate.evidence.setdefault("size", "title_inferred")
        else:
            match = re.search(r"\b(XS|S|M|L|XL|XXL)\b", text)
            if match:
                candidate.size = match.group(1)
                candidate.evidence.setdefault("size", "title_inferred")

    if candidate.category == "unknown":
        lowered = text.lower()
        for keyword, category in _CATEGORY_HINTS:
            if keyword in lowered:
                candidate.category = category
                break

    return candidate


# Words that carry no matching signal — every listing has some of them.
_STOPWORDS = {
    "the", "and", "for", "with", "size", "mens", "womens", "men", "women",
    "used", "new", "vintage", "genuine", "original", "uk", "eu", "rare",
}


def _significant_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if len(t) >= 3 and t not in _STOPWORDS]


def _token_weight(token: str) -> float:
    """Model numbers identify a product; adjectives don't.

    "700" or "fx290" pins down exactly what something is, while "north" and
    "face" are shared by every item a brand has ever made. Weighting the
    distinctive tokens higher is what lets "TNF Nuptse 700" match a listing
    titled "The North Face Nuptse 700" despite the abbreviation.
    """
    return 2.0 if any(ch.isdigit() for ch in token) else 1.0


def _is_comparable(query: str, comp_title: str, threshold: float = 0.6) -> bool:
    """Is this sold listing actually the same kind of thing as our item?"""
    wanted = _significant_tokens(query)
    if not wanted:
        return True
    haystack = set(_significant_tokens(comp_title))
    if not haystack:
        return False
    total = sum(_token_weight(token) for token in wanted)
    hits = sum(_token_weight(token) for token in wanted if token in haystack)
    return hits / total >= threshold if total else False


_KNOWN_BRANDS = [
    "Arc'teryx", "Arcteryx", "Stone Island", "Patagonia", "Barbour", "Carhartt",
    "The North Face", "North Face", "Ralph Lauren", "Berghaus", "Levi's", "Levis",
    "Nike", "Adidas", "New Balance", "Reebok", "Puma", "Asics", "Salomon",
    "Technics", "Sansui", "Marantz", "Pioneer", "Rotel", "Sony", "Denon",
    "Yamaha", "Sennheiser", "Wharfedale", "Mission", "KEF", "Canon", "Olympus",
    "Pentax", "Nikon",
]

_CATEGORY_HINTS = [
    ("trainer", "trainers"), ("sneaker", "trainers"), ("air max", "trainers"),
    ("jacket", "clothing"), ("fleece", "clothing"), ("hoodie", "clothing"),
    ("jumper", "clothing"), ("coat", "clothing"), ("jeans", "clothing"),
    ("amplifier", "audio"), ("speaker", "audio"), ("turntable", "audio"),
    ("headphone", "audio"), ("receiver", "audio"), ("hifi", "audio"),
    ("walkman", "retro_tech"), ("camera", "retro_tech"), ("console", "retro_tech"),
    ("game boy", "retro_tech"), ("nintendo", "retro_tech"),
]
