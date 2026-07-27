#!/usr/bin/env python3
"""Turn ledger rows into marketplace-ready listings.

    # everything you've approved to buy
    python3 scripts/make_listings.py --verdict BUY

    # everything you've actually bought and now need to list
    python3 scripts/make_listings.py --status bought --set-status listed

Writes to data/out/:
    listings_canonical.csv   every field (this is the one for Google Sheets)
    listings_vinted.csv      copy-paste worksheet, in the Vinted app's field order
    listings_ebay_uk.csv     eBay File Exchange format
    listings_facebook.csv    Facebook catalogue format

Items that fail the quality gate are written to a separate `_needs_attention`
file rather than into the upload CSVs, so a claim you can't support never
reaches a live listing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autopilot import cli, config as cfg_mod, exporters, ledger as ledger_mod
from autopilot import listings as listings_mod, pricing as pricing_mod
from autopilot.models import Analysis, Candidate, Comps, Economics, Verdict


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate listing copy and marketplace CSVs.")
    parser.add_argument("--status", nargs="*", default=["candidate", "approved", "bought"],
                        help="ledger statuses to include")
    parser.add_argument("--verdict", nargs="*", help="only these verdicts (e.g. BUY REVIEW)")
    parser.add_argument("--sku", nargs="*", help="only these SKUs")
    parser.add_argument("--channel", help="force one channel for every item")
    parser.add_argument("--stem", default="listings", help="output filename stem")
    parser.add_argument("--set-status", help="move processed items to this status (e.g. listed)")
    parser.add_argument("--tsv", action="store_true", help="also write a Sheets paste file")
    parser.add_argument("--sheets", action="store_true", help="push to Google Sheets if configured")
    parser.add_argument("--template", action="store_true",
                        help="also write header-only templates to diff against the platform's own")
    return cli.common_args(parser)


def row_to_analysis(row: dict, cfg) -> Analysis:
    """Rebuild the objects the listing generator needs from a ledger row."""
    candidate = Candidate(
        title=row.get("title_seo") or row.get("model") or row.get("brand") or row.get("sku", ""),
        ask_price=_money(row.get("buy_price")),
        source=row.get("source", "ledger"),
        source_id=row.get("sku", ""),
        url=row.get("source_url", ""),
        category=row.get("category", "unknown"),
        brand=row.get("brand", ""),
        model=row.get("model", ""),
        size=row.get("size", ""),
        colour=row.get("colour", ""),
        material=row.get("material", ""),
        condition=row.get("condition") or "unknown",
        condition_note=row.get("condition_note", ""),
        defects=row.get("defects", ""),
        location=row.get("location", ""),
        photos=[p.strip() for p in str(row.get("image_refs", "")).split("|") if p.strip()],
        notes=row.get("notes", ""),
        sku=row.get("sku", ""),
    )
    # Preserve any evidence the sourcing pass recorded. Ledger rows don't carry
    # the evidence map, so treat everything as unverified until confirmed in
    # hand — the gate will hedge the copy accordingly. That's the safe default.

    comps = Comps(query=candidate.search_phrase, source=row.get("comps_source", "none"))
    comps.n = int(_money(row.get("comps_n")))
    comps.median = _money(row.get("comps_median"))
    comps.confidence = row.get("confidence") or "none"

    channel = row.get("channel") or (cfg.sell_channels() or ["vinted"])[0]
    list_price = _money(row.get("list_price"))
    economics = Economics(
        channel=channel,
        buy_price=_money(row.get("buy_price")),
        list_price=list_price,
        min_price=_money(row.get("min_price")) or round(list_price * 0.72, 2),
        platform_fee=_money(row.get("platform_fee")),
        postage_cost=_money(row.get("postage_cost")),
        packaging_cost=_money(row.get("packaging_cost")),
        postage_band=row.get("postage_band", ""),
        net_proceeds=list_price - _money(row.get("platform_fee")),
        net_profit=_money(row.get("est_net_profit")),
        margin_pct=_money(row.get("margin_pct")),
        roi_pct=_money(row.get("roi_pct")),
    )
    verdict = Verdict(action=row.get("verdict") or "REVIEW")

    return Analysis(
        candidate=candidate,
        comps=comps,
        economics=economics,
        verdict=verdict,
        est_resale_low=_money(row.get("est_resale_low")),
        est_resale_high=_money(row.get("est_resale_high")),
    )


def _money(raw) -> float:
    try:
        return float(str(raw or "0").replace("£", "").replace(",", "") or 0)
    except ValueError:
        return 0.0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = cfg_mod.load(args.config)

    rows = ledger_mod.load(cfg=cfg)
    if not rows:
        print(f"Ledger is empty ({cfg.path('ledger')}). Run source_deals.py first.", file=sys.stderr)
        return 1

    selected = [r for r in rows if r.get("status") in set(args.status)]
    if args.verdict:
        wanted = {v.upper() for v in args.verdict}
        selected = [r for r in selected if (r.get("verdict") or "").upper() in wanted]
    if args.sku:
        selected = [r for r in selected if r.get("sku") in set(args.sku)]

    if not selected:
        print(
            f"Nothing matched (status={args.status}, verdict={args.verdict}). "
            f"Ledger has {len(rows)} row(s).",
            file=sys.stderr,
        )
        return 1

    print(f"Building listings for {len(selected)} item(s)...\n")

    ready: list[dict] = []
    blocked: list[dict] = []
    for row in selected:
        analysis = row_to_analysis(row, cfg)

        # Re-price when the ledger has no price yet (a row added by hand), or
        # when --channel moves the item somewhere with a different audience —
        # a Facebook price is not an eBay price, and reusing one for the other
        # is how an item sits unsold for six months.
        channel_changed = bool(args.channel) and args.channel != analysis.economics.channel
        if (analysis.economics.list_price <= 0 or channel_changed) and analysis.comps.median > 0:
            # The ledger stores the median, not the full sample, so rebuild a
            # one-point distribution through build_comps — which also restores
            # the derived fields (n, trimmed_mean) that the pricing engine
            # checks before it will quote a price at all.
            rebuilt = pricing_mod.build_comps(
                analysis.comps.query,
                [analysis.comps.median],
                source=analysis.comps.source or "ledger",
                category=analysis.candidate.category,
                cfg=cfg,
            )
            # Keep the confidence the original run earned; a one-point rebuild
            # would otherwise report "low" and misstate what we actually know.
            rebuilt.confidence = analysis.comps.confidence
            rebuilt.n = analysis.comps.n or rebuilt.n
            analysis = pricing_mod.analyse(
                analysis.candidate, rebuilt, channel=args.channel, cfg=cfg
            )

        package = listings_mod.build_package(analysis, channel=args.channel, cfg=cfg)
        if not args.quiet:
            print(cli.format_package(package))
            print()

        merged = dict(row)
        merged.update(
            {
                "title_seo": package.title_seo,
                "title_style": package.title_style,
                "description": package.description,
                "keywords": ", ".join(package.keywords),
                "channel": package.channel,
                "quality_gate": package.quality_gate,
                "warnings": " | ".join(package.warnings),
            }
        )
        # Never let a failed re-price blank out a price the ledger already had.
        if package.list_price > 0:
            economics = analysis.economics
            merged.update(
                {
                    "list_price": f"{package.list_price:.2f}",
                    "min_price": f"{package.min_price:.2f}",
                    # The profit columns have to move with the price. Leaving a
                    # £54-on-Vinted profit next to a £42 Facebook price is worse
                    # than showing nothing — you'd act on it.
                    "est_net_profit": f"{economics.net_profit:.2f}",
                    "margin_pct": f"{economics.margin_pct:.1f}",
                    "roi_pct": f"{economics.roi_pct:.1f}",
                    "platform_fee": f"{economics.platform_fee:.2f}",
                    "postage_cost": f"{economics.postage_cost:.2f}",
                    "packaging_cost": f"{economics.packaging_cost:.2f}",
                    "postage_band": economics.postage_band,
                }
            )
        (blocked if package.quality_gate == "fail" else ready).append(merged)

    out_dir = cfg.path("out")
    written = exporters.export_all(ready, out_dir, stem=args.stem) if ready else {}

    print("=" * 70)
    print(f"{len(ready)} listing(s) exported, {len(blocked)} held back by the quality gate.")
    for name, path in written.items():
        print(f"  {name:<10} {path}")

    if blocked:
        held = out_dir / f"{args.stem}_needs_attention.csv"
        exporters.export_canonical(blocked, held)
        print(f"  held back  {held}")
        for row in blocked:
            print(f"    ! {row.get('sku')}: {row.get('warnings', '')[:110]}")

    if args.template:
        for channel in ("vinted", "ebay_uk", "facebook"):
            path = exporters.write_template(out_dir / f"template_{channel}.csv", channel)
            print(f"  template   {path}")

    if args.tsv or args.sheets:
        from autopilot import sheets as sheets_mod

        tsv_path = sheets_mod.write_tsv(ready, out_dir / f"{args.stem}_sheets_paste.tsv")
        print(f"  paste file {tsv_path}  (select all, copy, paste into a Google Sheet)")
        if args.sheets:
            print(f"  sheets     {sheets_mod.push(ready, worksheet='listings')}")

    if args.set_status and ready:
        for row in ready:
            ledger_mod.set_status(row["sku"], args.set_status, cfg=cfg)
        print(f"\nMoved {len(ready)} item(s) to status '{args.set_status}'.")
    elif ready:
        # Persist the generated copy so the next run doesn't regenerate it.
        ledger_mod.upsert(ready, cfg=cfg)

    print(
        "\nBefore you upload: read README_EXPORTS.txt in data/out/. Check every"
        "\nquality-gate warning above — those are claims the evidence doesn't support."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
