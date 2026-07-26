// Depop and Vinted do not offer a public API for third parties to create
// listings on a seller's behalf — this is a platform limitation, not
// something more engineering would fix. The realistic MVP path is a
// "copy-ready" export: everything the seller needs to paste into the Depop
// or Vinted app in under a minute.
import { Listing } from '@/types';

export interface ExportPackage {
  title: string;
  description: string;
  price: number;
  photoUrls: string[];
  copyText: string;
}

export function buildExportPackage(listing: Listing): ExportPackage {
  const copyText = [
    listing.title,
    '',
    listing.description ?? '',
    '',
    `Price: $${listing.price}`,
  ].join('\n');

  return {
    title: listing.title,
    description: listing.description ?? '',
    price: listing.price,
    photoUrls: listing.photos,
    copyText,
  };
}
