// Google Trends sourcing — uses the unofficial `google-trends-api` npm
// package (no account/key needed, but no SLA either; Google can change or
// rate-limit this at any time, which is why it's a "best-effort" signal, not
// the sole input to the trend score).
import googleTrends from 'google-trends-api';

export async function getSearchInterest(keyword: string): Promise<number> {
  try {
    const raw = await googleTrends.interestOverTime({
      keyword,
      startTime: new Date(Date.now() - 7 * 24 * 60 * 60 * 1000),
    });
    const parsed = JSON.parse(raw);
    const points = parsed?.default?.timelineData ?? [];
    if (points.length === 0) return 0;
    const latest = Number(points[points.length - 1]?.value?.[0] ?? 0);
    return latest; // 0-100, Google's own normalized scale
  } catch (err) {
    console.warn(`Google Trends lookup failed for "${keyword}":`, err);
    return 0; // fail soft — this source is best-effort
  }
}
