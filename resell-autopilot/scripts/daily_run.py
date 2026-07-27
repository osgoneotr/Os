#!/usr/bin/env python3
"""One command for the whole daily loop.

    python3 scripts/daily_run.py

What it does, in order:
  1. Sources candidates (from data/inbox/, or live eBay if --source ebay).
  2. Prices every one and ranks them.
  3. Prints an APPROVAL LIST — the only thing you actually have to read.
  4. Regenerates listing copy and marketplace CSVs for anything you've already
     marked as bought.
  5. Prints the day's numbers and what's left of your working capital.

It never buys, never lists, never spends money. Every purchase and every
upload stays a human decision — the automation is in the finding, the pricing
and the writing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autopilot import cli, config as cfg_mod, exporters, ledger as ledger_mod
from autopilot import listings as listings_mod, reports, sourcing
from autopilot.models import analysis_to_row
from autopilot.sources.ebay_browse import EbayAuthError

DEFAULT_SEEDS = ["data/seeds/trainers_clothing.yaml", "data/seeds/retro_tech.yaml"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="The daily sourcing + listing run.")
    parser.add_argument("--source", choices=["inbox", "ebay"], default="inbox")
    parser.add_argument("--seeds", nargs="*", default=DEFAULT_SEEDS, help="seed YAML files (--source ebay)")
    parser.add_argument("--category", default="unknown")
    parser.add_argument("--comps", help="sold-listings CSV/HTML to price against")
    parser.add_argument("--top", type=int, default=10, help="how many deals to put on the approval list")
    parser.add_argument("--max-price", type=float)
    parser.add_argument("--no-api", action="store_true")
    parser.add_argument("--skip-listings", action="store_true", help="source only, no listing generation")
    return cli.common_args(parser)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = cfg_mod.load(args.config)
    today = dt.date.today().isoformat()

    print("=" * 70)
    print(f"DAILY RUN — {today}")
    print("=" * 70)
    cfg_mod.print_stale_fee_warnings(cfg, stream=sys.stdout)

    existing = ledger_mod.load(cfg=cfg)
    headroom = ledger_mod.capital_headroom(existing, cfg)
    print(f"\nWorking capital available: £{headroom:.2f}")
    if headroom <= 0:
        print("  All capital is tied up in stock. Sell something before buying more.")

    # ---- 1. source ----------------------------------------------------
    print("\n[1/4] Sourcing")
    candidates, skips = [], []
    if args.source == "ebay":
        queries: list[str] = []
        for seed in args.seeds:
            path = Path(seed)
            if not path.is_absolute():
                path = cfg_mod.ROOT / path
            if not path.exists():
                print(f"  seed file missing: {path}")
                continue
            seed_queries, _ = sourcing.load_seed_queries(path)
            queries.extend(seed_queries)
            print(f"  {path.name}: {len(seed_queries)} queries")
        try:
            candidates = sourcing.gather_from_ebay(
                queries, category=args.category, max_price=args.max_price, cfg=cfg
            )
        except EbayAuthError as exc:
            print(f"  {exc}")
            print("  Falling back to data/inbox/.")
            candidates, skips = sourcing.gather_from_files(
                sourcing.inbox_csvs(cfg), category=args.category, cfg=cfg
            )
    else:
        files = sourcing.inbox_csvs(cfg)
        if files:
            print(f"  reading {len(files)} file(s) from {cfg.path('inbox')}")
            candidates, skips = sourcing.gather_from_files(files, category=args.category, cfg=cfg)
        else:
            print(f"  nothing in {cfg.path('inbox')} — skipping the sourcing step")

    analyses = []
    if candidates:
        candidates = [sourcing.infer_attributes(c, cfg) for c in candidates]
        if args.max_price is not None:
            candidates = [c for c in candidates if c.ask_price <= args.max_price]

        resolver = sourcing.CompsResolver(cfg, comps_file=args.comps, use_api=not args.no_api)
        print(f"  pricing {len(candidates)} candidate(s)")
        analyses, errors = sourcing.analyse_candidates(candidates, resolver, cfg=cfg)
        if resolver.api_note:
            print(f"  {resolver.api_note}")

        rows = [analysis_to_row(a) for a in analyses]
        _, added, updated = ledger_mod.upsert(rows, cfg=cfg)
        print(f"  ledger: +{added} new, {updated} updated")
        cli.print_errors(skips, "import rows skipped")
        cli.print_errors(errors, "items failed to price")

    # ---- 2. approval list ---------------------------------------------
    print("\n[2/4] Approval list")
    if analyses:
        buys = [a for a in sourcing.rank(analyses) if a.verdict.action in ("BUY", "REVIEW")]
        shortlist = buys[: args.top]
        if not shortlist:
            print("  Nothing cleared the buy rule today. That's a normal result — most days")
            print("  there is nothing worth buying, and forcing it is how you lose money.")
        else:
            running_cost = 0.0
            for index, analysis in enumerate(shortlist, 1):
                affordable = running_cost + analysis.economics.buy_price <= headroom
                flag = "" if affordable else "  [over budget]"
                if affordable and analysis.verdict.action == "BUY":
                    running_cost += analysis.economics.buy_price
                print(f"\n  {index}.{flag}")
                print("  " + cli.format_analysis(analysis, verbose=not args.quiet).replace("\n", "\n  "))
            print(f"\n  Total to spend if you take every BUY above: £{running_cost:.2f}")
        print(f"\n  {cli.summarise_verdicts(analyses)}")
    else:
        print("  No new candidates today.")

    # ---- 3. listings ---------------------------------------------------
    print("\n[3/4] Listing generation")
    if args.skip_listings:
        print("  skipped (--skip-listings)")
    else:
        rows = ledger_mod.load(cfg=cfg)
        to_list = [r for r in rows if r.get("status") == "bought"]
        if not to_list:
            print("  Nothing at status 'bought'. Mark items bought once you've paid:")
            print("    python3 scripts/report.py --mark <SKU> bought")
        else:
            from make_listings import row_to_analysis  # same directory

            ready, held = [], []
            for row in to_list:
                analysis = row_to_analysis(row, cfg)
                package = listings_mod.build_package(analysis, cfg=cfg)
                merged = dict(row)
                merged.update(
                    {
                        "title_seo": package.title_seo,
                        "title_style": package.title_style,
                        "description": package.description,
                        "keywords": ", ".join(package.keywords),
                        "channel": package.channel,
                        "list_price": f"{package.list_price:.2f}",
                        "min_price": f"{package.min_price:.2f}",
                        "quality_gate": package.quality_gate,
                        "warnings": " | ".join(package.warnings),
                    }
                )
                (held if package.quality_gate == "fail" else ready).append(merged)

            if ready:
                written = exporters.export_all(ready, cfg.path("out"), stem="listings")
                ledger_mod.upsert(ready, cfg=cfg)
                print(f"  {len(ready)} listing(s) exported:")
                for name, path in written.items():
                    print(f"    {name:<10} {path}")
            if held:
                path = exporters.export_canonical(held, cfg.path("out") / "listings_needs_attention.csv")
                print(f"  {len(held)} held back by the quality gate -> {path}")

            flagged = [r for r in ready if r.get("quality_gate") == "pass_with_warnings"]
            if flagged:
                print(f"  {len(flagged)} listing(s) carry warnings — read them before uploading:")
                for row in flagged[:5]:
                    print(f"    ! {row['sku']}: {row.get('warnings', '')[:100]}")

    # ---- 4. numbers ----------------------------------------------------
    print("\n[4/4] Position")
    rows = ledger_mod.load(cfg=cfg)
    summary = reports.summarise(rows, cfg)
    counts, money = summary["counts"], summary["money"]
    print(
        f"  pipeline: {counts['candidates']} candidates | {counts['bought']} bought | "
        f"{counts['listed']} listed | {counts['sold']} sold"
    )
    print(
        f"  money: £{money['realised_profit']:.2f} realised profit, "
        f"£{money['capital_committed']:.2f} in stock, £{money['capital_headroom']:.2f} free"
    )

    stale = reports.stale_inventory(rows, cfg)
    if stale:
        print(f"  {len(stale)} item(s) have gone stale — run: python3 scripts/report.py --stale")

    print("\nDone. Your jobs today: approve the buys, upload the CSVs, post the parcels.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
