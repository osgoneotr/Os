"""Dashboard metrics and the weekly report.

Reads the ledger, computes the handful of numbers that actually change your
decisions, and prints them. Also writes a `dashboard.csv` you can paste into
Google Sheets if you'd rather look at it there.

The numbers that matter at this scale, in order:
  1. Realised profit (not estimated — what actually landed)
  2. Sell-through rate and days-to-sell (dead stock is the real killer)
  3. Profit per category (tells you where to spend Saturday morning)
  4. Estimate accuracy (are the comps lying to you?)
"""

from __future__ import annotations

import csv
import datetime as dt
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Sequence

from . import config as cfg_mod
from . import ledger as ledger_mod


def _money(raw) -> float:
    try:
        return float(str(raw or "0").replace("£", "").replace(",", "") or 0)
    except ValueError:
        return 0.0


def _date(raw) -> dt.date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(text[:19] if "T" in text else text, fmt).date()
        except ValueError:
            continue
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def realised_profit(row: dict) -> float:
    """Actual profit if recorded, otherwise reconstructed from what we know."""
    actual = _money(row.get("actual_profit"))
    if actual:
        return actual
    sold = _money(row.get("sold_price"))
    if not sold:
        return 0.0
    costs = (
        _money(row.get("buy_price"))
        + (_money(row.get("actual_fees")) or _money(row.get("platform_fee")))
        + (_money(row.get("actual_postage")) or _money(row.get("postage_cost")))
        + _money(row.get("packaging_cost"))
    )
    return round(sold - costs, 2)


def summarise(rows: Sequence[dict], cfg=None, today: dt.date | None = None) -> dict:
    cfg = cfg or cfg_mod.load()
    today = today or dt.date.today()

    sold = ledger_mod.by_status(rows, "sold")
    listed = ledger_mod.by_status(rows, "listed")
    bought = ledger_mod.by_status(rows, "bought")
    candidates = ledger_mod.by_status(rows, "candidate")

    profits = [realised_profit(r) for r in sold]
    revenue = sum(_money(r.get("sold_price")) for r in sold)
    spend = sum(_money(r.get("buy_price")) for r in rows if r.get("status") in ("bought", "listed", "sold"))

    days_to_sell: list[int] = []
    for row in sold:
        listed_on, sold_on = _date(row.get("listed_at")), _date(row.get("sold_at"))
        if listed_on and sold_on and sold_on >= listed_on:
            days_to_sell.append((sold_on - listed_on).days)

    # Did our estimates match reality? A ratio well below 1.0 means the comps
    # (or the calibration factor) are optimistic and should be retuned.
    accuracy: list[float] = []
    for row in sold:
        estimated, actual = _money(row.get("est_net_profit")), realised_profit(row)
        if estimated > 0:
            accuracy.append(actual / estimated)

    listed_or_sold = len(listed) + len(sold)
    return {
        "as_of": today.isoformat(),
        "counts": {
            "candidates": len(candidates),
            "bought": len(bought),
            "listed": len(listed),
            "sold": len(sold),
            "dead": len(ledger_mod.by_status(rows, "dead")),
            "total": len(rows),
        },
        "money": {
            "revenue": round(revenue, 2),
            "spend_on_stock": round(spend, 2),
            "realised_profit": round(sum(profits), 2),
            "avg_profit_per_sale": round(statistics.fmean(profits), 2) if profits else 0.0,
            "capital_committed": ledger_mod.capital_committed(rows),
            "capital_headroom": ledger_mod.capital_headroom(rows, cfg),
        },
        "velocity": {
            "sell_through_pct": round(len(sold) / listed_or_sold * 100, 1) if listed_or_sold else 0.0,
            "median_days_to_sell": round(statistics.median(days_to_sell), 1) if days_to_sell else None,
            "target_days": cfg.get("thresholds.target_sell_through_days", 45),
        },
        "estimate_accuracy": round(statistics.fmean(accuracy), 2) if accuracy else None,
        "hmrc": _hmrc_status(revenue, len(sold), cfg),
    }


def _hmrc_status(revenue: float, sale_count: int, cfg) -> dict:
    """UK reality check, not tax advice.

    The £1,000 trading allowance is gross income, not profit. Separately, online
    platforms report seller data to HMRC once you pass roughly 30 sales or
    ~£1,700 in a year — that reporting happens whether or not you owe anything.
    """
    allowance = float(cfg.get("business.hmrc_trading_allowance_gbp", 1000.0))
    return {
        "gross_revenue": round(revenue, 2),
        "trading_allowance": allowance,
        "over_allowance": revenue > allowance,
        "sales_count": sale_count,
        "platform_reporting_likely": sale_count >= 30 or revenue >= 1700,
        "note": (
            "Over £1,000 gross in a tax year means registering for Self Assessment. "
            "Platforms report seller data to HMRC above ~30 sales or ~£1,700/yr. "
            "Keep the ledger — it is your records. This is a reminder, not tax advice."
        ),
    }


