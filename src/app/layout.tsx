import './globals.css';
import Link from 'next/link';

export const metadata = {
  title: 'Designer Resale Platform',
  description: 'Trend discovery + resell listing tool',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav className="border-b px-6 py-3 flex gap-6 text-sm font-medium">
          <Link href="/trends">Trends</Link>
          <Link href="/listings/new">List an item</Link>
        </nav>
        {children}
      </body>
    </html>
  );
}
