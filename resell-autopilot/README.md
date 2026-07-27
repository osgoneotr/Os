# resell-autopilot

A UK reselling pipeline for a solo seller with £200 and no spare time. Finds
underpriced items, prices them against real sold data, writes the listings,
and tracks what actually made money.

Built for: Slough/West London · GBP · Vinted first, then eBay UK, then
Facebook Marketplace · trainers & branded clothing, retro tech & audio.

**Read [BLUEPRINT.md](BLUEPRINT.md) first** — it explains the business model
and what is and isn't automatable. This file is how to run it.

---

## 1. Setup checklist

### Works immediately, zero accounts

```bash
cd resell-autopilot
python3 -c "import yaml" || pip install pyyaml   # the only hard dependency
python3 tests/test_pipeline.py                    # 39 tests, ~0.1s
```

That's it. Everything below is optional.

### Accounts to create, in the order they become useful

| # | Account | Cost | Needed for | When |
|---|---|---|---|---|
| 1 | **Vinted** | Free | Selling clothing and trainers | Day 1 |
| 2 | **eBay UK** (buy + sell) | Free | Sold-price research; selling electronics | Day 1 |
| 3 | **PayPal / bank details** on both | Free | Getting paid | Day 1 |
| 4 | **eBay developer** — developer.ebay.com | Free | Automated price comps + eBay deal sourcing | Week 2 |
| 5 | **Google account** | Free | Ledger in Sheets instead of a CSV | Optional |
| 6 | eBay **Marketplace Insights** access | Free, needs approval | Real sold prices via API | Optional |

### Files to create

```bash
cp .env.example .env          # only if you're using any API
```

| File | Purpose |
|---|---|
| `.env` | API keys. Git-ignored. Never commit it. |
| `data/inbox/*.csv` | Items you're considering buying |
| `data/inbox/comps/*` | Sold-price evidence — CSVs or saved eBay pages |
| `data/ledger.csv` | Created automatically. Your inventory and accounts. |
| `data/out/*.csv` | Generated. Upload these to the marketplaces. |

### eBay API keys (10 minutes, free, optional but worth it)

1. developer.ebay.com → sign up → **Application Keys**.
2. Create a **Production** keyset (the Sandbox one returns fake data).
3. Copy the App ID (Client ID) and Cert ID (Client Secret) into `.env`:
   ```
   EBAY_CLIENT_ID=your-app-id
   EBAY_CLIENT_SECRET=your-cert-id
   ```
4. Test: `python3 scripts/source_deals.py --source ebay --query "carhartt jacket" --top 5`

The free tier is 5,000 calls/day — far more than this uses.

---

## 2. The daily loop

```bash
python3 scripts/daily_run.py
```

That one command sources, prices, ranks, generates listings and reports your
position. Everything below is the same thing in pieces.

### Standing in a shop with something in your hand

```bash
python3 scripts/analyze_item.py \
  --title "The North Face Nuptse 700 puffer black" --price 14 \
  --brand "The North Face" --model "Nuptse 700" --size L \
  --condition good --category clothing \
  --confirmed brand model size --listing
```

`--confirmed` is the important flag: list the attributes you can physically
see. Anything not confirmed gets hedged in the listing copy rather than
asserted. For electronics, `--tested` means you powered it on — without it the
copy will never claim the item works.

### Got home with a haul

1. Put the items in a CSV in `data/inbox/`. Minimum columns: `title`, `price`.
   Headers are matched loosely — `Item`/`Cost`/`Sold Price` all work.
2. Get comps: search each item on eBay, tick **Sold items**, `Ctrl+S` the
   results page into `data/inbox/comps/` named after the product
   (`north-face-nuptse-700.html`). The filename is how items get matched to
   their comps.
3. Run:
   ```bash
   python3 scripts/source_deals.py --source inbox --category clothing
   ```

### Bought something, want it listed

```bash
python3 scripts/report.py --mark VIN-A1B2C3 bought
python3 scripts/make_listings.py --status bought --set-status listed
```

Then upload from `data/out/` — see `README_EXPORTS.txt` there for what each
file is and where it goes.

