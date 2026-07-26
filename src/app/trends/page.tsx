'use client';

import { useEffect, useState } from 'react';
import { TrendRanking } from '@/types';
import { TrendCard } from '@/components/TrendCard';
import { TrendFilters, TrendFilterState } from '@/components/TrendFilters';

export default function TrendsPage() {
  const [filters, setFilters] = useState<TrendFilterState>({ brand: '', category: '', minScore: 0 });
  const [rankings, setRankings] = useState<TrendRanking[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const params = new URLSearchParams();
    if (filters.brand) params.set('brand', filters.brand);
    if (filters.category) params.set('category', filters.category);
    if (filters.minScore) params.set('minScore', String(filters.minScore));

    setLoading(true);
    fetch(`/api/trends?${params}`)
      .then((r) => r.json())
      .then((data) => setRankings(data.rankings ?? []))
      .finally(() => setLoading(false));
  }, [filters]);

  return (
    <main className="max-w-5xl mx-auto p-6">
      <h1 className="text-2xl font-bold mb-1">Trend Discovery</h1>
      <p className="text-gray-500 mb-6">
        What&apos;s in demand right now, ranked by search interest, sale velocity, and social buzz.
      </p>

      <TrendFilters value={filters} onChange={setFilters} />

      {loading ? (
        <p className="text-gray-400">Loading trends…</p>
      ) : rankings.length === 0 ? (
        <p className="text-gray-400">
          No trend data yet. Run the refresh job (see README) to populate this dashboard.
        </p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {rankings.map((t) => (
            <TrendCard key={`${t.brand_id}:${t.category_id}`} trend={t} />
          ))}
        </div>
      )}
    </main>
  );
}
