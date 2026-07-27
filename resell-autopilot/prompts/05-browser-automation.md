# Prompt 5 — Browser-assisted collection (comps and sourcing)

**Read this first:** the honest position on browser automation.

Vinted, Facebook Marketplace and Gumtree have no public listing API, and their
terms prohibit automated scraping. Building a bot to hammer them gets your
account banned — which costs you the business, not just the bot. eBay's Browse
API is the one properly sanctioned automated route, and it's free.

So the split this system uses is:

| Job | How |
|---|---|
| Price comps | eBay API (free), or you save a Sold-items page with Ctrl+S |
| Finding deals on eBay | eBay Browse API |
| Finding deals on FB/Gumtree | You browse; photograph or paste what you find |
| Listing on eBay | File Exchange CSV upload |
| Listing on Vinted | Copy-paste worksheet, ~40 seconds per item |
| Listing on Facebook | Catalogue CSV upload |

That's not a limitation of this toolkit. It's the actual ceiling, and anyone
selling you a "fully automated Vinted bot" is selling you a banned account.

---

## The two-minute comps routine (no API needed)

```
1. Go to ebay.co.uk, search the item exactly as you'd describe it.
2. Filters → tick "Sold items".
3. Ctrl+S (Cmd+S on Mac) → save as "Webpage, HTML only".
4. Save it into resell-autopilot/data/inbox/comps/ and name the file after the
   product: north-face-nuptse-700.html
5. Run any script. It finds the file automatically and matches it to the right
   item by name.
```

The filename matters — it's what the matcher uses to decide which item the
comps belong to.

---

## If you do use a browser automation tool or MCP browser plugin

Keep it to these three jobs, and configure it like this:

```
Task: collect sold-listing evidence for a product.

1. Navigate to ebay.co.uk and search "<product>".
2. Apply the "Sold items" filter.
3. Save the rendered page HTML to
   resell-autopilot/data/inbox/comps/<slug>.html
4. Stop. Do not click into listings, do not paginate more than 2 pages, do not
   run more than one search per 10 seconds.

Never: log in, place bids, send messages, create listings, or take any action
that changes state on the site. Read-only, at human pace, or not at all.
```

The rate limit isn't politeness — hammering eBay from a residential IP gets
that IP soft-blocked, and then even your normal browsing breaks.

## What to do instead of automating Vinted

Vinted's own app has a "sell similar" flow. Once you've listed one item from a
category, the next one takes under a minute using the worksheet CSV this
toolkit generates. Batch them: list ten items in one sitting on a Sunday
rather than one a day. That's where the real time saving is, and it doesn't
risk the account you depend on.
