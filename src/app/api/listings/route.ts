// POST /api/listings — creates a draft listing owned by the logged-in user.
// Also creates a 'self_attested' auth_requests row (see README for why full
// third-party authentication isn't part of the MVP).
import { NextResponse } from 'next/server';
import { createServerClient } from '@/lib/supabase/server';

export async function POST(request: Request) {
  const supabase = createServerClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    return NextResponse.json({ error: 'Not signed in' }, { status: 401 });
  }

  const body = await request.json();
  const { brandId, categoryId, condition, title, description, price, photos, suggestedPriceLow, suggestedPriceHigh } = body;

  if (!title || !condition || !price) {
    return NextResponse.json({ error: 'title, condition, and price are required' }, { status: 400 });
  }

  const { data: listing, error } = await supabase
    .from('listings')
    .insert({
      user_id: user.id,
      brand_id: brandId || null,
      category_id: categoryId || null,
      title,
      description,
      condition,
      price,
      suggested_price_low: suggestedPriceLow ?? null,
      suggested_price_high: suggestedPriceHigh ?? null,
      status: 'draft',
    })
    .select()
    .single();

  if (error) return NextResponse.json({ error: error.message }, { status: 500 });

  if (photos?.length) {
    await supabase.from('listing_photos').insert(
      photos.map((url: string, position: number) => ({ listing_id: listing.id, url, position }))
    );
  }

  await supabase.from('auth_requests').insert({ listing_id: listing.id, status: 'self_attested' });

  return NextResponse.json({ listing });
}
