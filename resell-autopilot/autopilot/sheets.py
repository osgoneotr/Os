"""Optional Google Sheets sync.

Entirely optional. The whole toolkit works on local CSVs; this exists so the
ledger can live in a Sheet you can open on your phone in a charity shop.

Two ways in, in order of hassle:

1. `to_tsv()` — zero setup. Prints tab-separated text; select it, copy, and
   paste into a Sheet. Google splits it into columns automatically. For a
   200-row ledger this is genuinely fine and takes ten seconds.

2. `push()` — real API sync via `gspread` + a Google service account. Worth it
   once you're updating the ledger daily. Setup is in the README.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from . import config as cfg_mod
from .models import LEDGER_COLUMNS

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def to_tsv(rows: Sequence[dict], columns: Sequence[str] | None = None) -> str:
    """Tab-separated text for copy-paste into a Sheet."""
    columns = list(columns or LEDGER_COLUMNS)
    lines = ["\t".join(columns)]
    for row in rows:
        cells = []
        for col in columns:
            value = str(row.get(col, "") or "")
            # Tabs and newlines inside a cell would break the paste.
            cells.append(value.replace("\t", " ").replace("\n", " / "))
        lines.append("\t".join(cells))
    return "\n".join(lines)


def write_tsv(rows: Sequence[dict], path: str | Path, columns: Sequence[str] | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_tsv(rows, columns), encoding="utf-8")
    return path


def available() -> bool:
    try:
        import gspread  # noqa: F401
        from google.oauth2.service_account import Credentials  # noqa: F401
    except ImportError:
        return False
    return bool(cfg_mod.env("GOOGLE_SERVICE_ACCOUNT_JSON") and cfg_mod.env("GOOGLE_SHEET_ID"))


def push(
    rows: Sequence[dict],
    *,
    worksheet: str = "ledger",
    columns: Sequence[str] | None = None,
) -> str:
    """Replace a worksheet's contents with these rows. Returns a status string."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        return (
            "gspread is not installed — skipping Sheets sync. "
            "Run `pip install gspread google-auth`, or just use the .tsv file and paste it in."
        )

    key_path = cfg_mod.env("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = cfg_mod.env("GOOGLE_SHEET_ID")
    if not key_path or not sheet_id:
        return (
            "GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SHEET_ID not set in .env — skipping Sheets sync."
        )
    if not Path(key_path).exists():
        return f"Service-account key not found at {key_path} — skipping Sheets sync."

    columns = list(columns or LEDGER_COLUMNS)
    credentials = Credentials.from_service_account_file(key_path, scopes=SCOPES)
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(sheet_id)

    try:
        sheet = spreadsheet.worksheet(worksheet)
        sheet.clear()
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=worksheet, rows=max(len(rows) + 10, 100), cols=len(columns))

    values = [columns] + [[str(row.get(col, "") or "") for col in columns] for row in rows]
    sheet.update(values, "A1")
    return f"Pushed {len(rows)} row(s) to '{worksheet}' in sheet {sheet_id}."
