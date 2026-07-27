"""Core data structures + the canonical CSV schema.

Everything in the pipeline moves between these four shapes:

    Candidate  -> something we might buy (from a marketplace or a CSV)
    Comps      -> what the market says similar items go for
    Economics  -> the money maths for one (candidate, channel) pair
    Verdict    -> BUY / PASS / REVIEW plus the reasons why

ListingPackage is the final artefact: marketplace-ready copy for one item.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Literal

Action = Literal["BUY", "PASS", "REVIEW", "BLOCKED"]
Confidence = Literal["high", "medium", "low", "none"]

CONDITIONS = (
    "new_with_tags",
    "new_without_tags",
    "excellent",
    "good",
    "fair",
    "for_parts",
    "unknown",
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def make_sku(source: str, source_id: str, title: str = "") -> str:
    """Stable, human-readable SKU. Same input always yields the same SKU, so
    re-running the sourcing script never creates duplicate ledger rows."""
    digest = hashlib.sha1(f"{source}|{source_id}|{title}".encode("utf-8")).hexdigest()[:6]
    prefix = re.sub(r"[^A-Z]", "", source.upper())[:3] or "ITM"
    return f"{prefix}-{digest.upper()}"


@dataclass
class Candidate:
    """One item we might buy."""

    title: str
    ask_price: float
    source: str = "manual"            # ebay_browse | csv | facebook | gumtree | manual
    source_id: str = ""
    url: str = ""
    category: str = "unknown"         # trainers | clothing | retro_tech | audio
    brand: str = ""
    model: str = ""
    size: str = ""
    colour: str = ""
    material: str = ""
    condition: str = "unknown"
    condition_note: str = ""
    defects: str = ""
    location: str = ""
    seller: str = ""
    photos: list[str] = field(default_factory=list)
    weight_kg: float | None = None
    tested_confirmed: bool = False    # did WE plug it in / try it on?
    evidence: dict[str, str] = field(default_factory=dict)  # attribute -> where it came from
    notes: str = ""
    seen_at: str = field(default_factory=_now)
    sku: str = ""

    def __post_init__(self) -> None:
        if not self.sku:
            self.sku = make_sku(self.source, self.source_id or self.url or self.title, self.title)
        if self.condition not in CONDITIONS:
            self.condition = "unknown"
        self.ask_price = round(float(self.ask_price or 0.0), 2)

    @property
    def search_phrase(self) -> str:
        """What we'd type into eBay to find comparable items."""
        parts = [self.brand, self.model]
        if not any(parts):
            return self.title
        if self.size and self.category in ("trainers", "clothing"):
            parts.append(f"size {self.size}")
        return " ".join(p for p in parts if p).strip()

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class Comps:
    """Market evidence for one search phrase."""

    query: str
    prices: list[float] = field(default_factory=list)
    source: str = "none"              # ebay_sold_csv | ebay_browse_active | manual | none
    calibration_applied: float = 1.0  # active->sold adjustment, 1.0 = none
    sample_urls: list[str] = field(default_factory=list)
    computed_at: str = field(default_factory=_now)
    # Filled by pricing.build_comps()
    n: int = 0
    low: float = 0.0
    median: float = 0.0
    high: float = 0.0
    trimmed_mean: float = 0.0
    confidence: Confidence = "none"
    warnings: list[str] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        return self.n > 0 and self.trimmed_mean > 0


@dataclass
class Economics:
    """Money maths for selling one item on one channel."""

    channel: str
    buy_price: float
    list_price: float
    min_price: float
    platform_fee: float
    postage_cost: float
    packaging_cost: float
    postage_band: str
    net_proceeds: float       # what lands in your account after fees & postage
    net_profit: float         # net_proceeds - buy_price
    margin_pct: float         # net_profit / list_price
    roi_pct: float            # net_profit / buy_price
    breakdown: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class Verdict:
    action: Action
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    profit_low: float = 0.0
    profit_high: float = 0.0
    justification: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class Analysis:
    """Everything we know about one candidate after pricing."""

    candidate: Candidate
    comps: Comps
    economics: Economics
    verdict: Verdict
    est_resale_low: float = 0.0
    est_resale_high: float = 0.0


