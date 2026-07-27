"""eBay Browse API client (free developer account).

Two things this gives us:

1. `search_candidates()` — cheap Buy-It-Now listings we might flip. Used by the
   sourcing engine when the "buy on eBay, sell on Vinted" arbitrage is live.
2. `price_samples()` — the price distribution for a search phrase, which feeds
   the pricing engine.

IMPORTANT LIMITATION, stated up front: the free Browse API returns ACTIVE
listings — asking prices, not sold prices. Asking prices skew high because the
overpriced listings are the ones that never sell and therefore stay visible.
The pricing module multiplies these by `pricing.active_to_sold_ratio` and marks
the result low-confidence. Real sold data needs eBay's Marketplace Insights API,
which requires a separate application to eBay (see README). Until you have it,
the sold-comps CSV path in `tabular.py` is the more accurate route.

Credentials: put EBAY_CLIENT_ID / EBAY_CLIENT_SECRET in resell-autopilot/.env.
"""

from __future__ import annotations

import base64
import time
from typing import Any, Iterable

from .. import config as cfg_mod
from ..http_client import HttpClient, HttpError
from ..models import Candidate

# eBay condition IDs -> our vocabulary.
_CONDITION_MAP = {
    "1000": "new_with_tags",
    "1500": "new_without_tags",
    "1750": "new_without_tags",
    "2000": "excellent",
    "2010": "excellent",
    "2020": "excellent",
    "2030": "good",
    "2500": "good",
    "3000": "good",
    "4000": "good",
    "5000": "fair",
    "6000": "fair",
    "7000": "for_parts",
}


class EbayAuthError(RuntimeError):
    pass


