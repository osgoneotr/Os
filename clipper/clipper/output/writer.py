"""Write clips and their review sidecars to disk."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import ClipResult

SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_length: int = 40) -> str:
    slug = SLUG_RE.sub("-", text.lower()).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rsplit("-", 1)[0]
    return slug or "clip"


def clip_basename(index: int, title: str) -> str:
    return f"{index:02d}-{slugify(title)}"


def write_clip_outputs(result: ClipResult, directory: Path) -> Path:
    """Write the JSON sidecar and a human-readable Markdown card next to the mp4."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = result.video_path.stem

    json_path = directory / f"{stem}.json"
    json_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")

    md_path = directory / f"{stem}.md"
    md_path.write_text(_markdown_card(result), encoding="utf-8")
    return md_path


def _markdown_card(result: ClipResult) -> str:
    meta = result.metadata
    cand = result.candidate
    features = "\n".join(
        f"| {name} | {value:.2f} |" for name, value in sorted(cand.features.items())
    )
    return f"""# {meta.title or f'Clip {result.index}'}

**Source range:** {_timecode(cand.start)} → {_timecode(cand.end)} ({cand.duration:.1f}s)
**Score:** {cand.score:.3f}  ·  **Copy generator:** {meta.generator}
**Video:** `{result.video_path.name}`

## Caption

```
{meta.caption}
```

## Hashtags

```
{' '.join(meta.hashtags)}
```

## Why this segment scored well

| feature | value |
|---|---|
{features}

## Transcript

{meta.transcript}
"""


def _timecode(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def write_index(results: list[ClipResult], directory: Path, source: Path) -> Path:
    """One review page listing every clip, best first."""
    directory.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Clips from `{source.name}`",
        "",
        f"{len(results)} clip(s), ranked by score. Review before posting — nothing is uploaded.",
        "",
        "| # | score | in → out | length | title |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        c = r.candidate
        lines.append(
            f"| [{r.index}]({r.video_path.name}) | {c.score:.3f} | "
            f"{_timecode(c.start)} → {_timecode(c.end)} | {c.duration:.0f}s | "
            f"{r.metadata.title} |"
        )
    lines.append("")
    path = directory / "index.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
