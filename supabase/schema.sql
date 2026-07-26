-- Designer Resale Platform — core schema
-- Run this in the Supabase SQL editor (Project > SQL Editor > New query).

create extension if not exists "uuid-ossp";

-- ---------- Reference data ----------

create table brands (
  id uuid primary key default uuid_generate_v4(),
  name text not null unique,
  tier text not null default 'designer' check (tier in ('designer', 'premium', 'contemporary')),
  created_at timestamptz not null default now()
);

create table categories (
  id uuid primary key default uuid_generate_v4(),
  name text not null,
  parent_id uuid references categories(id) on delete set null,
  created_at timestamptz not null default now()
);

-- ---------- Trend discovery ----------

-- One row per (brand, category, source, day). Raw signal, not yet scored.
create table trend_signals (
  id uuid primary key default uuid_generate_v4(),
  brand_id uuid references brands(id) on delete cascade,
  category_id uuid references categories(id) on delete cascade,
  source text not null check (source in ('google_trends', 'ebay_sold', 'reddit_mentions', 'composite')),
  metric text not null,          -- e.g. 'search_interest', 'sold_count', 'mention_count'
  value numeric not null,
  captured_at date not null default current_date,
  created_at timestamptz not null default now()
);

create index idx_trend_signals_lookup on trend_signals (brand_id, category_id, captured_at desc);

-- Sold-price comparables, mainly from the eBay API.
create table price_comps (
  id uuid primary key default uuid_generate_v4(),
  brand_id uuid references brands(id) on delete cascade,
  category_id uuid references categories(id) on delete cascade,
  source text not null default 'ebay',
  title text,
  sold_price numeric not null,
  currency text not null default 'USD',
  condition text,
  sold_at date,
  external_url text,
  created_at timestamptz not null default now()
);

create index idx_price_comps_lookup on price_comps (brand_id, category_id, sold_at desc);

-- ---------- Users & listings (single-seller MVP: users table is minimal) ----------

create table users (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  stripe_account_id text,
  created_at timestamptz not null default now()
);

create table listings (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid not null references users(id) on delete cascade,
  brand_id uuid references brands(id),
  category_id uuid references categories(id),
  title text not null,
  description text,
  condition text not null check (condition in ('new_with_tags', 'excellent', 'good', 'fair')),
  price numeric not null,
  suggested_price_low numeric,
  suggested_price_high numeric,
  status text not null default 'draft' check (status in ('draft', 'active', 'sold', 'archived')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table listing_photos (
  id uuid primary key default uuid_generate_v4(),
  listing_id uuid not null references listings(id) on delete cascade,
  url text not null,
  position int not null default 0
);

-- Self-attestation / authenticity checklist (see README for why this isn't
-- full third-party authentication in the MVP).
create table auth_requests (
  id uuid primary key default uuid_generate_v4(),
  listing_id uuid not null references listings(id) on delete cascade,
  status text not null default 'self_attested' check (status in ('none', 'self_attested', 'pending_manual', 'verified')),
  notes text,
  created_at timestamptz not null default now()
);

-- Tracks each cross-post/export of a listing to an external marketplace.
create table external_listings (
  id uuid primary key default uuid_generate_v4(),
  listing_id uuid not null references listings(id) on delete cascade,
  marketplace text not null check (marketplace in ('ebay', 'depop', 'vinted')),
  method text not null check (method in ('api', 'manual_export')),
  status text not null default 'pending' check (status in ('pending', 'posted', 'failed', 'exported')),
  external_id text,
  external_url text,
  created_at timestamptz not null default now()
);

-- ---------- Row Level Security ----------
-- Single-seller MVP: every listing belongs to auth.uid(). Tighten further if
-- you ever add multiple sellers.

alter table listings enable row level security;
alter table listing_photos enable row level security;
alter table auth_requests enable row level security;
alter table external_listings enable row level security;

create policy "owner can manage own listings" on listings
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy "owner can manage own listing photos" on listing_photos
  for all using (
    exists (select 1 from listings l where l.id = listing_photos.listing_id and l.user_id = auth.uid())
  );

create policy "owner can manage own auth requests" on auth_requests
  for all using (
    exists (select 1 from listings l where l.id = auth_requests.listing_id and l.user_id = auth.uid())
  );

create policy "owner can manage own external listings" on external_listings
  for all using (
    exists (select 1 from listings l where l.id = external_listings.listing_id and l.user_id = auth.uid())
  );

-- trend_signals, price_comps, brands, categories are read-only reference/market
-- data — no RLS needed, but only the service role (server-side) should write to them.
