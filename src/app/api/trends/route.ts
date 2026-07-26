// GET /api/trends?brand=&category=&minScore=&days=30
// Returns the latest composite trend score per brand+category, plus a
// history array for the sparkline. Reads pre-computed rows written by
// /api/trends/refresh — this route does no external API calls itself.
import { NextResponse } from 'next/server';
import { createAdminClient } from '@/lib/supabase/server';
import { TrendRanking } from '@/types';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const brandFilter = searchParams.get('brand');
  const categoryFilter = searchParams.get('category');
  const minScore = Number(searchParams.get('minScore') ?? 0);
  const days = Number(searchParams.get('days') ?? 30);

  const supabase = createAdminClient();
  const since = new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);

  const { data: rows, error } = await supabase
    .from('trend_signals')
    .select('brand_id, category_id, source, metric, value, captured_at, brands(name), categories(name)')
    .eq('source', 'composite')
    .gte('captured_at', since)
    .order('captured_at', { ascending: true });

  if (error) return NextResponse.json({ error: error.message }, { status: 500 });

  const byPair = new Map<string, TrendRanking>();

  for (const row of rows ?? []) {
    const key = `${row.brand_id}:${row.category_id}`;
    const brandName = (row as any).brands?.name ?? 'Unknown';
    const categoryName = (row as any).categories?.name ?? 'Unknown';

    if (!byPair.has(key)) {
      byPair.set(key, {
        brand_id: row.brand_id,
        brand_name: brandName,
        category_id: row.category_id,
        category_name: categoryName,
        score: 0,
        driver: 'search_spike',
        search_interest: 0,
        sold_velocity: 0,
        mention_count: 0,
        history: [],
      });
    }

    const entry = byPair.get(key)!;
    entry.history.push({ date: row.captured_at, score: row.value });
    entry.score = row.value; // rows are ascending by date, so last write wins = latest
  }

  let rankings = Array.from(byPair.values()).filter((r) => r.score >= minScore);
  if (brandFilter) rankings = rankings.filter((r) => r.brand_name.toLowerCase().includes(brandFilter.toLowerCase()));
  if (categoryFilter) rankings = rankings.filter((r) => r.category_name.toLowerCase().includes(categoryFilter.toLowerCase()));

  rankings.sort((a, b) => b.score - a.score);

  return NextResponse.json({ rankings });
}
