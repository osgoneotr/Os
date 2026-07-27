#!/usr/bin/env python3
"""Dashboard and weekly review.

    python3 scripts/report.py                  # the full weekly review
    python3 scripts/report.py --stale          # just what's been sitting too long
    python3 scripts/report.py --top 10         # categories ranked by profit
    python3 scripts/report.py --mark SKU sold --price 42.00

The --mark flag is how you record a sale without opening the CSV.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autopilot import cli, config as cfg_mod, ledger as ledger_mod, reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reporting over the ledger.")
    parser.add_argument("--stale", action="store_true", help="only the stale-inventory section")
    parser.add_argument("--top", type=int, metavar="N", help="only the top N categories by profit")
    parser.add_argument("--dashboard", action="store_true", help="write data/out/dashboard.csv")
    parser.add_argument("--sheets", action="store_true", help="push the ledger to Google Sheets")
    parser.add_argument(
        "--mark", nargs=2, metavar=("SKU", "STATUS"),
        help="update one item, e.g. --mark VIN-A1B2C3 sold",
    )
    parser.add_argument("--price", type=float, help="sale price, used with --mark ... sold")
    parser.add_argument("--fees", type=float, help="actual fees paid, used with --mark ... sold")
    parser.add_argument("--postage", type=float, help="actual postage paid, used with --mark ... sold")
    return cli.common_args(parser)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = cfg_mod.load(args.config)

    if args.mark:
        sku, status = args.mark
        extras: dict[str, str] = {}
        if args.price is not None:
            extras["sold_price"] = f"{args.price:.2f}"
        if args.fees is not None:
            extras["actual_fees"] = f"{args.fees:.2f}"
        if args.postage is not None:
            extras["actual_postage"] = f"{args.postage:.2f}"

        row = ledger_mod.set_status(sku, status, cfg=cfg, **extras)
        if not row:
            print(f"No ledger row with SKU '{sku}'.", file=sys.stderr)
            return 1
        print(f"{sku} -> {status}")
        if status == "sold":
            profit = reports.realised_profit(row)
            estimated = reports._money(row.get("est_net_profit"))
            print(f"  realised profit £{profit:.2f}", end="")
            if estimated:
                delta = profit - estimated
                print(f"  (estimated £{estimated:.2f}, {'+' if delta >= 0 else ''}{delta:.2f})")
            else:
                print()
        return 0

    rows = ledger_mod.load(cfg=cfg)
    if not rows:
        print(f"Ledger is empty ({cfg.path('ledger')}).", file=sys.stderr)
        return 1

    if args.stale:
        stale = reports.stale_inventory(rows, cfg)
        if not stale:
            days = cfg.get("thresholds.stale_after_days", 60)
            print(f"Nothing has been listed longer than {days} days. ")
            return 0
        print(f"{len(stale)} stale item(s):\n")
        for item in stale:
            print(f"[{item['days_listed']:>3}d] {item['sku']}  {str(item['title'])[:50]}")
            print(f"        bought £{item['buy_price']:.2f}, listed £{item['list_price']:.2f}")
            print(f"        -> {item['suggested_action']}\n")
        return 0

    if args.top:
        buckets = reports.top_categories(rows, args.top)
        print(f"Top {len(buckets)} categories by realised profit:\n")
        print(f"{'category':<16}{'sold':>6}{'profit':>11}{'ROI':>9}{'sell-thru':>12}")
        for bucket in buckets:
            print(
                f"{bucket['category'][:15]:<16}{bucket['sold']:>6}"
                f"{('£%.2f' % bucket['profit']):>11}{('%.0f%%' % bucket['roi_pct']):>9}"
                f"{('%.0f%%' % bucket['sell_through_pct']):>12}"
            )
        return 0

    print(reports.render_text(rows, cfg))

    if args.dashboard:
        path = reports.write_dashboard_csv(rows, cfg.path("out") / "dashboard.csv", cfg)
        print(f"\nDashboard CSV: {path}")

    if args.sheets:
        from autopilot import sheets as sheets_mod

        print(f"\n{sheets_mod.push(rows, worksheet='ledger')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
