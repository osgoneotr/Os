# Prompt 4 — Weekly Review

**Use this when:** Sunday evening, once a week. Fifteen minutes.

This is the prompt that actually makes you money over time — the daily run
finds items, but this one tells you which *kind* of item to stop buying.

---

```
Weekly review.

1. First, let me record the week's sales. Ask me for anything that sold, then
   run for each:
     python3 scripts/report.py --mark <SKU> sold --price <£> --fees <£> --postage <£>

2. python3 scripts/report.py --dashboard

3. Give me:

   THE NUMBERS
   Revenue, realised profit, capital committed, capital free.
   Sell-through % and median days to sell against my 45-day target.

   WHAT'S WORKING
   Top categories by realised profit AND by ROI — say which if they disagree,
   because ROI is what matters when capital is the constraint, not profit.

   WHAT'S NOT
   Any category with 2+ sales and under 50% ROI. Say plainly whether to stop
   buying it.

   ESTIMATE ACCURACY
   The actual/estimated ratio. If it's under 0.9 my pricing is optimistic —
   tell me exactly which config number to change and to what.

   STALE STOCK
   Everything over 60 days with a specific action per item: cut to £X, bundle,
   move channel, or write off.

   NEXT WEEK
   Three concrete instructions. Not "source more" — "buy men's M/L Carhartt
   and North Face outerwear under £18, skip audio until the two amps sell".

4. Flag it if I'm near the £1,000 HMRC trading allowance or ~30 sales.
```

---

## Monthly, on top of the weekly

```
Monthly deep dive. Same data, but answer these instead:

1. What's my actual profit per hour, given roughly <N> hours spent this month?
2. Which single change would have made the biggest difference to that number?
3. Is my £25 per-item cap costing me money? Show me the REVIEW/PASS items
   that were rejected only because of it, and what they'd have made.
4. Should I raise working capital, given what's currently selling?
```
