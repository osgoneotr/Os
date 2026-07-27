# Prompt 2 — Turn a batch of items into listings

**Use this when:** you've got home with 5–15 items and want them all listed.

---

```
Here's today's haul. Turn it into upload-ready listings.

[paste a list, one item per line: what it is, what you paid, condition,
 anything wrong with it. Attach the photos, named so I can tell which is which.]

Steps:

1. Write these into a CSV at data/inbox/haul-<today's date>.csv using the
   columns the loader understands: title, price, brand, model, size, colour,
   material, condition, defects, category, notes, photos.
   Normalise my sloppy condition words to the vocabulary in models.py.

2. For anything you don't have comps for, tell me the exact eBay search to run
   with "Sold items" ticked, and wait while I save the pages into
   data/inbox/comps/. Do not guess a price to fill the gap.

3. Run:
     python3 scripts/source_deals.py --source inbox --category <category>
   then mark the ones I confirm I've bought:
     python3 scripts/report.py --mark <SKU> bought
   then:
     python3 scripts/make_listings.py --status bought --set-status listed

4. Show me a table: SKU | item | buy | list | net profit | channel | gate.
   Below it, list every quality-gate warning grouped by item.

5. For anything held back by the gate, tell me the single photo I need to take
   to unblock it (usually the brand label or the size tag).

Rules:
- Every listing gets two titles: one SEO-structured, one style-led.
- eBay titles must be ≤80 characters. Vinted ≤100.
- Descriptions are bullets: size, material, features, condition, flaws.
- Flaws are never omitted. An undisclosed mark is a refund plus a defect.
- 10–20 keywords, most specific first.
- No claim goes in that the photos don't support.
```

---

## Variant: I only want the copy, not the pipeline

```
Just write the listing copy for these <N> items — two titles, bullets,
keywords, list price and floor price each. Skip the scripts. Same rules:
nothing claimed that the photos don't show, eBay titles ≤80 chars.
```