class EbayBrowseClient:
    def __init__(self, cfg=None, http: HttpClient | None = None):
        self.cfg = cfg or cfg_mod.load()
        self.http = http or HttpClient(self.cfg)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # -- auth -----------------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(cfg_mod.env("EBAY_CLIENT_ID") and cfg_mod.env("EBAY_CLIENT_SECRET"))

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        client_id = cfg_mod.env("EBAY_CLIENT_ID")
        client_secret = cfg_mod.env("EBAY_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise EbayAuthError(
                "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET are not set. Either add them to "
                "resell-autopilot/.env (free account at developer.ebay.com) or run with "
                "--source csv to use the no-API path."
            )

        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        payload = self.http.post_json(
            self.cfg.get("ebay_api.oauth_url"),
            data={
                "grant_type": "client_credentials",
                "scope": self.cfg.get("ebay_api.scope"),
            },
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        if not payload or "access_token" not in payload:
            raise EbayAuthError(f"eBay did not return an access token. Response: {payload}")

        self._token = payload["access_token"]
        self._token_expires_at = time.time() + float(payload.get("expires_in", 7200))
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "X-EBAY-C-MARKETPLACE-ID": self.cfg.get("ebay_api.marketplace_id", "EBAY_GB"),
            "Content-Type": "application/json",
        }

    # -- search ---------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        category_ids: str | None = None,
        max_price: float | None = None,
        min_price: float | None = None,
        conditions: Iterable[str] | None = None,
        buy_it_now_only: bool = True,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Raw itemSummaries for a query, paged up to ebay_api.max_pages."""
        page_size = int(limit or self.cfg.get("ebay_api.page_size", 100))
        max_pages = int(self.cfg.get("ebay_api.max_pages", 3))
        url = self.cfg.get("ebay_api.browse_url")

        filters: list[str] = []
        if buy_it_now_only:
            filters.append("buyingOptions:{FIXED_PRICE}")
        if min_price is not None or max_price is not None:
            lo = f"{min_price:.2f}" if min_price is not None else ""
            hi = f"{max_price:.2f}" if max_price is not None else ""
            filters.append(f"price:[{lo}..{hi}]")
            filters.append("priceCurrency:GBP")
        if conditions:
            filters.append("conditionIds:{%s}" % "|".join(str(c) for c in conditions))

        summaries: list[dict[str, Any]] = []
        for page in range(max_pages):
            params: dict[str, Any] = {
                "q": query,
                "limit": page_size,
                "offset": page * page_size,
            }
            if category_ids:
                params["category_ids"] = category_ids
            if filters:
                params["filter"] = ",".join(filters)

            try:
                payload = self.http.get_json(url, params=params, headers=self._headers())
            except HttpError as exc:
                if exc.status == 429:
                    # Free tier is 5,000 calls/day. Stop the run, keep what we have.
                    print(f"[ebay] rate limited on '{query}' — stopping paging early.")
                    break
                raise

            items = (payload or {}).get("itemSummaries") or []
            summaries.extend(items)
            if len(items) < page_size:
                break

        return summaries

    def search_candidates(
        self, query: str, *, category: str = "unknown", **kwargs: Any
    ) -> list[Candidate]:
        """Search results converted to Candidates, malformed rows skipped."""
        candidates: list[Candidate] = []
        skipped = 0
        for item in self.search(query, **kwargs):
            candidate = _to_candidate(item, category)
            if candidate is None:
                skipped += 1
                continue
            candidates.append(candidate)
        if skipped:
            print(f"[ebay] skipped {skipped} malformed listing(s) for '{query}'")
        return candidates

    def price_samples(self, query: str, **kwargs: Any) -> tuple[list[float], list[str]]:
        """(prices, sample_urls) for a query — the input to comps building."""
        prices: list[float] = []
        urls: list[str] = []
        for item in self.search(query, **kwargs):
            price = _extract_price(item)
            if price is None:
                continue
            prices.append(price)
            if len(urls) < 5 and item.get("itemWebUrl"):
                urls.append(item["itemWebUrl"])
        return prices, urls

    def sold_price_samples(self, query: str, **kwargs: Any) -> tuple[list[float], list[str]]:
        """Real sold prices via Marketplace Insights.

        Only works if eBay has approved your app for the Marketplace Insights
        API and you set ebay_api.marketplace_insights_enabled: true. Otherwise
        this raises, and callers fall back to active listings.
        """
        if not self.cfg.get("ebay_api.marketplace_insights_enabled"):
            raise EbayAuthError(
                "Marketplace Insights (real sold prices) is not enabled. It needs a separate "
                "approval from eBay — see README. Falling back to active listings."
            )
        url = self.cfg.get("ebay_api.marketplace_insights_url")
        params = {"q": query, "limit": self.cfg.get("ebay_api.page_size", 100)}
        payload = self.http.get_json(url, params=params, headers=self._headers())
        prices, urls = [], []
        for item in (payload or {}).get("itemSales", []):
            price = _extract_price(item, key="lastSoldPrice")
            if price is not None:
                prices.append(price)
                if len(urls) < 5 and item.get("itemWebUrl"):
                    urls.append(item["itemWebUrl"])
        return prices, urls


# -- helpers -------------------------------------------------------------


def _extract_price(item: dict[str, Any], key: str = "price") -> float | None:
    node = item.get(key) or {}
    raw = node.get("value")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    # Mixed-currency results would poison the maths; drop anything non-GBP.
    if node.get("currency") not in (None, "GBP"):
        return None
    return round(value, 2)


def _to_candidate(item: dict[str, Any], category: str) -> Candidate | None:
    title = (item.get("title") or "").strip()
    price = _extract_price(item)
    if not title or price is None:
        return None

    location = ""
    loc = item.get("itemLocation") or {}
    if loc:
        location = ", ".join(str(v) for v in (loc.get("city"), loc.get("postalCode")) if v)

    photos = []
    if (item.get("image") or {}).get("imageUrl"):
        photos.append(item["image"]["imageUrl"])
    for extra in item.get("additionalImages") or []:
        if extra.get("imageUrl"):
            photos.append(extra["imageUrl"])

    condition_id = str(item.get("conditionId") or "")
    return Candidate(
        title=title,
        ask_price=price,
        source="ebay_browse",
        source_id=str(item.get("itemId") or ""),
        url=item.get("itemWebUrl") or "",
        category=category,
        condition=_CONDITION_MAP.get(condition_id, "unknown"),
        condition_note=item.get("condition") or "",
        location=location,
        seller=(item.get("seller") or {}).get("username") or "",
        photos=photos[:12],
        # Everything here came from eBay's structured fields, not our eyes.
        evidence={"title": "ebay_api", "condition": "ebay_api_seller_declared"},
    )
