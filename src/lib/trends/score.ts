// Composite trend score: blends three normalized signals into one 0-100
// number so the dashboard can rank by a single value instead of three.
// Weights: search interest 40%, sold/listing velocity 40%, social buzz 20%.
// See README for why these three sources and this refresh cadence.

export interface RawSignal {
  brandId: string;
  categoryId: string;
  searchInterest: number; // already 0-100 (Google Trends scale)
  soldVelocity: number; // raw count, needs normalizing against the batch
  mentionCount: number; // raw count, needs normalizing against the batch
}

export interface ScoredTrend extends RawSignal {
  score: number;
  driver: 'search_spike' | 'sold_velocity' | 'social_buzz';
}

function normalize(value: number, max: number): number {
  if (max <= 0) return 0;
  return Math.min(100, (value / max) * 100);
}

export function scoreTrends(signals: RawSignal[]): ScoredTrend[] {
  const maxVelocity = Math.max(1, ...signals.map((s) => s.soldVelocity));
  const maxMentions = Math.max(1, ...signals.map((s) => s.mentionCount));

  return signals.map((s) => {
    const searchNorm = s.searchInterest; // already 0-100
    const velocityNorm = normalize(s.soldVelocity, maxVelocity);
    const mentionNorm = normalize(s.mentionCount, maxMentions);

    const score = searchNorm * 0.4 + velocityNorm * 0.4 + mentionNorm * 0.2;

    const parts: [ScoredTrend['driver'], number][] = [
      ['search_spike', searchNorm],
      ['sold_velocity', velocityNorm],
      ['social_buzz', mentionNorm],
    ];
    const driver = parts.reduce((a, b) => (b[1] > a[1] ? b : a))[0];

    return { ...s, score: Math.round(score * 10) / 10, driver };
  });
}
