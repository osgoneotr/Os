// POST /api/listings/:id/publish   body: { marketplace: 'ebay' | 'depop' | 'vinted' }
//
// 'ebay'          -> attempts a real API listing via lib/marketplaces/ebay.ts
// 'depop'/'vinted' -> returns a copy-ready export package (see
//                     lib/marketplaces/exportPackage.ts for why these two
//                     can't be automated)
import { NextResponse } from 'next/server';
import { createServerClient } from '@/lib/supabase/server';
import { createEbayListing } from '@/lib/marketplaces/ebay';
import { buildExportPackage } from '@/lib/marketplaces/exportPackage';
import { Listing } from '@/types';

export async function POST(request: Request, { params }: { params: { id: string } }) {
  const supabase = createServerClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: 'Not signed in' }, { status: 401 });

  const { marketplace } = await request.json();

  const { data: listingRow, error } = await supabase
    .from('listings')
    .select('*, listing_photos(url, position)')
    .eq('id', params.id)
    .eq('user_id', user.id)
    .single();

  if (error || !listingRow) return NextResponse.json({ error: 'Listing not found' }, { status: 404 });

  const listing: Listing = {
    ...listingRow,
    photos: (listingRow.listing_photos ?? [])
      .sort((a: any, b: any) => a.position - b.position)
      .map((p: any) => p.url),
  };

  if (marketplace === 'ebay') {
    const token = process.env.EBAY_USER_ACCESS_TOKEN;
    if (!token) {
      return NextResponse.json(
        {
          error:
            'eBay is not connected yet. Complete the eBay seller OAuth setup in the README (business policies + user OAuth token) and set EBAY_USER_ACCESS_TOKEN.',
        },
        { status: 400 }
      );
    }
    try {
      const result = await createEbayListing(listing, token);
      await supabase.from('external_listings').insert({
        listing_id: listing.id,
        marketplace: 'ebay',
        method: 'api',
        status: 'posted',
        external_url: result.listingUrl,
      });
      return NextResponse.json({ status: 'posted', url: result.listingUrl });
    } catch (err: any) {
      await supabase.from('external_listings').insert({
        listing_id: listing.id,
        marketplace: 'ebay',
        method: 'api',
        status: 'failed',
      });
      return NextResponse.json({ error: err.message }, { status: 502 });
    }
  }

  if (marketplace === 'depop' || marketplace === 'vinted') {
    const pkg = buildExportPackage(listing);
    await supabase.from('external_listings').insert({
      listing_id: listing.id,
      marketplace,
      method: 'manual_export',
      status: 'exported',
    });
    return NextResponse.json({ status: 'exported', package: pkg });
  }

  return NextResponse.json({ error: 'Unknown marketplace' }, { status: 400 });
}
