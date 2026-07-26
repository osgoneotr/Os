// Publishing a real listing to eBay via the Sell API (Inventory API).
//
// FLAGGED — this needs more than the free developer account used for trend
// data:
//   1. A registered eBay seller account.
//   2. eBay "business policies" configured on that account (payment,
//      fulfillment/shipping, return policy) — Sell API listings reference
//      policy IDs, they can't be created from scratch via API.
//   3. A user-level OAuth token (authorization-code grant with seller
//      consent), not the client-credentials app token used for browsing —
//      creating listings acts on behalf of the seller's account.
//
// This function is a real implementation of the Inventory API calls, but you
// must complete the three steps above (all free, but manual) before it will
// work. Until then, expect it to throw — that's intentional, not a bug.
import { Listing } from '@/types';

const EBAY_API_BASE = 'https://api.ebay.com/sell/inventory/v1';

export interface EbayPublishResult {
  offerId: string;
  listingUrl: string;
}

export async function createEbayListing(
  listing: Listing,
  userAccessToken: string
): Promise<EbayPublishResult> {
  const sku = `listing-${listing.id}`;

  const inventoryRes = await fetch(`${EBAY_API_BASE}/inventory_item/${sku}`, {
    method: 'PUT',
    headers: {
      Authorization: `Bearer ${userAccessToken}`,
      'Content-Type': 'application/json',
      'Content-Language': 'en-US',
    },
    body: JSON.stringify({
      product: {
        title: listing.title,
        description: listing.description,
        imageUrls: listing.photos,
      },
      condition: mapCondition(listing.condition),
      availability: { shipToLocationAvailability: { quantity: 1 } },
    }),
  });
  if (!inventoryRes.ok) {
    throw new Error(`eBay inventory item create failed: ${inventoryRes.status} ${await inventoryRes.text()}`);
  }

  const offerRes = await fetch(`${EBAY_API_BASE}/offer`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${userAccessToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      sku,
      marketplaceId: 'EBAY_US',
      format: 'FIXED_PRICE',
      pricingSummary: { price: { value: listing.price, currency: 'USD' } },
      listingPolicies: {
        // These policy IDs must already exist on the seller's eBay account —
        // see the setup steps in the file header.
        fulfillmentPolicyId: process.env.EBAY_FULFILLMENT_POLICY_ID,
        paymentPolicyId: process.env.EBAY_PAYMENT_POLICY_ID,
        returnPolicyId: process.env.EBAY_RETURN_POLICY_ID,
      },
    }),
  });
  if (!offerRes.ok) {
    throw new Error(`eBay offer create failed: ${offerRes.status} ${await offerRes.text()}`);
  }
  const offer = await offerRes.json();

  const publishRes = await fetch(`${EBAY_API_BASE}/offer/${offer.offerId}/publish`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${userAccessToken}` },
  });
  if (!publishRes.ok) {
    throw new Error(`eBay offer publish failed: ${publishRes.status} ${await publishRes.text()}`);
  }
  const published = await publishRes.json();

  return {
    offerId: offer.offerId,
    listingUrl: `https://www.ebay.com/itm/${published.listingId}`,
  };
}

function mapCondition(condition: Listing['condition']): string {
  const map: Record<Listing['condition'], string> = {
    new_with_tags: 'NEW_WITH_TAGS',
    excellent: 'USED_EXCELLENT',
    good: 'USED_GOOD',
    fair: 'USED_ACCEPTABLE',
  };
  return map[condition];
}
