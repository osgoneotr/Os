import { ListingForm } from '@/components/ListingForm';

export default function NewListingPage() {
  return (
    <main className="max-w-2xl mx-auto p-6">
      <h1 className="text-2xl font-bold mb-1">List an item</h1>
      <p className="text-gray-500 mb-6">
        Add photos and basic details — we&apos;ll draft the title, description, and price for you.
      </p>
      <ListingForm />
    </main>
  );
}
