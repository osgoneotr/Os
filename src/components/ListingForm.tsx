'use client';

import { useEffect, useState } from 'react';
import { createClient } from '@/lib/supabase/client';
import { Brand, Category, Condition, PriceSuggestion as PriceSuggestionType } from '@/types';
import { PhotoUploader } from './PhotoUploader';
import { PriceSuggestion } from './PriceSuggestion';

const conditions: { value: Condition; label: string }[] = [
  { value: 'new_with_tags', label: 'New With Tags' },
  { value: 'excellent', label: 'Excellent' },
  { value: 'good', label: 'Good' },
  { value: 'fair', label: 'Fair' },
];

export function ListingForm() {
  const supabase = createClient();
  const [brands, setBrands] = useState<Brand[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);

  const [brandId, setBrandId] = useState('');
  const [categoryId, setCategoryId] = useState('');
  const [condition, setCondition] = useState<Condition>('good');
  const [notes, setNotes] = useState('');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [price, setPrice] = useState<number | ''>('');
  const [photos, setPhotos] = useState<string[]>([]);
  const [priceSuggestion, setPriceSuggestion] = useState<PriceSuggestionType | null>(null);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedListingId, setSavedListingId] = useState<string | null>(null);

  useEffect(() => {
    supabase.from('brands').select('id, name, tier').then(({ data }) => setBrands(data ?? []));
    supabase.from('categories').select('id, name, parent_id').then(({ data }) => setCategories(data ?? []));
  }, [supabase]);

  async function handleGenerate() {
    const brand = brands.find((b) => b.id === brandId);
    const category = categories.find((c) => c.id === categoryId);
    if (!brand || !category) return;

    setGenerating(true);
    try {
      const res = await fetch('/api/ai/generate-listing', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          brandId: brand.id,
          brandName: brand.name,
          categoryId: category.id,
          categoryName: category.name,
          condition,
          notes,
        }),
      });
      const data = await res.json();
      setTitle(data.content.title);
      setDescription(data.content.description);
      setPriceSuggestion(data.price);
      if (data.price?.median) setPrice(data.price.median);
    } finally {
      setGenerating(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const res = await fetch('/api/listings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          brandId,
          categoryId,
          condition,
          title,
          description,
          price: Number(price),
          photos,
          suggestedPriceLow: priceSuggestion?.low,
          suggestedPriceHigh: priceSuggestion?.high,
        }),
      });
      const data = await res.json();
      if (res.ok) setSavedListingId(data.listing.id);
    } finally {
      setSaving(false);
    }
  }

  if (savedListingId) {
    return (
      <div className="rounded border border-green-200 bg-green-50 p-4">
        <p className="font-medium text-green-700">Listing saved.</p>
        <a href={`/listings/${savedListingId}`} className="text-sm underline text-green-700">
          Continue to publish/cross-post →
        </a>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4 max-w-xl">
      <div>
        <label className="block text-sm font-medium mb-1">Brand</label>
        <select className="border rounded px-3 py-2 w-full" value={brandId} onChange={(e) => setBrandId(e.target.value)} required>
          <option value="">Select brand</option>
          {brands.map((b) => (
            <option key={b.id} value={b.id}>{b.name}</option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-sm font-medium mb-1">Category</label>
        <select className="border rounded px-3 py-2 w-full" value={categoryId} onChange={(e) => setCategoryId(e.target.value)} required>
          <option value="">Select category</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-sm font-medium mb-1">Condition</label>
        <select className="border rounded px-3 py-2 w-full" value={condition} onChange={(e) => setCondition(e.target.value as Condition)}>
          {conditions.map((c) => (
            <option key={c.value} value={c.value}>{c.label}</option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-sm font-medium mb-1">Notes (size, flaws, etc.)</label>
        <textarea className="border rounded px-3 py-2 w-full" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </div>

      <PhotoUploader onChange={setPhotos} />

      <button
        type="button"
        onClick={handleGenerate}
        disabled={!brandId || !categoryId || generating}
        className="text-sm bg-indigo-600 text-white px-4 py-2 rounded disabled:opacity-50"
      >
        {generating ? 'Generating…' : 'Generate title, description & price'}
      </button>

      <div>
        <label className="block text-sm font-medium mb-1">Title</label>
        <input className="border rounded px-3 py-2 w-full" value={title} onChange={(e) => setTitle(e.target.value)} required />
      </div>

      <div>
        <label className="block text-sm font-medium mb-1">Description</label>
        <textarea className="border rounded px-3 py-2 w-full" rows={4} value={description} onChange={(e) => setDescription(e.target.value)} required />
      </div>

      <PriceSuggestion suggestion={priceSuggestion} onUse={(p) => setPrice(p)} />

      <div>
        <label className="block text-sm font-medium mb-1">Price (USD)</label>
        <input
          type="number"
          className="border rounded px-3 py-2 w-full"
          value={price}
          onChange={(e) => setPrice(e.target.value === '' ? '' : Number(e.target.value))}
          required
        />
      </div>

      <button type="submit" disabled={saving} className="bg-black text-white px-4 py-2 rounded disabled:opacity-50">
        {saving ? 'Saving…' : 'Save listing'}
      </button>
    </form>
  );
}
