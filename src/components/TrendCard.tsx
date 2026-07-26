import { TrendRanking } from '@/types';

const driverLabel: Record<TrendRanking['driver'], string> = {
  search_spike: 'Search interest spiking',
  sold_velocity: 'Selling fast right now',
  social_buzz: 'Getting talked about',
};

export function TrendCard({ trend }: { trend: TrendRanking }) {
  return (
    <div className="rounded-lg border border-gray-200 p-4 shadow-sm hover:shadow-md transition-shadow">
      <div className="flex items-baseline justify-between">
        <h3 className="font-semibold text-lg">{trend.brand_name}</h3>
        <span className="text-2xl font-bold text-indigo-600">{Math.round(trend.score)}</span>
      </div>
      <p className="text-sm text-gray-500">{trend.category_name}</p>
      <p className="text-xs text-indigo-500 mt-2">{driverLabel[trend.driver]}</p>

      <div className="mt-3 flex gap-3 text-xs text-gray-400">
        <span>Search {Math.round(trend.search_interest)}</span>
        <span>Velocity {Math.round(trend.sold_velocity)}</span>
        <span>Mentions {Math.round(trend.mention_count)}</span>
      </div>

      {trend.history.length > 1 && (
        <Sparkline points={trend.history.map((h) => h.score)} />
      )}
    </div>
  );
}

function Sparkline({ points }: { points: number[] }) {
  const max = Math.max(...points, 1);
  const w = 120;
  const h = 28;
  const step = w / (points.length - 1);
  const path = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'} ${i * step} ${h - (p / max) * h}`)
    .join(' ');

  return (
    <svg width={w} height={h} className="mt-2 text-indigo-400">
      <path d={path} fill="none" stroke="currentColor" strokeWidth={1.5} />
    </svg>
  );
}
