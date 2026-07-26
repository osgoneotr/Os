'use client';

import { useState } from 'react';
import { createClient } from '@/lib/supabase/client';

export function PhotoUploader({ onChange }: { onChange: (urls: string[]) => void }) {
  const [urls, setUrls] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const supabase = createClient();

  async function handleFiles(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);

    const uploaded: string[] = [];
    for (const file of Array.from(files)) {
      const path = `${crypto.randomUUID()}-${file.name}`;
      const { error } = await supabase.storage.from('listing-photos').upload(path, file);
      if (error) {
        console.error('Upload failed:', error.message);
        continue;
      }
      const { data } = supabase.storage.from('listing-photos').getPublicUrl(path);
      uploaded.push(data.publicUrl);
    }

    const next = [...urls, ...uploaded];
    setUrls(next);
    onChange(next);
    setUploading(false);
  }

  return (
    <div>
      <label className="block text-sm font-medium mb-1">Photos</label>
      <input
        type="file"
        accept="image/*"
        multiple
        disabled={uploading}
        onChange={(e) => handleFiles(e.target.files)}
        className="text-sm"
      />
      {uploading && <p className="text-xs text-gray-400 mt-1">Uploading…</p>}
      <div className="mt-2 flex gap-2 flex-wrap">
        {urls.map((url) => (
          // eslint-disable-next-line @next/next/no-img-element
          <img key={url} src={url} alt="listing photo" className="w-20 h-20 object-cover rounded border" />
        ))}
      </div>
    </div>
  );
}
