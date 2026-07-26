// POST /api/checkout   body: { listingId }
// Single-seller MVP: payment goes straight to your own Stripe account, so
// this uses plain Stripe Checkout — no Connect/escrow needed (that's only
// required for a multi-vendor marketplace splitting payouts between sellers).
import { NextResponse } from 'next/server';
import { createServerClient } from '@/lib/supabase/server';
import { stripe } from '@/lib/stripe';

export async function POST(request: Request) {
  const supabase = createServerClient();
  const { listingId } = await request.json();

  const { data: listing, error } = await supabase
    .from('listings')
    .select('id, title, price, status')
    .eq('id', listingId)
    .eq('status', 'active')
    .single();

  if (error || !listing) {
    return NextResponse.json({ error: 'Listing not found or not active' }, { status: 404 });
  }

  const session = await stripe.checkout.sessions.create({
    mode: 'payment',
    line_items: [
      {
        price_data: {
          currency: 'usd',
          product_data: { name: listing.title },
          unit_amount: Math.round(listing.price * 100),
        },
        quantity: 1,
      },
    ],
    metadata: { listingId: listing.id },
    success_url: `${process.env.NEXT_PUBLIC_SITE_URL}/listings/${listing.id}?purchase=success`,
    cancel_url: `${process.env.NEXT_PUBLIC_SITE_URL}/listings/${listing.id}?purchase=cancelled`,
  });

  return NextResponse.json({ url: session.url });
}
