// Reddit sourcing — free, but requires a free "script" app registered at
// reddit.com/prefs/apps to get a client id/secret (Reddit requires OAuth for
// sanctioned API access, not scraping the public JSON endpoints).

const REDDIT_OAUTH_URL = 'https://www.reddit.com/api/v1/access_token';
const REDDIT_SEARCH_URL = 'https://oauth.reddit.com/search';

let cachedToken: { token: string; expiresAt: number } | null = null;

async function getAppToken(): Promise<string> {
  if (cachedToken && cachedToken.expiresAt > Date.now()) {
    return cachedToken.token;
  }

  const clientId = process.env.REDDIT_CLIENT_ID;
  const clientSecret = process.env.REDDIT_CLIENT_SECRET;
  if (!clientId || !clientSecret) {
    throw new Error(
      'Missing REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET. Register a free "script" app at reddit.com/prefs/apps.'
    );
  }

  const basicAuth = Buffer.from(`${clientId}:${clientSecret}`).toString('base64');
  const res = await fetch(REDDIT_OAUTH_URL, {
    method: 'POST',
    headers: {
      Authorization: `Basic ${basicAuth}`,
      'Content-Type': 'application/x-www-form-urlencoded',
      'User-Agent': 'designer-resale-platform/0.1 (trend-discovery)',
    },
    body: new URLSearchParams({ grant_type: 'client_credentials' }),
  });

  if (!res.ok) {
    throw new Error(`Reddit OAuth failed: ${res.status} ${await res.text()}`);
  }

  const data = await res.json();
  cachedToken = { token: data.access_token, expiresAt: Date.now() + (data.expires_in - 60) * 1000 };
  return cachedToken.token;
}

export async function getMentionCount(keyword: string): Promise<number> {
  try {
    const token = await getAppToken();
    const params = new URLSearchParams({
      q: keyword,
      t: 'week',
      limit: '100',
      sort: 'new',
    });
    const res = await fetch(`${REDDIT_SEARCH_URL}?${params}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        'User-Agent': 'designer-resale-platform/0.1 (trend-discovery)',
      },
    });
    if (!res.ok) throw new Error(`Reddit search failed: ${res.status}`);
    const data = await res.json();
    return data?.data?.children?.length ?? 0;
  } catch (err) {
    console.warn(`Reddit mention lookup failed for "${keyword}":`, err);
    return 0; // fail soft
  }
}