@dataclass
class ListingPackage:
    """Marketplace-ready copy for one item on one channel."""

    sku: str
    channel: str
    title_seo: str
    title_style: str
    description: str
    keywords: list[str]
    list_price: float
    min_price: float
    category_hint: str = ""
    quality_gate: str = "pass"          # pass | pass_with_warnings | fail
    warnings: list[str] = field(default_factory=list)
    stripped_claims: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Canonical ledger schema — the one table everything reads from and writes to.
# Column order here is the column order in data/ledger.csv.
# ---------------------------------------------------------------------------

LEDGER_COLUMNS: list[str] = [
    "sku",
    "status",              # candidate | approved | bought | listed | sold | dead
    "sourced_at",
    "category",
    "brand",
    "model",
    "size",
    "colour",
    "material",
    "condition",
    "condition_note",
    "defects",
    "title_seo",
    "title_style",
    "description",
    "keywords",
    "channel",
    "buy_price",
    "list_price",
    "min_price",
    "est_resale_low",
    "est_resale_high",
    "est_net_profit",
    "margin_pct",
    "roi_pct",
    "platform_fee",
    "postage_cost",
    "packaging_cost",
    "postage_band",
    "location",
    "image_refs",
    "source",
    "source_url",
    "comps_source",
    "comps_n",
    "comps_median",
    "confidence",
    "verdict",
    "verdict_reasons",
    "risks",
    "quality_gate",
    "warnings",
    "listed_at",
    "sold_at",
    "sold_price",
    "actual_postage",
    "actual_fees",
    "actual_profit",
    "notes",
]

STATUSES = ("candidate", "approved", "bought", "listed", "sold", "dead")


def blank_row() -> dict[str, str]:
    return {col: "" for col in LEDGER_COLUMNS}


def analysis_to_row(analysis: Analysis, package: ListingPackage | None = None) -> dict[str, str]:
    """Flatten an Analysis (+ optional listing copy) into a ledger row."""
    c, comps, econ, v = analysis.candidate, analysis.comps, analysis.economics, analysis.verdict
    row = blank_row()
    row.update(
        {
            "sku": c.sku,
            "status": "candidate",
            "sourced_at": c.seen_at,
            "category": c.category,
            "brand": c.brand,
            "model": c.model,
            "size": c.size,
            "colour": c.colour,
            "material": c.material,
            "condition": c.condition,
            "condition_note": c.condition_note,
            "defects": c.defects,
            "channel": econ.channel,
            "buy_price": f"{econ.buy_price:.2f}",
            "list_price": f"{econ.list_price:.2f}",
            "min_price": f"{econ.min_price:.2f}",
            "est_resale_low": f"{analysis.est_resale_low:.2f}",
            "est_resale_high": f"{analysis.est_resale_high:.2f}",
            "est_net_profit": f"{econ.net_profit:.2f}",
            "margin_pct": f"{econ.margin_pct:.1f}",
            "roi_pct": f"{econ.roi_pct:.1f}",
            "platform_fee": f"{econ.platform_fee:.2f}",
            "postage_cost": f"{econ.postage_cost:.2f}",
            "packaging_cost": f"{econ.packaging_cost:.2f}",
            "postage_band": econ.postage_band,
            "location": c.location,
            "image_refs": " | ".join(c.photos),
            "source": c.source,
            "source_url": c.url,
            "comps_source": comps.source,
            "comps_n": str(comps.n),
            "comps_median": f"{comps.median:.2f}",
            "confidence": comps.confidence,
            "verdict": v.action,
            "verdict_reasons": " | ".join(v.reasons),
            "risks": " | ".join(v.risks),
            "notes": c.notes,
        }
    )
    if package:
        row.update(
            {
                "title_seo": package.title_seo,
                "title_style": package.title_style,
                "description": package.description,
                "keywords": ", ".join(package.keywords),
                "quality_gate": package.quality_gate,
                "warnings": " | ".join(package.warnings),
            }
        )
    return row
