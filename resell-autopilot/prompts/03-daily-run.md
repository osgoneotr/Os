# Prompt 3 — Daily Run

**Use this when:** once a day, in the morning. Should take you 10 minutes.

Copy the block. That's the whole prompt — it doesn't change day to day.

---

```
Daily run.

1. cd resell-autopilot && python3 scripts/daily_run.py

2. Read the output and give me:
   - THE SHORTLIST: every BUY, one line each — item, buy price, expected net,
     where I'm selling it. Ranked by profit.
   - THE MAYBES: every REVIEW with the specific reason it isn't a BUY, and the
     single thing that would resolve it.
   - Total spend if I take the whole shortlist, and whether that fits inside
     my remaining working capital.

3. For any REVIEW that's only missing comps, give me the exact eBay search
   URLs (with "Sold items" ticked) so I can save the pages into
   data/inbox/comps/ and you can re-run in one go.

4. Tell me what's stale and what to do about it.

5. End with a three-line summary: what to buy, what to list, what to post.

Don't buy anything. Don't upload anything. Just tell me what's worth doing.
```

---

## When you're sourcing live on eBay instead of from a shopping trip

```
Daily run, eBay sourcing mode:

  python3 scripts/daily_run.py --source ebay --category clothing --top 15

Same output format as above. Flag any listing where the seller's photos don't
support the title — those are the mispriced ones worth bidding on, and also
the ones most likely to be a problem.
```

## The zero-typing version

Set it up once and never type it again:

```bash
# from the repo root
alias resell='cd ~/Os/resell-autopilot && python3 scripts/daily_run.py'
```

Or schedule it — in Claude Code, `/loop 1d run the Daily Run prompt` will keep
it going on its own and only interrupt you when there's something to approve.