def top_categories(rows: Sequence[dict], limit: int = 10) -> list[dict]:
    """Categories ranked by realised profit, with sell-through alongside."""
    buckets: dict[str, dict] = defaultdict(
        lambda: {"category": "", "sold": 0, "listed": 0, "profit": 0.0, "spend": 0.0}
    )
    for row in rows:
        category = row.get("category") or "unknown"
        bucket = buckets[category]
        bucket["category"] = category
        status = row.get("status")
        if status == "sold":
            bucket["sold"] += 1
            bucket["profit"] += realised_profit(row)
            bucket["spend"] += _money(row.get("buy_price"))
        elif status == "listed":
            bucket["listed"] += 1
            bucket["spend"] += _money(row.get("buy_price"))

    out = []
    for bucket in buckets.values():
        denominator = bucket["sold"] + bucket["listed"]
        out.append(
            {
                **bucket,
                "profit": round(bucket["profit"], 2),
                "spend": round(bucket["spend"], 2),
                "roi_pct": round(bucket["profit"] / bucket["spend"] * 100, 1) if bucket["spend"] else 0.0,
                "sell_through_pct": round(bucket["sold"] / denominator * 100, 1) if denominator else 0.0,
            }
        )
    return sorted(out, key=lambda b: b["profit"], reverse=True)[:limit]


def stale_inventory(rows: Sequence[dict], cfg=None, today: dt.date | None = None) -> list[dict]:
    """Listed items older than `thresholds.stale_after_days`, oldest first."""
    cfg = cfg or cfg_mod.load()
    today = today or dt.date.today()
    limit_days = int(cfg.get("thresholds.stale_after_days", 60))

    stale = []
    for row in ledger_mod.by_status(rows, "listed"):
        listed_on = _date(row.get("listed_at")) or _date(row.get("sourced_at"))
        if not listed_on:
            continue
        age = (today - listed_on).days
        if age < limit_days:
            continue
        list_price = _money(row.get("list_price"))
        min_price = _money(row.get("min_price"))
        stale.append(
            {
                "sku": row.get("sku"),
                "title": row.get("title_seo") or row.get("title_style"),
                "category": row.get("category"),
                "days_listed": age,
                "buy_price": _money(row.get("buy_price")),
                "list_price": list_price,
                "min_price": min_price,
                "suggested_action": _stale_action(age, limit_days, list_price, min_price, row),
            }
        )
    return sorted(stale, key=lambda r: r["days_listed"], reverse=True)


