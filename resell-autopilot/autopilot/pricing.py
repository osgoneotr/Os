"""Comps -> estimated resale price -> profit -> BUY / PASS / REVIEW.

The one rule this module enforces everywhere: never let a confident-looking
number come out of thin evidence. Every price carries its sample size, its
source, and a confidence label, and the verdict downgrades itself to REVIEW
rather than BUY when the evidence is thin.
"""

from __future__ import annotations

import statistics
from typing import Sequence

from . import config as cfg_mod
from . import fees as fees_mod
from .models import Analysis, Candidate, Comps, Economics, Verdict

# Categories that historically sit longer before selling. Used for risk notes,
# not to change the maths.
SLOW_CATEGORIES = {"audio", "clothing"}


# ---------------------------------------------------------------------------
# Comps
# ---------------------------------------------------------------------------


def build_comps(
    query: str,
    prices: Sequence[float],
    *,
    source: str,
    category: str = "unknown",
    sample_urls: Sequence[str] | None = None,
    cfg=None,
) -> Comps:
    """Turn a raw price list into a trimmed, outlier-guarded distribution.

    `source` drives calibration: active eBay listings are asking prices and get
    multiplied down to an estimated sold price; sold data is used as-is.
    """
    cfg = cfg or cfg_mod.load()
    comps = Comps(query=query, source=source, sample_urls=list(sample_urls or []))

    clean = sorted(float(p) for p in prices if p and float(p) > 0)
    if not clean:
        comps.confidence = "none"
        comps.warnings.append(f"No usable price comps found for '{query}'.")
        return comps

    # 1. Outlier guard against the raw median — one £900 listing among £20 items
    #    would otherwise drag a trimmed mean upward.
    raw_median = statistics.median(clean)
    lo_mult = float(cfg.get("pricing.outlier_low_multiple", 0.33))
    hi_mult = float(cfg.get("pricing.outlier_high_multiple", 3.0))
    kept = [p for p in clean if raw_median * lo_mult <= p <= raw_median * hi_mult]
    dropped = len(clean) - len(kept)
    if dropped:
        comps.warnings.append(f"Dropped {dropped} outlier comp(s) outside {lo_mult}x–{hi_mult}x median.")
    if not kept:
        kept = clean

    # 2. Trim the tails before averaging.
    trim = float(cfg.get("pricing.trim_fraction", 0.10))
    cut = int(len(kept) * trim)
    trimmed = kept[cut : len(kept) - cut] if len(kept) - 2 * cut >= 1 else kept

    # 3. Calibrate asking prices down to estimated sold prices.
    calibration = 1.0
    if source == "ebay_browse_active":
        ratios = cfg.get("pricing.active_to_sold_ratio", {}) or {}
        calibration = float(ratios.get(category, ratios.get("default", 0.78)))
        comps.warnings.append(
            f"Comps are ACTIVE listings (asking prices), scaled by {calibration:.2f} to "
            "approximate sold prices. Treat as an estimate — use a sold-listings "
            "export for a firm number."
        )

    scaled = [round(p * calibration, 2) for p in trimmed]
    comps.prices = scaled
    comps.calibration_applied = calibration
    comps.n = len(scaled)
    comps.low = min(scaled)
    comps.high = max(scaled)
    comps.median = round(statistics.median(scaled), 2)
    comps.trimmed_mean = round(statistics.fmean(scaled), 2)
    comps.confidence = _confidence(comps, source, cfg)
    return comps


def _confidence(comps: Comps, source: str, cfg) -> str:
    high_n = int(cfg.get("pricing.min_comps_high_confidence", 8))
    med_n = int(cfg.get("pricing.min_comps_medium_confidence", 4))

    if comps.n >= high_n:
        level = "high"
    elif comps.n >= med_n:
        level = "medium"
    else:
        level = "low"

    # Asking-price data never earns "high", however many samples there are.
    if source == "ebay_browse_active" and level == "high":
        level = "medium"

    # A distribution this wide isn't one product — it's a bad search phrase.
    if comps.low > 0 and comps.high / comps.low > 4 and level != "low":
        comps.warnings.append(
            f"Comp spread is wide (£{comps.low:.2f}–£{comps.high:.2f}); the search phrase "
            "may be matching more than one product. Confidence downgraded."
        )
        level = "low" if level == "medium" else "medium"

    return level


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile. q in [0,1]."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    position = q * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 2)


