# Business Blueprint — UK reselling, £200 capital, near-zero hours

## Business Profile

Set from your answers, 27 July 2026:

| | |
|---|---|
| **Location** | Slough / West London, UK |
| **Currency** | GBP |
| **Capital** | £0–200 |
| **Channels, ranked** | 1. Vinted 2. eBay UK 3. Facebook Marketplace 4. Gumtree *(sourcing only)* |
| **Categories** | 1. Trainers & branded clothing 2. Retro tech & audio |
| **Accounts held** | None yet — everything starts from zero, all outputs default to local CSV |
| **Stack** | Standalone Python, no build step, no server |
| **Time budget** | Near zero: approve purchases, upload CSVs, post parcels |

---

## Part A — The model, and why this one

**Local arbitrage → online resale.** Buy physical goods cheaply in person
(charity shops, car boots, Facebook/Gumtree collection-only listings around
Slough, Datchet, Langley, Reading), sell them nationally on Vinted and eBay.

Three reasons this is the right model for your constraints, not just a
plausible one:

1. **It's the only model where £200 is enough.** Retail arbitrage needs stock
   at retail prices. Dropshipping needs ad spend. Charity-shop sourcing has an
   average unit cost of £5–15, so £200 buys 15–30 units — a real portfolio, and
   enough throughput to learn what sells before the money runs out.

2. **Your supply is genuinely mispriced.** A charity shop volunteer prices an
   Arc'teryx shell at £15 because it's "a coat". That gap isn't a market
   inefficiency someone will arbitrage away — it's structural, permanent, and
   it exists within a 20-minute drive of you.

3. **Geography is on your side.** Slough sits between high-income catchment
   (Windsor, Gerrards Cross, Beaconsfield charity shops receive genuinely good
   donations) and the M4/Elizabeth line for collection-only bargains across
   West London. This is a materially better sourcing base than most of the UK.

**What this model is not:** passive. The automation removes the research,
pricing and writing — which is 80% of the hours. It cannot remove going to
shops, and it cannot remove going to the Post Office.

### Concrete parameters

| Parameter | Target | Why |
|---|---|---|
| Buy price | £3–25/item | Caps the loss on any single mistake at the price of lunch |
| List price | £15–70/item | Below £15 postage eats the margin; above £70 needs capital you don't have |
| Gross margin | ≥35% net of all fees | Below this, one return wipes out three sales |
| ROI | ≥100% (double your money) | With £200 of capital, ROI matters more than profit per item |
| Absolute profit floor | £8/item | Under £8 the trip to the post box isn't worth it |
| Sell-through | ≤45 days | Dead stock is the actual failure mode of reselling, not bad buying |
| Volume target | 10–20 listings live by week 4 | Sales scale with listings, roughly linearly, up to ~100 |

### Sourcing channels, in order of yield per hour

1. **Charity shops** — Windsor, Maidenhead, Gerrards Cross, Beaconsfield.
   Higher-income donation catchments. Go Tuesday–Thursday mornings after the
   weekend donations are processed.
2. **Car boots** — Denham, Dunstable, Chertsey on Sundays. Highest yield per
   hour, worst weather dependency. Arrive at opening.
3. **Facebook Marketplace / Gumtree, collection only** — filtered to 15 miles.
   Collection-only excludes most competition; you have a car-boot-sized market
   available from your sofa.
4. **eBay, misspelling searches** — "carhart", "north fase", "techincs". Items
   invisible to every normal search, and so cheap. This is the one channel the
   toolkit automates end to end.

### Why Vinted first, given you have no accounts

Vinted takes **zero seller fees** and the buyer pays the postage — so a £35
Vinted sale nets you £35 minus a mailing bag. The equivalent eBay sale nets
you £31–32 once you've absorbed the label. On clothing and trainers, Vinted's
audience is also younger and larger. eBay's role in your business is as the
**pricing oracle** — it's the only platform with a public API and real sold
history — plus the channel for anything Vinted doesn't cover (electronics,
audio, anything not wearable).

That split is the core architectural insight here: **price on eBay, sell on
Vinted.**

---

## Part B — System architecture

