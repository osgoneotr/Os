import Link from 'next/link';

export default function HomePage() {
  return (
    <main className="max-w-2xl mx-auto p-6 text-center mt-20">
      <h1 className="text-3xl font-bold mb-4">Designer Resale Platform</h1>
      <p className="text-gray-500 mb-8">
        See what&apos;s trending, then list your own pieces in minutes.
      </p>
      <div className="flex gap-4 justify-center">
        <Link href="/trends" className="bg-indigo-600 text-white px-4 py-2 rounded">
          View trends
        </Link>
        <Link href="/listings/new" className="border px-4 py-2 rounded">
          List an item
        </Link>
      </div>
    </main>
  );
}
