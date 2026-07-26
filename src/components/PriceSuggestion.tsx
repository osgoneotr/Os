import { PriceSuggestion as PriceSuggestionType } from '@/types';

export function PriceSuggestion({
  suggestion,
  onUse,
}: {
  suggestion: PriceSuggestionType | null;
  onUse: (price: number) => void;
}) {
  if (!suggestion) {
    return (
      <p className="text-xs text-gray-400">
        No comps yet for this brand/category — run the trend refresh job first, or set a price manually.
      </p>
    );
  }

  return (
    <div className="rounded border border-indigo-100 bg-indigo-50 p-3 text-sm">
      <p className="font-medium text-indigo-700">
        Suggested range: ${suggestion.low} – ${suggestion.high}
      </p>
      <p className="text-xs text-indigo-500">
        Based on {suggestion.compsUsed} comparable listings, adjusted for current demand.
      </p>
      <button
        type="button"
        onClick={() => onUse(suggestion.median)}
        className="mt-2 text-xs underline text-indigo-600"
      >
        Use median (${suggestion.median})
      </button>
    </div>
  );
}
