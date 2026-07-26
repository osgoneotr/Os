// Daily trend refresh job. Trigger via Vercel Cron (see vercel.json) or call
// manually while developing: GET /api/trends/refresh
//
// For every tracked brand x category pair: pulls search interest (Google
// Trends), active-listing price/volume (eBay Browse API), and mention count
// (Reddit), stores the raw signals + a composite score, and stores a few
// price comps for the listing flow's price suggestion.
import { NextResponse } from 'next/server';
import { createAdminClient } from '@/lib/supabase/server';
import { getSearchInterest } from '@/lib/trends/googleTrends';
import { searchActiveListings, estimateSoldVelocity } from '@/lib/trends/ebay';
import { getMentionCount } from '@/lib/trends/reddit';
import { scoreTrends, RawSignal } from '@/lib/trends/score';

export const maxDuration = 60;

export async function GET(request: Request) {
  // Simple shared-secret check so this endpoint can't be triggered by anyone.
  const auth = request.headers.get('authorization');
  if (process.env.CRON_SECRET && auth !== `Bearer ${process.env.CRON_SECRET}`) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  const supabase = createAdminClient();

  const { data: brands } = await supabase.from('brands').select('id, name');
  const { data: categories } = await supabase.from('categories').select('id, name');
  if (!brands?.length || !categories?.length) {
    return NextResponse.json({ error: 'Seed brands and categories first' }, { status: 400 });
  }

  const rawSignals: RawSignal[] = [];
  const today = new Date().toISOString().slice(0, 10);

  for (const brand of brands) {
    for (const category of categories) {
      const keyword = `${brand.name} ${category.name}`;

      const [searchInterest, mentionCount, ebaySamples] = await Promise.all([
        getSearchInterest(keyword).catch(() => 0),
        getMentionCount(brand.name).catch(() => 0),
        searchActiveListings(brand.name, category.name).catch(() => []),
      ]);
      const soldVelocity = estimateSoldVelocity(ebaySamples);

      rawSignals.push({
        brandId: brand.id,
        categoryId: category.id,
        searchInterest,
        soldVelocity,
        mentionCount,
      });

      await supabase.from('trend_signals').insert([
        { brand_id: brand.id, category_id: category.id, source: 'google_trends', metric: 'search_interest', value: searchInterest, captured_at: today },
        { brand_id: brand.id, category_id: category.id, source: 'ebay_sold', metric: 'sold_count', value: soldVelocity, captured_at: today },
        { brand_id: brand.id, category_id: category.id, source: 'reddit_mentions', metric: 'mention_count', value: mentionCount, captured_at: today },
      ]);

      if (ebaySamples.length) {
        await supabase.from('price_comps').insert(
          ebaySamples.slice(0, 10).map((s) => ({
            brand_id: brand.id,
            category_id: category.id,
            source: 'ebay',
            title: s.title,
            sold_price: s.price,
            currency: s.currency,
            sold_at: today,
            external_url: s.url,
          }))
        );
      }
    }
  }

  const scored = scoreTrends(rawSignals);
  await supabase.from('trend_signals').insert(
    scored.map((s) => ({
      brand_id: s.brandId,
      category_id: s.categoryId,
      source: 'composite',
      metric: 'score',
      value: s.score,
      captured_at: today,
    }))
  );

  return NextResponse.json({ refreshed: scored.length, date: today });
}