```
                        ┌───────────────────────────────┐
   YOU, in a shop  ───► │  data/inbox/*.csv             │
   or on eBay          │  (title, price, condition…)   │
                        └──────────────┬────────────────┘
                                       │
   eBay Browse API ────────────────────┤
   (free, optional)                    │
                                       ▼
                        ┌───────────────────────────────┐
                        │  1. SOURCING ENGINE           │
                        │  sourcing.py                  │
                        │  · loads candidates           │
                        │  · infers brand/size/category │
                        │  · marks every guess as a     │
                        │    guess                      │
                        └──────────────┬────────────────┘
                                       │  Candidate
                                       ▼
   data/inbox/comps/  ──►  ┌───────────────────────────────┐
   · saved eBay Sold      │  2. COMPS RESOLVER            │
     pages (Ctrl+S)       │  CompsResolver                │
   · sold CSVs            │  sold CSV > Insights API >    │
   · eBay API             │  active listings > nothing    │
                          │  matched per item by title    │
                          └──────────────┬────────────────┘
                                         │  Comps (n, median, confidence)
                                         ▼
                        ┌───────────────────────────────┐
                        │  3. PRICING & PROFIT          │
                        │  pricing.py + fees.py         │
                        │  · trim outliers              │
                        │  · calibrate asking→sold      │
                        │  · condition & channel adj.   │
                        │  · fees + postage + packaging │
                        │  · BUY / PASS / REVIEW        │
                        └──────────────┬────────────────┘
                                       │  Analysis
                                       ▼
                        ┌───────────────────────────────┐
                        │  4. LISTING ENGINE            │
                        │  listings.py                  │
                        │  · 2 titles, bullets, tags    │
                        │  ┌─────────────────────────┐  │
                        │  │ QUALITY GATE            │  │
                        │  │ strips unsupported      │  │
                        │  │ claims, hedges guesses  │  │
                        │  └─────────────────────────┘  │
                        └──────────────┬────────────────┘
                                       │  ListingPackage
                                       ▼
                        ┌───────────────────────────────┐
                        │  5. EXPORTERS  exporters.py   │
                        │  canonical · vinted · ebay ·  │
                        │  facebook                     │
                        └──────────────┬────────────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              ▼                        ▼                        ▼
      data/out/*.csv          data/ledger.csv          Google Sheets
      (upload these)          (source of truth)        (optional)
                                       │
                                       ▼
                        ┌───────────────────────────────┐
                        │  6. REPORTS  reports.py       │
                        │  profit · sell-through ·      │
                        │  stale stock · estimate       │
                        │  accuracy · HMRC watch        │
                        └───────────────────────────────┘
```

**Data flow in one sentence:** items enter as rows, acquire market evidence,
become priced decisions, become written listings, become CSVs you upload, and
end as ledger entries that tell you what to buy next.

### The one design rule everything follows

> **A number never appears without the evidence behind it.**

Every price carries its sample size, its source and a confidence label. Thin
evidence produces `REVIEW`, never a confident `BUY`. Missing evidence produces
"not priced" rather than £0.00. Attributes that were guessed from a title are
marked as guesses and get hedged in the listing copy. The system is built to
be *uncertain out loud*, because the failure mode of a reselling tool isn't
being wrong — it's being confidently wrong with your money.

---

## Part C — What's automated, honestly

| Step | Automated? | Reality |
|---|---|---|
| Find candidates on eBay | **Yes** | Browse API, seeded misspelling searches |
| Find candidates in shops | No | You go. Nothing changes that. |
| Price comps | **Yes** | eBay API, or a saved page you drop in a folder |
| Profit maths | **Yes** | Fees, postage band, packaging, all channels |
| Buy/pass decision | **Yes** | You approve; the rule does the filtering |
| Listing copy | **Yes** | Two titles, bullets, keywords, prices |
| Safety/authenticity check | **Yes** | Runs before anything reaches a CSV |
| Upload to eBay | Semi | File Exchange CSV — one upload, many items |
| Upload to Facebook | Semi | Catalogue CSV |
| Upload to Vinted | No | No API exists. Worksheet gets it to ~40s/item |
| Packing and posting | No | You. |
| Bookkeeping | **Yes** | The ledger is your HMRC record |

**Realistic weekly time at 15 live listings:** ~2 hours sourcing (the fun
part), ~30 minutes listing, ~30 minutes posting, ~15 minutes reviewing. The
automation eliminates roughly 4–5 hours of research and writing per week.

---

## Part D — What can go wrong

The three things that actually kill small reselling operations, and what this
system does about each:

1. **Dead stock.** You buy 20 things, sell 6, and your capital is in a
   cupboard. → Sell-through tracking, 60-day stale alerts with a specific
   price action per item, and a working-capital ceiling that stops you buying
   when you're fully committed.

2. **A "not as described" case.** One undisclosed flaw or one "fully working"
   on an untested amp, and you're refunding plus paying return postage plus
   carrying a defect on a new account. → The quality gate. It won't let those
   words through, and it makes you state flaws.

3. **Pricing off asking prices.** eBay's free API shows active listings, and
   overpriced items are precisely the ones that stay visible. Averaging them
   overstates value by 20–30%. → Calibration factor, never-high confidence on
   that path, and the weekly estimate-accuracy ratio that tells you to retune it.

### Compliance, stated plainly

- **HMRC.** Over £1,000 gross in a tax year means registering for Self
  Assessment. Vinted and eBay report seller data to HMRC above roughly 30
  sales or £1,700/year, whether or not you owe anything. The ledger is your
  record — keep it. *This is a reminder, not tax advice.*
- **Counterfeits.** Selling one is an offence, not a policy violation. The
  blocklist catches obvious signals; your eyes catch the rest.
- **Prohibited items.** Used helmets and child car seats are blocked by
  default (unknown impact history), along with the usual restricted goods.
- **Terms of service.** No scraping of Vinted, Facebook or Gumtree. eBay's API
  is the sanctioned automated route and it's free. See
  `prompts/05-browser-automation.md`.