def _stale_action(age: int, limit: int, list_price: float, min_price: float, row: dict) -> str:
    buy = _money(row.get("buy_price"))
    if age >= limit * 2:
        if min_price > buy:
            return f"Drop to £{min_price:.2f} (your floor) or bundle it. Still £{min_price - buy:.2f} up."
        return "Below your floor now — relist as part of a bundle or write it off as dead stock."
    cut = round(list_price * 0.9, 2)
    if cut > min_price:
        return f"Cut 10% to £{cut:.2f} and refresh the photos/title."
    return f"Cut to your floor of £{min_price:.2f}, or move it to another channel."


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def render_text(rows: Sequence[dict], cfg=None, today: dt.date | None = None) -> str:
    cfg = cfg or cfg_mod.load()
    s = summarise(rows, cfg, today)
    lines: list[str] = []

    lines.append(f"RESELL AUTOPILOT — weekly review, {s['as_of']}")
    lines.append("=" * 58)
    lines.append("")

    c, m, v = s["counts"], s["money"], s["velocity"]
    lines.append("PIPELINE")
    lines.append(
        f"  candidates {c['candidates']} | bought {c['bought']} | listed {c['listed']} "
        f"| sold {c['sold']} | dead {c['dead']}"
    )
    lines.append("")
    lines.append("MONEY")
    lines.append(f"  Revenue            £{m['revenue']:.2f}")
    lines.append(f"  Spent on stock     £{m['spend_on_stock']:.2f}")
    lines.append(f"  Realised profit    £{m['realised_profit']:.2f}")
    lines.append(f"  Avg profit / sale  £{m['avg_profit_per_sale']:.2f}")
    lines.append(f"  Capital committed  £{m['capital_committed']:.2f}")
    lines.append(f"  Headroom left      £{m['capital_headroom']:.2f}")
    lines.append("")
    lines.append("VELOCITY")
    days = v["median_days_to_sell"]
    lines.append(f"  Sell-through       {v['sell_through_pct']:.1f}%")
    lines.append(
        f"  Median days to sell {days if days is not None else 'n/a'} (target ≤{v['target_days']})"
    )
    if s["estimate_accuracy"] is not None:
        ratio = s["estimate_accuracy"]
        verdict = "on the money" if 0.9 <= ratio <= 1.1 else ("optimistic" if ratio < 0.9 else "conservative")
        lines.append(f"  Estimate accuracy   {ratio:.2f}x actual/estimated — estimates are {verdict}")
        if ratio < 0.9:
            lines.append(
                "    -> lower pricing.active_to_sold_ratio in settings.yaml, or switch to sold-comp CSVs"
            )
    lines.append("")

    categories = top_categories(rows)
    if categories:
        lines.append("CATEGORIES BY REALISED PROFIT")
        lines.append(f"  {'category':<14}{'sold':>5}{'profit':>10}{'ROI':>8}{'sell-thru':>11}")
        for bucket in categories:
            lines.append(
                f"  {bucket['category'][:13]:<14}{bucket['sold']:>5}"
                f"{('£%.2f' % bucket['profit']):>10}{('%.0f%%' % bucket['roi_pct']):>8}"
                f"{('%.0f%%' % bucket['sell_through_pct']):>11}"
            )
        lines.append("")

    stale = stale_inventory(rows, cfg, today)
    if stale:
        lines.append(f"STALE INVENTORY (listed >{cfg.get('thresholds.stale_after_days', 60)} days)")
        for item in stale[:10]:
            lines.append(f"  [{item['days_listed']}d] {item['sku']} {str(item['title'])[:44]}")
            lines.append(f"        -> {item['suggested_action']}")
        lines.append("")

    hmrc = s["hmrc"]
    if hmrc["over_allowance"] or hmrc["platform_reporting_likely"]:
        lines.append("HMRC")
        if hmrc["over_allowance"]:
            lines.append(
                f"  Gross revenue £{hmrc['gross_revenue']:.2f} is over the "
                f"£{hmrc['trading_allowance']:.0f} trading allowance."
            )
        if hmrc["platform_reporting_likely"]:
            lines.append("  You're in the range where platforms report seller data to HMRC.")
        lines.append(f"  {hmrc['note']}")
        lines.append("")

    lines.extend(_recommendations(rows, s, categories, stale))
    return "\n".join(lines)


def _recommendations(rows, summary, categories, stale) -> list[str]:
    """Concrete next actions, derived from the numbers above."""
    out = ["WHAT TO DO THIS WEEK"]
    velocity, money = summary["velocity"], summary["money"]

    if summary["counts"]["sold"] == 0:
        out.append("  1. Nothing has sold yet — too early to draw conclusions. Keep listing.")
        out.append("  2. If items have been up 14+ days with no watchers, the price is the problem.")
        return out

    step = 1
    if categories:
        best = categories[0]
        out.append(
            f"  {step}. Best category is '{best['category']}' (£{best['profit']:.2f} profit, "
            f"{best['roi_pct']:.0f}% ROI). Source more of it."
        )
        step += 1
        losers = [b for b in categories if b["sold"] >= 2 and b["roi_pct"] < 50]
        if losers:
            out.append(
                f"  {step}. Drop or re-price '{losers[-1]['category']}' — "
                f"{losers[-1]['roi_pct']:.0f}% ROI isn't paying for your time."
            )
            step += 1

    if velocity["median_days_to_sell"] and velocity["median_days_to_sell"] > velocity["target_days"]:
        out.append(
            f"  {step}. Median {velocity['median_days_to_sell']:.0f} days to sell is over your "
            f"{velocity['target_days']}-day target — list 5–10% lower on the next batch."
        )
        step += 1

    if stale:
        out.append(f"  {step}. Clear {len(stale)} stale item(s) using the actions listed above.")
        step += 1

    if money["capital_headroom"] < 20:
        out.append(
            f"  {step}. Only £{money['capital_headroom']:.2f} of working capital left — "
            "sell before you buy."
        )
    else:
        out.append(f"  {step}. £{money['capital_headroom']:.2f} free to spend on the next sourcing run.")
    return out


def write_dashboard_csv(rows: Sequence[dict], path: str | Path, cfg=None) -> Path:
    """Flat metric/value CSV — paste straight into Sheets."""
    cfg = cfg or cfg_mod.load()
    s = summarise(rows, cfg)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    records: list[tuple[str, str, str]] = [("section", "metric", "value")]
    for section in ("counts", "money", "velocity"):
        for key, value in s[section].items():
            records.append((section, key, "" if value is None else str(value)))
    records.append(("meta", "estimate_accuracy", str(s["estimate_accuracy"])))
    for bucket in top_categories(rows):
        records.append(("category_profit", bucket["category"], f"{bucket['profit']:.2f}"))
        records.append(("category_sell_through_pct", bucket["category"], f"{bucket['sell_through_pct']:.1f}"))

    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(records)
    return path
