// Stripe webhook — marks a listing 'sold' when checkout completes.
//
// FLAGGED (hybrid selling risk): this only knows about sales made through
// your own site. If the same item also sold on eBay or was hand-posted to
// Depop/Vinted, there's no automatic sync in this MVP — you must manually
// delist it from the other channel(s) to avoid selling it twice. A future
// version could poll eBay's Sell API for order status and auto-delist.
import { NextResponse } from 'next/server';
import { stripe } from '@/lib/stripe';
import { createAdminClient } from '@/lib/supabase/server';

export async function POST(request: Request) {
  const body = await request.text();
  const signature = request.headers.get('stripe-signature')!;

  let event;
  try {
    event = stripe.webhooks.constructEvent(body, signature, process.env.STRIPE_WEBHOOK_SECRET!);
  } catch (err: any) {
    return NextResponse.json({ error: `Webhook signature invalid: ${err.message}` }, { status: 400 });
  }

  if (event.type === 'checkout.session.completed') {
    const session = event.data.object as any;
    const listingId = session.metadata?.listingId;
    if (listingId) {
      const supabase = createAdminClient();
      await supabase.from('listings').update({ status: 'sold' }).eq('id', listingId);
    }
  }

  return NextResponse.json({ received: true });
}
