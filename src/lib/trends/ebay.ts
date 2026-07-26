// eBay sourcing for trend data.
//
// IMPORTANT / FLAGGED: true "sold item" history lives behind eBay's
// Marketplace Insights API, which requires an extra approval step beyond a
// free developer account (https://developer.ebay.com — sign up is free, but
// Marketplace Insights access is not automatic). Until/unless you get that
// approval, this uses the Browse API (freely available on the basic free
// tier) to pull *active* listing prices for a brand+category as a proxy for
// market price and sold velocity is approximated from result-count deltas.
// Swap `searchActiveListings` for a Marketplace Insights call once approved.

const EBAY_OAUTH_URL = 'https://api.ebay.com/identity/v1/oauth2/token';
const EBAY_BROWSE_URL = 'https://api.ebay.com/buy/browse/v1/item_summary/search';

let cachedToken: { token: string; expiresAt: number } | null = null;

async function getAppToken(): Promise<string> {
  if (cachedToken && cachedToken.expiresAt > Date.now()) {
    return cachedToken.token;
  }

  const clientId = process.env.EBAY_CLIENT_ID;
  const clientSecret = process.env.EBAY_CLIENT_SECRET;
  if (!clientId || !clientSecret) {
    throw new Error(
      'Missing EBAY_CLIENT_ID / EBAY_CLIENT_SECRET. Sign up for a free eBay developer account at developer.ebay.com to get these.'
    );
  }

  const basicAuth = Buffer.from(`${clientId}:${clientSecret}`).toString('base64');
  const res = await fetch(EBAY_OAUTH_URL, {
    method: 'POST',
    headers: {
      Authorization: `Basic ${basicAuth}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: new URLSearchParams({
      grant_type: 'client_credentials',
      scope: 'https://api.ebay.com/oauth/api_scope',
    }),
  });

  if (!res.ok) {
    throw new Error(`eBay OAuth failed: ${res.status} ${await res.text()}`);
  }

  const data = await res.json();
  cachedToken = { token: data.access_token, expiresAt: Date.now() + (data.expires_in - 60) * 1000 };
  return cachedToken.token;
}

export interface EbayListingSample {
  title: string;
  price: number;
  currency: string;
  url: string;
}

export async function searchActiveListings(
  brandName: string,
  categoryName: string,
  limit = 50
): Promise<EbayListingSample[]> {
  const token = await getAppToken();
  const query = `${brandName} ${categoryName}`;
  const params = new URLSearchParams({
    q: query,
    limit: String(limit),
    filter: 'buyingOptions:{FIXED_PRICE}',
  });

  const res = await fetch(`${EBAY_BROWSE_URL}?${params}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      'X-EBAY-C-MARKETPLACE-ID': 'EBAY_US',
    },
  });

  if (!res.ok) {
    throw new Error(`eBay search failed: ${res.status} ${await res.text()}`);
  }

  const data = await res.json();
  return (data.itemSummaries ?? []).map((item: any) => ({
    title: item.title,
    price: Number(item.price?.value ?? 0),
    currency: item.price?.currency ?? 'USD',
    url: item.itemWebUrl,
  }));
}

/** Sold velocity proxy: how many active results exist right now for the query. */
export function estimateSoldVelocity(samples: EbayListingSample[]): number {
  return samples.length;
}