### Something sold

```bash
python3 scripts/report.py --mark VIN-A1B2C3 sold --price 57.00 --postage 3.15
```

### Weekly

```bash
python3 scripts/report.py --dashboard
python3 scripts/report.py --stale
```

---

## 3. What each script does

| Script | Job |
|---|---|
| `scripts/daily_run.py` | The whole loop in one command |
| `scripts/source_deals.py` | Find and price candidates (CSV, inbox, or eBay API) |
| `scripts/analyze_item.py` | One item, one decision — the in-the-shop script |
| `scripts/make_listings.py` | Ledger rows → listing copy → marketplace CSVs |
| `scripts/report.py` | Dashboard, stale stock, marking items sold |

| Module | Job |
|---|---|
| `autopilot/config.py` | Loads `config/settings.yaml`; warns when fees go stale |
| `autopilot/models.py` | The four core shapes + the canonical CSV schema |
| `autopilot/sources/ebay_browse.py` | eBay Browse API client |
| `autopilot/sources/tabular.py` | CSV and saved-HTML parsing |
| `autopilot/sourcing.py` | Candidate gathering, comps resolution, ranking |
| `autopilot/pricing.py` | Comps → resale estimate → verdict |
| `autopilot/fees.py` | Platform fees, postage bands, packaging |
| `autopilot/listings.py` | Copy generation + the quality gate |
| `autopilot/exporters.py` | Per-marketplace CSV formats |
| `autopilot/ledger.py` | The CSV that is the source of truth |
| `autopilot/reports.py` | Metrics and the weekly review |
| `autopilot/sheets.py` | Optional Google Sheets sync |

---

## 4. Tuning it

Everything lives in `config/settings.yaml`. The numbers you'll actually
change:

```yaml
business.max_buy_price_gbp: 25.00     # raise as capital grows
business.working_capital_gbp: 200.00
thresholds.min_net_profit_gbp: 8.00   # your "worth the trip" floor
thresholds.min_margin_pct: 35.0
thresholds.min_roi_pct: 100.0
pricing.list_price_percentile: 0.62   # lower = sells faster, earns less
pricing.active_to_sold_ratio: …       # retune from the weekly accuracy ratio
```

**Fees change.** Every fee block has a `last_verified` date, and the scripts
warn you when one is over six months old. When that fires, check the
platform's fee page and update the file. Profit maths built on a stale fee is
fiction.

---

## 5. Things this deliberately doesn't do

- **It never buys anything.** Every purchase is your decision.
- **It never uploads anything.** It generates CSVs; you upload them.
- **It doesn't scrape Vinted, Facebook or Gumtree.** No public API exists and
  their terms prohibit it — an automated account is a banned account, which
  costs you the business. eBay's API is the sanctioned route and it's free.
- **It won't price an item it has no evidence for.** You get `REVIEW` and an
  instruction, not a guess dressed up as a number.
- **It won't write a claim it can't support.** "Fully working" on an untested
  item, "100% authentic", "brand new" on a used piece — all blocked. That gate
  is the difference between a business and a series of refunds.

## 6. Limits worth knowing before you rely on it

- **eBay's free API returns active listings, not sold ones.** Asking prices
  skew high, because overpriced items are the ones that don't sell and
  therefore stay visible. The toolkit calibrates them down and never rates
  that data "high confidence" — but a saved Sold-items page is genuinely more
  accurate. Use the API to find deals; use saved pages to price them.
- **Bulk-upload templates drift.** eBay and Facebook validate against the
  template *you* download from your account. Run `make_listings.py --template`
  and diff the headers before your first upload.
- **The category IDs in the eBay export are blank** — you fill them once per
  category and reuse them.
- **Sold-comp matching is title-based.** If your comps file covers a different
  product, you'll get "0 match" rather than a wrong price. That's intentional.

## 7. Prompt library

`prompts/` holds copy-pasteable workflows: analysing one item, batching a
haul into listings, the daily run, the weekly review, and the honest position
on browser automation. Start with `prompts/03-daily-run.md`.
