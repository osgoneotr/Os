"""The ledger: one CSV that is the single source of truth for every item.

Deliberately a plain CSV rather than a database. You can open it in Excel or
Google Sheets, fix a typo, mark something sold by hand, and every script here
will still read it. That property matters more than query speed at this scale.

Re-running the sourcing script is always safe: rows are upserted by SKU, and
columns you've edited by hand (status, sold_price, notes...) are never
overwritten by a fresh sourcing pass.
"""

from __future__ import annotations

import csv
import datetime as dt
import shutil
from pathlib import Path
from typing import Iterable, Sequence

from . import config as cfg_mod
from .models import LEDGER_COLUMNS, STATUSES

# Fields the human owns. A re-source never clobbers these.
HUMAN_OWNED = {
    "status", "buy_price", "listed_at", "sold_at", "sold_price",
    "actual_postage", "actual_fees", "actual_profit", "notes",
}


def load(path: str | Path | None = None, cfg=None) -> list[dict[str, str]]:
    cfg = cfg or cfg_mod.load()
    path = Path(path) if path else cfg.path("ledger")
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    # Tolerate a ledger written by an older version with fewer columns.
    return [{col: (row.get(col) or "") for col in LEDGER_COLUMNS} for row in rows]


def save(rows: Sequence[dict], path: str | Path | None = None, cfg=None, *, backup: bool = True) -> Path:
    cfg = cfg or cfg_mod.load()
    path = Path(path) if path else cfg.path("ledger")
    path.parent.mkdir(parents=True, exist_ok=True)

    if backup and path.exists():
        # Cheap insurance: one rolling backup before every write.
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))

    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=LEDGER_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in LEDGER_COLUMNS})
    return path


def upsert(
    new_rows: Iterable[dict],
    path: str | Path | None = None,
    cfg=None,
) -> tuple[list[dict[str, str]], int, int]:
    """Merge rows in by SKU. Returns (all_rows, added, updated)."""
    existing = load(path, cfg)
    by_sku = {row["sku"]: row for row in existing if row.get("sku")}

    added = updated = 0
    for new in new_rows:
        sku = new.get("sku")
        if not sku:
            continue
        if sku in by_sku:
            current = by_sku[sku]
            for key, value in new.items():
                if key in HUMAN_OWNED:
                    continue
                if value not in ("", None):
                    current[key] = value
            updated += 1
        else:
            row = {col: "" for col in LEDGER_COLUMNS}
            row.update({k: v for k, v in new.items() if k in LEDGER_COLUMNS})
            row.setdefault("status", "candidate")
            by_sku[sku] = row
            added += 1

    merged = list(by_sku.values())
    save(merged, path, cfg)
    return merged, added, updated


def set_status(
    sku: str,
    status: str,
    *,
    path: str | Path | None = None,
    cfg=None,
    **fields: str,
) -> dict[str, str] | None:
    """Move one item along the pipeline, stamping the relevant date."""
    if status not in STATUSES:
        raise ValueError(f"Unknown status '{status}'. Valid: {', '.join(STATUSES)}")

    rows = load(path, cfg)
    today = dt.date.today().isoformat()
    for row in rows:
        if row.get("sku") != sku:
            continue
        row["status"] = status
        if status == "listed" and not row.get("listed_at"):
            row["listed_at"] = today
        if status == "sold" and not row.get("sold_at"):
            row["sold_at"] = today
        for key, value in fields.items():
            if key in LEDGER_COLUMNS:
                row[key] = str(value)
        save(rows, path, cfg)
        return row
    return None


def by_status(rows: Sequence[dict], *statuses: str) -> list[dict]:
    wanted = set(statuses)
    return [row for row in rows if row.get("status") in wanted]


def capital_committed(rows: Sequence[dict]) -> float:
    """Cash currently sitting in unsold stock."""
    total = 0.0
    for row in by_status(rows, "bought", "listed"):
        total += _money(row.get("buy_price"))
    return round(total, 2)


def capital_headroom(rows: Sequence[dict], cfg=None) -> float:
    cfg = cfg or cfg_mod.load()
    budget = float(cfg.get("business.working_capital_gbp", 200.0))
    return round(budget - capital_committed(rows), 2)


def _money(raw) -> float:
    try:
        return float(str(raw or "0").replace("£", "").replace(",", "") or 0)
    except ValueError:
        return 0.0
