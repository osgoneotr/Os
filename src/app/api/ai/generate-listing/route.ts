// POST /api/ai/generate-listing
// Body: { brandId, brandName, categoryId, categoryName, condition, notes? }
// Returns generated title/description plus a price suggestion in one call so
// the listing form can show everything at once.
import { NextResponse } from 'next/server';
import { generateListingContent } from '@/lib/ai/generateListingContent';
import { suggestPrice } from '@/lib/pricing';

export async function POST(request: Request) {
  const body = await request.json();
  const { brandId, brandName, categoryId, categoryName, condition, notes } = body;

  if (!brandName || !categoryName || !condition) {
    return NextResponse.json({ error: 'brandName, categoryName, and condition are required' }, { status: 400 });
  }

  const [content, price] = await Promise.all([
    generateListingContent({ brandName, categoryName, condition, notes }),
    brandId && categoryId ? suggestPrice(brandId, categoryId) : Promise.resolve(null),
  ]);

  return NextResponse.json({ content, price });
}
