'use client';

export interface TrendFilterState {
  brand: string;
  category: string;
  minScore: number;
}

export function TrendFilters({
  value,
  onChange,
}: {
  value: TrendFilterState;
  onChange: (next: TrendFilterState) => void;
}) {
  return (
    <div className="flex flex-wrap gap-3 mb-6">
      <input
        className="border rounded px-3 py-2 text-sm"
        placeholder="Filter by brand"
        value={value.brand}
        onChange={(e) => onChange({ ...value, brand: e.target.value })}
      />
      <input
        className="border rounded px-3 py-2 text-sm"
        placeholder="Filter by category"
        value={value.category}
        onChange={(e) => onChange({ ...value, category: e.target.value })}
      />
      <label className="flex items-center gap-2 text-sm text-gray-500">
        Min score
        <input
          type="range"
          min={0}
          max={100}
          value={value.minScore}
          onChange={(e) => onChange({ ...value, minScore: Number(e.target.value) })}
        />
        {value.minScore}
      </label>
    </div>
  );
}
