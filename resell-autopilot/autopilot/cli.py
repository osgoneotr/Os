"""Shared CLI plumbing: path bootstrap, common flags, terminal formatting."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .models import Analysis, ListingPackage

ROOT = Path(__file__).resolve().parent.parent


def bootstrap() -> None:
    """Let `python3 scripts/foo.py` import the `autopilot` package."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def common_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--config", help="path to settings.yaml (defaults to config/settings.yaml)")
    parser.add_argument("--quiet", action="store_true", help="suppress per-item output")
    return parser


_ICON = {"BUY": "[BUY ]", "REVIEW": "[CHK ]", "PASS": "[pass]", "BLOCKED": "[STOP]"}


def format_analysis(analysis: Analysis, *, verbose: bool = True) -> str:
    c, e, v, comps = analysis.candidate, analysis.economics, analysis.verdict, analysis.comps
    priced = comps.is_usable

    lines = [f"{_ICON.get(v.action, '[    ]')} {c.sku}  {c.title[:58]}"]
    if priced:
        lines.append(
            f"        buy £{e.buy_price:.2f} -> list £{e.list_price:.2f} on {e.channel}"
            f"  |  net £{e.net_profit:.2f} ({e.margin_pct:.0f}% margin, {e.roi_pct:.0f}% ROI)"
        )
    else:
        # Printing "£0.00 list, -112% ROI" would dress up missing data as a finding.
        lines.append(f"        buy £{e.buy_price:.2f} -> not priced (no comps yet)")

    if not verbose:
        return "\n".join(lines)

    if priced:
        lines.append(
            f"        comps: n={comps.n} median £{comps.median:.2f} "
            f"[{comps.source}, {comps.confidence} confidence]"
        )
        lines.append(f"        profit range £{v.profit_low:.2f} – £{v.profit_high:.2f}")
    lines.append(f"        {v.justification}")
    for reason in v.reasons[: 3 if v.action != "BUY" else 2]:
        lines.append(f"          - {reason}")
    for risk in v.risks[:3]:
        lines.append(f"          ! {risk}")
    for warning in comps.warnings[:2]:
        lines.append(f"          ~ {warning}")
    if c.url:
        lines.append(f"        {c.url}")
    return "\n".join(lines)


def format_package(package: ListingPackage) -> str:
    gate_icon = {"pass": "OK", "pass_with_warnings": "OK*", "fail": "FAIL"}[package.quality_gate]
    lines = [
        f"--- {package.sku}  [{package.channel}]  quality gate: {gate_icon}",
        f"    SEO title   ({len(package.title_seo):>3}) {package.title_seo}",
        f"    Style title ({len(package.title_style):>3}) {package.title_style}",
        f"    Price       £{package.list_price:.2f}   (floor £{package.min_price:.2f})",
        f"    Keywords    {', '.join(package.keywords[:12])}",
    ]
    if package.description:
        for line in package.description.split("\n"):
            lines.append(f"    | {line}")
    for warning in package.warnings:
        lines.append(f"    ! {warning}")
    for claim in package.stripped_claims:
        lines.append(f"    x removed unsupported claim: '{claim}'")
    return "\n".join(lines)


def summarise_verdicts(analyses: Sequence[Analysis]) -> str:
    counts: dict[str, int] = {}
    for analysis in analyses:
        counts[analysis.verdict.action] = counts.get(analysis.verdict.action, 0) + 1
    parts = [f"{action} {count}" for action, count in sorted(counts.items())]
    buy_profit = sum(a.economics.net_profit for a in analyses if a.verdict.action == "BUY")
    buy_cost = sum(a.economics.buy_price for a in analyses if a.verdict.action == "BUY")
    return (
        f"{len(analyses)} analysed  ({', '.join(parts) or 'none'})  |  "
        f"BUY basket: spend £{buy_cost:.2f} -> est. profit £{buy_profit:.2f}"
    )


def print_errors(errors: Sequence[str], label: str = "skipped") -> None:
    if not errors:
        return
    print(f"\n{len(errors)} {label}:", file=sys.stderr)
    for message in errors[:20]:
        print(f"  - {message}", file=sys.stderr)
    if len(errors) > 20:
        print(f"  ... and {len(errors) - 20} more", file=sys.stderr)
