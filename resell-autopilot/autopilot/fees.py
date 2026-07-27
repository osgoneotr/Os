"""Fees, postage and packaging — the costs between a sale price and your money.

Every number comes from config/settings.yaml so you can correct it without
touching code when a platform changes its rates.
"""

from __future__ import annotations

from . import config as cfg_mod
from .models import Candidate, Economics

# Rough weights (kg) used when an item has no weight recorded, so we can pick
# a postage band. Erring heavy is the safe direction — it under-states profit.
_CATEGORY_WEIGHT_KG = {
    "trainers": 1.4,
    "clothing": 0.6,
    "retro_tech": 1.2,
    "audio": 3.0,
    "unknown": 1.5,
}


def platform_fee(channel: str, list_price: float, cfg=None) -> tuple[float, str]:
    """(fee_in_gbp, human_readable_explanation)"""
    cfg = cfg or cfg_mod.load()
    ch = cfg.channel(channel)

    # eBay splits its fee model by seller type.
    if "seller_type" in ch:
        seller_type = ch.get("seller_type", "private")
        block = ch.get(seller_type) or {}
        pct = float(block.get("seller_fee_pct", 0.0))
        fixed = float(block.get("seller_fee_fixed_gbp", 0.0))
        label = f"{channel} ({seller_type})"
    else:
        pct = float(ch.get("seller_fee_pct", 0.0))
        fixed = float(ch.get("seller_fee_fixed_gbp", 0.0))
        label = channel

    fee = round(list_price * pct / 100.0 + fixed, 2)
    if fee == 0:
        return 0.0, f"{label}: no seller fee (buyer pays the platform's buyer-protection fee)"
    return fee, f"{label}: {pct}% + £{fixed:.2f} = £{fee:.2f}"


def postage_band_for(candidate: Candidate, cfg=None) -> str:
    """Pick the cheapest postage band the item actually fits in."""
    cfg = cfg or cfg_mod.load()
    bands: dict = cfg.get("postage.bands", {})

    weight = candidate.weight_kg
    if weight is None:
        default_map = cfg.get("postage.default_band_by_category", {})
        named = default_map.get(candidate.category)
        if named and named in bands:
            return named
        weight = _CATEGORY_WEIGHT_KG.get(candidate.category, _CATEGORY_WEIGHT_KG["unknown"])

    fitting = sorted(
        ((name, spec) for name, spec in bands.items() if float(spec.get("max_kg", 0)) >= weight),
        key=lambda pair: float(pair[1].get("max_kg", 0)),
    )
    if fitting:
        return fitting[0][0]
    # Heavier than every band we priced — use the biggest and flag it upstream.
    return max(bands, key=lambda n: float(bands[n].get("max_kg", 0))) if bands else "unknown"


def postage_cost(channel: str, band: str, cfg=None) -> tuple[float, str]:
    """What POSTING the item costs you on this channel.

    Vinted: the buyer pays the courier, so it's £0 to you.
    Facebook local pickup: no postage at all.
    eBay: depends on `postage_charged_covers_cost` — see settings.yaml.
    """
    cfg = cfg or cfg_mod.load()
    ch = cfg.channel(channel)
    paid_by = ch.get("postage_paid_by", "seller")

    spec = (cfg.get("postage.bands", {}) or {}).get(band, {})
    raw_cost = round(float(spec.get("cost_gbp", 0.0)), 2)
    desc = spec.get("desc", band)

    if paid_by == "none":
        return 0.0, f"{channel}: local collection, no postage"

    if paid_by == "buyer":
        # Vinted books and pays the courier itself — it never touches your money.
        if ch.get("buyer_pays_courier_directly"):
            return 0.0, f"{channel}: buyer pays the courier directly"
        # Elsewhere the buyer only pays what you charge them. Whether that
        # actually covers the label is a listing decision, hence the flag.
        if ch.get("postage_charged_covers_cost"):
            return 0.0, f"{channel}: postage charged to buyer covers {desc} (£{raw_cost:.2f})"

    return raw_cost, f"{channel}: you absorb {desc} = £{raw_cost:.2f}"


def packaging_cost(candidate: Candidate, channel: str, cfg=None) -> tuple[float, str]:
    cfg = cfg or cfg_mod.load()
    if cfg.channel(channel).get("postage_paid_by") == "none":
        return 0.0, "no packaging needed for local collection"
    costs = cfg.get("postage.packaging_cost_gbp", {})
    # Trainers and audio go in a box; clothing goes in a bag.
    use_box = candidate.category in ("trainers", "audio", "retro_tech")
    key = "box" if use_box else "bag"
    value = round(float(costs.get(key, 0.0)), 2)
    return value, f"packaging ({key}) = £{value:.2f}"


def compute_economics(
    candidate: Candidate,
    channel: str,
    list_price: float,
    *,
    buy_price: float | None = None,
    min_price: float | None = None,
    cfg=None,
) -> Economics:
    """Full cost stack for selling one item on one channel."""
    cfg = cfg or cfg_mod.load()
    buy = round(float(buy_price if buy_price is not None else candidate.ask_price), 2)
    list_price = round(float(list_price), 2)

    fee, fee_note = platform_fee(channel, list_price, cfg)
    band = postage_band_for(candidate, cfg)
    post, post_note = postage_cost(channel, band, cfg)
    pack, pack_note = packaging_cost(candidate, channel, cfg)

    net_proceeds = round(list_price - fee - post - pack, 2)
    net_profit = round(net_proceeds - buy, 2)
    margin = round(net_profit / list_price * 100, 1) if list_price > 0 else 0.0
    roi = round(net_profit / buy * 100, 1) if buy > 0 else float("inf") if net_profit > 0 else 0.0

    if min_price is None:
        fraction = float(cfg.get("pricing.min_price_fraction", 0.72))
        min_price = round(list_price * fraction, 2)

    return Economics(
        channel=channel,
        buy_price=buy,
        list_price=list_price,
        min_price=round(float(min_price), 2),
        platform_fee=fee,
        postage_cost=post,
        packaging_cost=pack,
        postage_band=band,
        net_proceeds=net_proceeds,
        net_profit=net_profit,
        margin_pct=margin,
        roi_pct=roi if roi != float("inf") else 999.9,
        breakdown=[
            f"list £{list_price:.2f}",
            fee_note,
            post_note,
            pack_note,
            f"buy £{buy:.2f}",
            f"-> net £{net_profit:.2f} ({margin:.1f}% margin, {roi if roi != float('inf') else 999.9:.0f}% ROI)",
        ],
    )
