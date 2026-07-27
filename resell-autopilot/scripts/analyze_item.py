#!/usr/bin/env python3
"""Price one item. This is the script you run standing in the shop.

    python3 scripts/analyze_item.py \
        --title "The North Face Nuptse 700 puffer jacket black" \
        --price 12 --brand "The North Face" --model "Nuptse 700" \
        --size L --condition good --category clothing \
        --comps data/inbox/nuptse_sold.html

With no --comps and no eBay keys it still runs, but returns REVIEW rather than
BUY — the system won't tell you something is a good buy when it has no evidence.

Add --save to write the result into the ledger, --listing to also print the
listing copy that would be generated.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autopilot import cli, config as cfg_mod, ledger as ledger_mod, listings as listings_mod
from autopilot import pricing as pricing_mod, sourcing
from autopilot.models import CONDITIONS, Candidate, analysis_to_row


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyse a single item: buy or pass?")
    parser.add_argument("--title", required=True, help="what the item is, as you'd search for it")
    parser.add_argument("--price", type=float, required=True, help="what it costs you (£)")
    parser.add_argument("--brand", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--size", default="")
    parser.add_argument("--colour", default="")
    parser.add_argument("--material", default="")
    parser.add_argument("--condition", default="unknown", choices=list(CONDITIONS))
    parser.add_argument("--condition-note", default="")
    parser.add_argument("--defects", default="")
    parser.add_argument("--category", default="unknown")
    parser.add_argument("--weight", type=float, help="kg, for the postage band")
    parser.add_argument("--photos", nargs="*", default=[], help="file paths or URLs")
    parser.add_argument(
        "--confirmed", nargs="*", default=[],
        help="attributes you verified IN HAND (e.g. --confirmed brand size). "
             "Unconfirmed attributes get hedged in the listing copy.",
    )
    parser.add_argument(
        "--tested", action="store_true",
        help="electronics only: you powered it on and it works. Without this the "
             "copy will not claim the item works.",
    )
    parser.add_argument("--comps", help="sold-listings CSV or saved eBay HTML")
    parser.add_argument("--channel", help="force a channel instead of picking the best")
    parser.add_argument("--listing", action="store_true", help="also print the listing copy")
    parser.add_argument("--save", action="store_true", help="write the result to the ledger")
    parser.add_argument("--no-api", action="store_true")
    return cli.common_args(parser)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = cfg_mod.load(args.config)
    cfg_mod.print_stale_fee_warnings(cfg)

    evidence = {attribute: "user_confirmed" for attribute in args.confirmed}
    candidate = Candidate(
        title=args.title,
        ask_price=args.price,
        source="manual",
        source_id=args.title,
        category=args.category,
        brand=args.brand,
        model=args.model,
        size=args.size,
        colour=args.colour,
        material=args.material,
        condition=args.condition,
        condition_note=args.condition_note,
        defects=args.defects,
        weight_kg=args.weight,
        photos=list(args.photos),
        tested_confirmed=args.tested,
        evidence=evidence,
    )
    candidate = sourcing.infer_attributes(candidate, cfg)

    blocked = listings_mod.check_blocked(candidate, cfg)

    resolver = sourcing.CompsResolver(cfg, comps_file=args.comps, use_api=not args.no_api)
    comps = resolver.resolve(candidate.search_phrase, candidate.category)
    if resolver.api_note:
        print(f"[comps] {resolver.api_note}\n")

    if args.channel or blocked:
        analysis = pricing_mod.analyse(
            candidate, comps, channel=args.channel, cfg=cfg, blocked_reason=blocked
        )
    else:
        analysis = pricing_mod.best_channel(candidate, comps, cfg=cfg)

    print(cli.format_analysis(analysis))

    if comps.is_usable:
        print()
        print("Cost breakdown:")
        for line in analysis.economics.breakdown:
            print(f"  {line}")

        # Show what the other channels would return — sometimes it's close, and
        # you may prefer the faster-selling one over the marginally richer one.
        if not args.channel and not blocked:
            print("\nBy channel:")
            for channel in cfg.sell_channels():
                alt = pricing_mod.analyse(candidate, comps, channel=channel, cfg=cfg)
                marker = " <-" if channel == analysis.economics.channel else ""
                print(
                    f"  {channel:<10} list £{alt.economics.list_price:>7.2f}  "
                    f"net £{alt.economics.net_profit:>7.2f}  "
                    f"({alt.economics.margin_pct:>5.1f}% margin){marker}"
                )
    else:
        print("\nNo cost breakdown — there's no price to work back from yet.")

    if args.listing:
        print()
        package = listings_mod.build_package(analysis, cfg=cfg)
        print(cli.format_package(package))

    if args.save:
        package = listings_mod.build_package(analysis, cfg=cfg) if not blocked else None
        _, added, updated = ledger_mod.upsert([analysis_to_row(analysis, package)], cfg=cfg)
        print(f"\nSaved to ledger (+{added} new, {updated} updated): {cfg.path('ledger')}")

    return 0 if analysis.verdict.action in ("BUY", "REVIEW") else 1


if __name__ == "__main__":
    raise SystemExit(main())