# ---------------------------------------------------------------------------
# Resale price
# ---------------------------------------------------------------------------


def estimate_resale(
    comps: Comps, candidate: Candidate, *, channel: str | None = None, cfg=None
) -> tuple[float, float, float]:
    """(list_price, resale_low, resale_high) adjusted for condition and channel."""
    cfg = cfg or cfg_mod.load()
    if not comps.is_usable:
        return 0.0, 0.0, 0.0

    multipliers = cfg.get("pricing.condition_multiplier", {}) or {}
    # Unknown condition is priced as "good" — the honest middle, not the best case.
    mult = float(multipliers.get(candidate.condition, multipliers.get("good", 0.88)))

    # Comps come from eBay, so a channel with a cheaper audience needs adjusting
    # down. Otherwise Facebook looks the most profitable purely because it has
    # no fees, while the item never actually sells at that price.
    if channel:
        mult *= float(cfg.get(f"channels.{channel}.price_multiplier", 1.0))

    p25 = percentile(comps.prices, 0.25)
    p50 = percentile(comps.prices, 0.50)
    p75 = percentile(comps.prices, 0.75)

    target = float(cfg.get("pricing.list_price_percentile", 0.62))
    base = percentile(comps.prices, target) if 0 <= target <= 1 else p50
    # Never let the target percentile land below the median-minus-a-bit.
    base = max(base, p50 * 0.9)

    return (
        round(base * mult, 2),
        round(p25 * mult, 2),
        round(p75 * mult, 2),
    )


