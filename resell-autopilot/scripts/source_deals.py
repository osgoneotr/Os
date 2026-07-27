#!/usr/bin/env python3
"""Find and price candidate deals.

Works three ways, in increasing order of setup:

    # 1. No accounts, no keys: a CSV of things you photographed in a shop
    python3 scripts/source_deals.py --source csv --input data/inbox/charity_shop.csv \
        --category clothing --comps data/inbox/north_face_sold.html

    # 2. Everything sitting in data/inbox/
    python3 scripts/source_deals.py --source inbox --category clothing

    # 3. Live eBay search using the seed queries (needs free API keys)
    python3 scripts/source_deals.py --source ebay --seeds data/seeds/trainers_clothing.yaml \
        --category clothing --top 20

Results go to the ledger (data/ledger.csv) and a review CSV in data/out/.
Nothing is ever bought automatically — you approve every purchase.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autopilot import cli, config as cfg_mod, exporters, ledger as ledger_mod, sourcing
from autopilot.models import analysis_to_row
from autopilot.sources.ebay_browse import EbayAuthError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Source and price candidate deals.")
    parser.add_argument(
        "--source", choices=["ebay", "csv", "inbox"], default="inbox",
        help="where candidates come from (default: everything in data/inbox/)",
    )
    parser.add_argument("--input", nargs="*", help="CSV file(s) of candidates (with --source csv)")
    parser.add_argument("--seeds", help="seeds YAML of search queries (with --source ebay)")
    parser.add_argument("--query", nargs="*", help="ad-hoc search phrase(s) (with --source ebay)")
    parser.add_argument(
        "--comps",
        help="sold-listings CSV or saved eBay HTML to price against. Most accurate option.",
    )
    parser.add_argument("--category", default="unknown", help="trainers | clothing | retro_tech | audio")
    parser.add_argument("--channel", help="force one sell channel (default: pick the most profitable)")
    parser.add_argument("--max-price", type=float, help="ignore anything above this buy price")
    parser.add_argument("--top", type=int, default=25, help="how many ranked deals to show")
    parser.add_argument("--no-api", action="store_true", help="never call the eBay API for comps")
    parser.add_argument("--out", help="review CSV path (default: data/out/candidates.csv)")
    parser.add_argument("--dry-run", action="store_true", help="don't write to the ledger")
    return cli.common_args(parser)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = cfg_mod.load(args.config)
    cfg_mod.print_stale_fee_warnings(cfg)

    candidates, skips = [], []

    if args.source == "ebay":
        queries = list(args.query or [])
        if args.seeds:
            seed_queries, _ = sourcing.load_seed_queries(args.seeds)
            queries.extend(seed_queries)
        if not queries:
            print("--source ebay needs --seeds and/or --query.", file=sys.stderr)
            return 2
        print(f"Searching eBay UK with {len(queries)} query/queries...")
        try:
            candidates = sourcing.gather_from_ebay(
                queries, category=args.category, max_price=args.max_price, cfg=cfg
            )
        except EbayAuthError as exc:
            print(f"\n{exc}\n", file=sys.stderr)
            return 1

    elif args.source == "csv":
        if not args.input:
            print("--source csv needs --input <file.csv> [more.csv ...]", file=sys.stderr)
            return 2
        candidates, skips = sourcing.gather_from_files(args.input, category=args.category, cfg=cfg)

    else:  # inbox
        files = sourcing.inbox_csvs(cfg)
        if not files:
            inbox = cfg.path("inbox")
            print(
                f"No CSVs in {inbox}.\n"
                "Drop a spreadsheet of items there (a 'title' and a 'price' column is the "
                "minimum), or use --source ebay to search live.",
                file=sys.stderr,
            )
            return 1
        print(f"Reading {len(files)} file(s) from {cfg.path('inbox')}")
        candidates, skips = sourcing.gather_from_files(files, category=args.category, cfg=cfg)

    if not candidates:
        cli.print_errors(skips, "rows skipped")
        print("No usable candidates found.", file=sys.stderr)
        return 1

    # Fill in brand/size/category from the title where we can. Anything guessed
    # is marked unverified so the listing copy hedges it.
    candidates = [sourcing.infer_attributes(c, cfg) for c in candidates]

    if args.max_price is not None:
        before = len(candidates)
        candidates = [c for c in candidates if c.ask_price <= args.max_price]
        if before != len(candidates):
            print(f"Filtered out {before - len(candidates)} item(s) over £{args.max_price:.2f}")

    resolver = sourcing.CompsResolver(cfg, comps_file=args.comps, use_api=not args.no_api)
    print(f"Pricing {len(candidates)} candidate(s)...")
    analyses, errors = sourcing.analyse_candidates(
        candidates, resolver, cfg=cfg, channel=args.channel
    )
    if resolver.api_note:
        print(f"[comps] {resolver.api_note}")

    ranked = sourcing.rank(analyses, top=args.top)

    print()
    for analysis in ranked:
        print(cli.format_analysis(analysis, verbose=not args.quiet))
        print()

    print("=" * 70)
    print(cli.summarise_verdicts(analyses))

    rows = [analysis_to_row(a) for a in analyses]
    out_path = Path(args.out) if args.out else cfg.path("out") / "candidates.csv"
    exporters.export_canonical(rows, out_path)
    print(f"Review CSV: {out_path}")

    if not args.dry_run:
        _, added, updated = ledger_mod.upsert(rows, cfg=cfg)
        print(f"Ledger: +{added} new, {updated} updated -> {cfg.path('ledger')}")
        headroom = ledger_mod.capital_headroom(ledger_mod.load(cfg=cfg), cfg)
        print(f"Working capital left: £{headroom:.2f}")

    cli.print_errors(skips, "rows skipped on import")
    cli.print_errors(errors, "items failed to price")

    print(
        "\nNext: check the BUY list above, buy what you agree with, then run\n"
        "  python3 scripts/make_listings.py --verdict BUY\n"
        "to generate the listing copy and marketplace CSVs."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
