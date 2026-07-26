// Price suggestion: blends recent price_comps (from eBay, see lib/trends/ebay.ts)
// with the current trend score for the brand+category. Comps drive the base
// range; a high trend score nudges the suggested range up slightly since
// demand is elevated right now.
import { createAdminClient } from '@/lib/supabase/server';
import { PriceSuggestion } from '@/types';

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

export async function suggestPrice(brandId: string, categoryId: string): Promise<PriceSuggestion | null> {
  const supabase = createAdminClient();

  const { data: comps } = await supabase
    .from('price_comps')
    .select('sold_price')
    .eq('brand_id', brandId)
    .eq('category_id', categoryId)
    .order('sold_at', { ascending: false })
    .limit(30);

  if (!comps?.length) return null;

  const prices = comps.map((c) => c.sold_price as number);
  const med = median(prices);

  const { data: trendRow } = await supabase
    .from('trend_signals')
    .select('value')
    .eq('brand_id', brandId)
    .eq('category_id', categoryId)
    .eq('source', 'composite')
    .order('captured_at', { ascending: false })
    .limit(1)
    .maybeSingle();

  const demandMultiplier = 1 + ((trendRow?.value ?? 0) / 100) * 0.15; // up to +15% when trend score is maxed

  return {
    low: Math.round(Math.min(...prices) * demandMultiplier),
    median: Math.round(med * demandMultiplier),
    high: Math.round(Math.max(...prices) * demandMultiplier),
    compsUsed: prices.length,
  };
}
