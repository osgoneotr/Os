# Prompt 1 — Analyse a single item (from photos + price)

**Use this when:** you're standing in a charity shop or car boot with an item
in your hand and 30 seconds to decide.

**How:** paste the block below into Claude Code, attach the photos, fill the
three blanks. Nothing else needs changing.

---

```
Analyse this item for resale. I'm in Slough, UK, selling on Vinted first,
then eBay UK.

Item: <WHAT IT IS — brand and model if you can read them>
Asking price: £<PRICE>
Condition as I see it: <one line — wear, marks, missing parts>
[attach photos]

Do this in order:

1. IDENTIFY. From the photos and text, tell me the brand, model, era and
   size. For each one, say whether you can SEE it in a photo or are INFERRING
   it. Do not state an inference as a fact.

2. PRICE. Run:
     python3 scripts/analyze_item.py --title "<TITLE>" --price <PRICE> \
       --brand "<BRAND>" --model "<MODEL>" --size "<SIZE>" \
       --condition <new_with_tags|new_without_tags|excellent|good|fair|for_parts> \
       --category <trainers|clothing|retro_tech|audio> \
       --confirmed <attributes you can see in the photos> --listing
   If there are no comps for it, tell me exactly what to search on eBay
   (with "Sold items" ticked) so I can save the page into data/inbox/comps/
   and you can re-run.

3. ANSWER in this exact shape, nothing else:

   VERDICT: BUY / PASS / REVIEW
   WHY: two or three sentences, plain English.
   PROFIT: £low – £high after fees, postage and packaging.
   RISKS: the two things most likely to go wrong.
   MAX I SHOULD PAY: £X (the price at which this stops clearing my rules).

4. If it's a BUY, print the listing copy too.

Rules you must not break:
- If you can't verify something from a photo, say so rather than assuming it.
- Never write "authentic", "genuine" or "fully working" unless I have said I
  physically confirmed it.
- If the numbers are thin, say PASS. There's always another item.
```

---

## Follow-ups worth having ready

- `Is this a fake? Point at what in the photos supports your answer.`
- `What's the cheapest postage band this fits, and does that change the verdict?`
- `Same item but the seller will do £X — recalculate.`
