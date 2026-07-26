# Designer Resale Platform

A trend-discovery dashboard + resell-listing tool for designer clothing, built
for a solo seller (not a multi-vendor marketplace).

## What's here

- **Trend Discovery** (`/trends`) — ranks brand+category combos by a
  composite score blending Google Trends search interest, eBay active-listing
  velocity, and Reddit mention count.
- **Resell Listing** (`/listings/new`) — upload photos, auto-generate a
  title/description/price suggestion, save as a draft, then publish: a real
  API push to eBay, or a copy-ready export for Depop/Vinted.
- Direct in-site checkout via Stripe (single-seller, no Connect/escrow needed).

## Cost / account summary (flagged up front)

| Thing | Cost | Signup needed? |
|---|---|---|
| Vercel hosting | Free tier | Free account |
| Supabase (DB, auth, storage) | Free tier | Free account |
| Google Trends data | Free | None (unofficial package, no guarantees) |
| eBay Browse API (comps) | Free tier | **Free** developer account at developer.ebay.com |
| Reddit API (buzz signal) | Free | **Free** "script" app at reddit.com/prefs/apps |
| eBay listing publish (real API) | Free | Free dev account **+** eBay seller account **+** business policies configured **+** user OAuth consent (see below) |
| Depop/Vinted publish | N/A | **Not possible via API** — no public listing-creation API exists for either. This app instead generates a copy-ready export. |
| AI title/description | ~fractions of a cent per listing | Only if you set `ANTHROPIC_API_KEY`. Leave blank to use the free template generator. |
| Stripe (direct sales) | Free to integrate, ~2.9%+30¢ per sale | Free account |

Everything works at **$0/month** except per-sale Stripe fees (only when you
actually sell something) and optional AI calls.

## Setup

1. **Install dependencies**
   ```bash
   npm install
   ```

2. **Create a free Supabase project** at supabase.com, then:
   - Go to SQL Editor > New query, paste the contents of `supabase/schema.sql`, run it.
   - Go to Storage > create a new **public** bucket named `listing-photos`.
   - Copy your Project URL, anon key, and service role key into `.env.local`
     (copy `.env.example` to `.env.local` first).

3. **Seed a few brands/categories** so the trend dashboard has something to
   compute. In the Supabase SQL editor:
   ```sql
   insert into brands (name) values ('Chanel'), ('Miu Miu'), ('The Row'), ('Celine');
   insert into categories (name) values ('Handbags'), ('Sneakers'), ('Outerwear'), ('Denim');
   ```

4. **(Optional but recommended) Get free eBay API keys** for real price comps:
   - Sign up at developer.ebay.com (free).
   - Create an app, grab the Client ID/Secret from the "Keys" tab, put them in `.env.local`.

5. **(Optional) Get free Reddit API keys** for the social-buzz signal:
   - Go to reddit.com/prefs/apps > "create app" > type "script".
   - Put the client id/secret in `.env.local`.

6. **Run it locally**
   ```bash
   npm run dev
   ```
   Visit http://localhost:3000.

7. **Populate trend data** by hitting the refresh endpoint once manually:
   ```bash
   curl http://localhost:3000/api/trends/refresh
   ```
   In production this runs automatically once a day via `vercel.json`'s cron
   config (daily is the realistic cadence given free-tier API limits — see
   the architecture notes below).

8. **Deploy** — push to GitHub, import into Vercel, add the same env vars
   from `.env.local` to the Vercel project settings, deploy.

## Connecting eBay for real listings (optional, more setup than the rest)

Publishing *to* eBay (not just reading comps) requires:
1. An eBay seller account.
2. Business policies (payment/fulfillment/return) configured on that account
   under Account Settings — the Sell API references these by ID, they can't
   be created purely via API.
3. A user-level OAuth token (the eBay OAuth consent flow, not the app-only
   token used for browsing). The easiest way to get a first token while
   developing is eBay's OAuth token generator in the developer portal's "User
   Tokens" tab.
4. Put the resulting token and your three policy IDs into `.env.local`.

Until you do this, the "Publish to eBay" button will return a clear error
telling you what's missing rather than failing silently.

## Why Depop/Vinted can't auto-post

Neither platform publishes a public API for third-party apps to create
listings on a seller's behalf. This isn't a gap in this codebase — it's a
platform restriction. The listing flow instead produces a **copy-ready
export** (title, description, price, photo links) you paste into their app
in under a minute. If either platform ever opens a public listing API, only
`src/lib/marketplaces/exportPackage.ts` needs to change.

## Hybrid selling — one real risk to know about

Because listings can sell both on your own site (Stripe) and on eBay, or be
hand-posted to Depop/Vinted, there's **no automatic inventory sync across
channels** in this MVP. If an item sells on eBay, you must manually mark it
sold/remove it elsewhere to avoid a double-sale. The `external_listings`
table tracks where an item was pushed, so building a "mark sold everywhere"
button is a natural next step once this matters to you.

## Authenticity — why there's no full verification flow

Real physical authentication (like Vestiaire Collective or eBay's Authenticity
Guarantee) requires human authenticators or hardware and isn't realistic for
a solo seller to build. The MVP instead has sellers self-attest against a
checklist (`auth_requests` table, status `self_attested`), surfaced as a
"Seller-Verified" badge — not "Authenticated." A paid third-party service
(e.g. Entrupy, per-item fee) could be wired in later for high-value pieces
without changing the data model.

## Architecture notes

- **Trend score** = 40% normalized Google Trends search interest + 40%
  normalized eBay active-listing volume (a free-tier proxy for sold
  velocity — true sold data requires eBay's Marketplace Insights API, which
  needs additional approval beyond the free developer account) + 20%
  normalized Reddit mention count. See `src/lib/trends/score.ts`.
- **Refresh cadence**: daily. Vercel's free Cron tier only allows daily jobs
  anyway, and Google Trends/Reddit rate limits make more-frequent polling
  risky on the unofficial/free paths.
- **Single-seller RLS**: every listing is scoped to `auth.uid()`. If you ever
  add multiple sellers, tighten the `users`/`listings` relationship and
  policies accordingly.

## Project structure

```
src/
  app/
    trends/page.tsx              Trend Discovery dashboard
    listings/new/page.tsx        Resell listing flow
    api/
      trends/route.ts            GET ranked trends
      trends/refresh/route.ts    Daily job: pulls signals, writes scores
      ai/generate-listing/route.ts   Title/description/price generation
      listings/route.ts          Create a listing
      listings/[id]/publish/route.ts  Push to eBay or export for Depop/Vinted
      checkout/route.ts          Stripe Checkout session
      stripe/webhook/route.ts    Marks listing sold on payment
  components/                    TrendCard, TrendFilters, ListingForm, etc.
  lib/
    supabase/                    Browser + server Supabase clients
    trends/                      ebay.ts, googleTrends.ts, reddit.ts, score.ts
    ai/generateListingContent.ts Template + optional Claude generation
    marketplaces/                ebay.ts (real API), exportPackage.ts (Depop/Vinted)
    pricing.ts                   Price suggestion from comps + trend score
    stripe.ts
  types/index.ts
supabase/schema.sql               Full DB schema + RLS policies
vercel.json                       Daily cron config for trend refresh
```