def round_price(value: float, channel: str) -> float:
    """Round to a price shape buyers expect on that platform.

    Vinted users list round numbers; eBay is a .99 culture.
    """
    if value <= 0:
        return 0.0
    whole = round(value)
    if channel == "ebay_uk" and whole >= 10:
        return round(whole - 0.01, 2)
    if channel == "vinted":
        return float(max(1, whole))
    return float(whole)


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def decide(
    candidate: Candidate,
    comps: Comps,
    economics: Economics,
    *,
    profit_low: float,
    profit_high: float,
    cfg=None,
    blocked_reason: str | None = None,
) -> Verdict:
    """Apply the buy rule. Returns BUY / PASS / REVIEW / BLOCKED with reasons."""
    cfg = cfg or cfg_mod.load()
    reasons: list[str] = []
    risks: list[str] = []

    if blocked_reason:
        return Verdict(
            action="BLOCKED",
            reasons=[blocked_reason],
            risks=["Item matched the safety blocklist — do not buy or list it."],
            justification=blocked_reason,
        )

    min_profit = float(cfg.get("thresholds.min_net_profit_gbp", 8.0))
    min_margin = float(cfg.get("thresholds.min_margin_pct", 35.0))
    min_roi = float(cfg.get("thresholds.min_roi_pct", 100.0))
    min_comps = int(cfg.get("thresholds.min_comps_for_auto_buy", 5))
    max_buy = float(cfg.get("business.max_buy_price_gbp", 25.0))

    # Risk notes are advisory — they never flip a BUY to a PASS on their own,
    # but they're attached to every verdict, so they're built first.
    if comps.confidence in ("low", "none"):
        risks.append(f"Price confidence is {comps.confidence} (n={comps.n}) — verify before buying.")
    if comps.source == "ebay_browse_active":
        risks.append("Comps are asking prices, not sold prices; real sale price may be lower.")
    if candidate.condition == "unknown":
        risks.append("Condition not recorded — inspect in person; priced as 'good' by default.")
    if candidate.condition == "for_parts":
        risks.append("Spares/repair items sell slowly and attract 'not as described' claims.")
    if candidate.category in SLOW_CATEGORIES:
        risks.append(
            f"'{candidate.category}' is a slower-moving category — expect to sit near the "
            f"{cfg.get('thresholds.target_sell_through_days', 45)}-day sell-through target."
        )
    if candidate.category == "trainers":
        risks.append("Trainers: size drives demand and fakes are common — check stitching, size tag and box label.")
    if candidate.category in ("retro_tech", "audio") and not candidate.tested_confirmed:
        risks.append("Untested electronics: don't claim 'fully working'. Test it or list as untested.")
    if economics.net_profit > 0 and economics.net_profit < min_profit * 1.5:
        risks.append("Thin absolute profit — one return or a lost parcel wipes out the trade.")

    # Hard stops next: these hold regardless of what the comps say.
    failures: list[str] = []
    floor_brand = _no_resale_floor(candidate, cfg)
    if floor_brand:
        failures.append(
            f"'{floor_brand}' has no resale floor — postage and your time cost more than "
            "it will fetch, whatever the comps say"
        )
    if economics.buy_price > max_buy:
        failures.append(f"buy price £{economics.buy_price:.2f} exceeds your £{max_buy:.2f} per-item cap")

    if failures:
        return Verdict(
            action="PASS",
            reasons=failures,
            risks=risks,
            profit_low=profit_low,
            profit_high=profit_high,
            justification=_justify("PASS", candidate, comps, economics, failures),
        )

    # No evidence is not the same as bad economics. Without comps every downstream
    # number is zero, and reporting that as "PASS — margin 0%" would be a lie about
    # the item. It's an unknown, and unknowns go to you.
    if not comps.is_usable:
        reasons = [
            "No price evidence for this item, so it can't be judged yet.",
            "Get comps first: search the item on eBay, tick 'Sold items', then Ctrl+S the "
            "results page into data/inbox/comps/ named after the product. Re-run and it "
            "will be picked up automatically.",
        ]
        return Verdict(
            action="REVIEW",
            reasons=reasons,
            risks=risks,
            profit_low=0.0,
            profit_high=0.0,
            justification=_justify("REVIEW", candidate, comps, economics, reasons),
        )

    if economics.net_profit < min_profit:
        failures.append(f"net profit £{economics.net_profit:.2f} below the £{min_profit:.2f} floor")
    if economics.margin_pct < min_margin:
        failures.append(f"margin {economics.margin_pct:.1f}% below the {min_margin:.0f}% minimum")
    if economics.roi_pct < min_roi:
        failures.append(f"ROI {economics.roi_pct:.0f}% below the {min_roi:.0f}% minimum")

    if failures:
        return Verdict(
            action="PASS",
            reasons=failures,
            risks=risks,
            profit_low=profit_low,
            profit_high=profit_high,
            justification=_justify("PASS", candidate, comps, economics, failures),
        )

    if comps.n < min_comps:
        reasons.append(
            f"Numbers clear every threshold, but only {comps.n} comp(s) support them "
            f"(need {min_comps} to auto-approve)."
        )
        return Verdict(
            action="REVIEW",
            reasons=reasons,
            risks=risks,
            profit_low=profit_low,
            profit_high=profit_high,
            justification=_justify("REVIEW", candidate, comps, economics, reasons),
        )

    reasons = [
        f"Net profit £{economics.net_profit:.2f} on a £{economics.buy_price:.2f} buy "
        f"({economics.margin_pct:.1f}% margin, {economics.roi_pct:.0f}% ROI).",
        f"{comps.n} comps, {comps.confidence} confidence, median £{comps.median:.2f}.",
    ]
    return Verdict(
        action="BUY",
        reasons=reasons,
        risks=risks,
        profit_low=profit_low,
        profit_high=profit_high,
        justification=_justify("BUY", candidate, comps, economics, reasons),
    )


def _no_resale_floor(candidate: Candidate, cfg) -> str | None:
    """Fast-fashion own-brands, which never clear the cost of posting them."""
    haystack = f"{candidate.brand} {candidate.title}".lower()
    for brand in cfg.get("safety.no_resale_floor_brands", []) or []:
        brand = str(brand).lower().strip()
        if brand and brand in haystack:
            return brand
    return None


def _justify(action, candidate, comps, economics, points) -> str:
    head = {
        "BUY": "Buy it.",
        "PASS": "Leave it.",
        "REVIEW": "Worth a look, but check first.",
        "BLOCKED": "Do not touch this one.",
    }[action]
    label = candidate.search_phrase or candidate.title
    body = points[0] if points else ""
    if body and body[-1] not in ".!?":
        body += "."
    tail = (
        f"Comps put it around £{comps.median:.2f}; listing at £{economics.list_price:.2f} on "
        f"{economics.channel} leaves £{economics.net_profit:.2f} after fees and postage."
        if comps.is_usable
        else "There isn't enough market evidence to price it yet."
    )
    return f"{head} {label}: {body} {tail}".strip()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def analyse(
    candidate: Candidate,
    comps: Comps,
    *,
    channel: str | None = None,
    buy_price: float | None = None,
    cfg=None,
    blocked_reason: str | None = None,
) -> Analysis:
    """Price one candidate on one channel and return the full analysis."""
    cfg = cfg or cfg_mod.load()
    channel = channel or (cfg.sell_channels() or ["vinted"])[0]

    raw_list, resale_low, resale_high = estimate_resale(comps, candidate, channel=channel, cfg=cfg)
    list_price = round_price(raw_list, channel)

    economics = fees_mod.compute_economics(
        candidate, channel, list_price, buy_price=buy_price, cfg=cfg
    )

    # Profit range: rerun the cost stack at the low and high ends of the comps.
    profit_low = fees_mod.compute_economics(
        candidate, channel, round_price(resale_low, channel), buy_price=buy_price, cfg=cfg
    ).net_profit
    profit_high = fees_mod.compute_economics(
        candidate, channel, round_price(resale_high, channel), buy_price=buy_price, cfg=cfg
    ).net_profit

    verdict = decide(
        candidate,
        comps,
        economics,
        profit_low=profit_low,
        profit_high=profit_high,
        cfg=cfg,
        blocked_reason=blocked_reason,
    )

    return Analysis(
        candidate=candidate,
        comps=comps,
        economics=economics,
        verdict=verdict,
        est_resale_low=resale_low,
        est_resale_high=resale_high,
    )


def best_channel(
    candidate: Candidate, comps: Comps, *, buy_price: float | None = None, cfg=None
) -> Analysis:
    """Pick a channel, respecting your stated preference order.

    Not simply "highest net profit": your first-choice channel wins ties and
    near-ties, and a lower-priority channel only displaces it by beating it by
    `channels.switch_threshold_pct`. Profit-per-item isn't the only cost — a
    Facebook sale means arranging to meet someone, and that's worth something.
    """
    cfg = cfg or cfg_mod.load()
    channels = cfg.sell_channels()
    if not channels:
        raise RuntimeError("No sell channels enabled in config/settings.yaml")

    incumbent = analyse(candidate, comps, channel=channels[0], buy_price=buy_price, cfg=cfg)
    if not comps.is_usable:
        # Every channel scores zero without comps; comparing them would just
        # pick whichever has the lowest fixed costs and present it as a choice.
        return incumbent

    threshold = 1.0 + float(cfg.get("channels.switch_threshold_pct", 15)) / 100.0

    for channel in channels[1:]:
        challenger = analyse(candidate, comps, channel=channel, buy_price=buy_price, cfg=cfg)
        incumbent_profit = incumbent.economics.net_profit
        # A negative incumbent can't be beaten by a percentage, so compare directly.
        beats = (
            challenger.economics.net_profit > incumbent_profit * threshold
            if incumbent_profit > 0
            else challenger.economics.net_profit > incumbent_profit
        )
        if beats:
            incumbent = challenger

    return incumbent
