"""Canonical rows -> the CSV each marketplace actually wants.

One honest caveat, stated once here and repeated in the generated README:
marketplace bulk-upload templates change, and eBay/Facebook both validate
uploads against the exact template you download from *your* account on the day.
These exporters produce the correct data in the conventional column names, which
is 95% of the work — but the first time you use one, download the platform's own
template and check the headers line up. `--template` writes a header-only file
you can diff against theirs.

Vinted has no bulk upload at all (no public listing API, no CSV import). Its
exporter produces a copy-paste worksheet: one row per item with the fields in
the order the Vinted app asks for them, so listing takes ~40 seconds instead of
five minutes. That's the honest ceiling on Vinted automation today.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Sequence

from .models import LEDGER_COLUMNS

# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

VINTED_COLUMNS = [
    "sku", "photos_folder", "title", "description", "category_path", "brand",
    "size", "condition", "colour", "material", "price_gbp", "min_price_gbp",
    "parcel_size", "keywords", "status",
]

# eBay File Exchange "Add" action. The Action cell carries the site/currency
# preamble on the first data row, which is how File Exchange expects it.
EBAY_COLUMNS = [
    "Action(SiteID=UK|Country=GB|Currency=GBP|Version=1193)",
    "CustomLabel", "Category", "Title", "Subtitle", "Description", "ConditionID",
    "PicURL", "Quantity", "Format", "StartPrice", "BestOfferEnabled",
    "BestOfferAutoAcceptPrice", "MinimumBestOfferPrice", "Duration", "Location",
    "ShippingType", "ShippingService-1:Option", "ShippingService-1:Cost",
    "DispatchTimeMax", "ReturnsAcceptedOption", "C:Brand", "C:Size", "C:Colour",
    "C:Material",
]

# Facebook commerce catalogue format (also used for Marketplace bulk listing).
FACEBOOK_COLUMNS = [
    "id", "title", "description", "availability", "condition", "price", "link",
    "image_link", "brand", "google_product_category", "quantity_to_sell_on_facebook",
    "sale_price", "item_group_id", "colour", "size", "material",
]

# eBay condition IDs, mapped from our vocabulary.
_EBAY_CONDITION_ID = {
    "new_with_tags": "1000",
    "new_without_tags": "1500",
    "excellent": "3000",
    "good": "3000",
    "fair": "3000",
    "for_parts": "7000",
    "unknown": "3000",
}

_FB_CONDITION = {
    "new_with_tags": "new",
    "new_without_tags": "new",
    "excellent": "used_like_new",
    "good": "used_good",
    "fair": "used_fair",
    "for_parts": "used_fair",
    "unknown": "used_good",
}

_VINTED_CONDITION = {
    "new_with_tags": "New with tags",
    "new_without_tags": "New without tags",
    "excellent": "Very good",
    "good": "Good",
    "fair": "Satisfactory",
    "for_parts": "Satisfactory",
    "unknown": "Good",
}

_PARCEL_SIZE = {
    "large_letter": "Small",
    "small_parcel_1kg": "Small",
    "small_parcel_2kg": "Medium",
    "medium_parcel_5kg": "Medium",
    "large_parcel_10kg": "Large",
}


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def write_csv(path: str | Path, columns: Sequence[str], rows: Iterable[dict]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in columns})
    return path


def write_template(path: str | Path, channel: str) -> Path:
    """Header-only CSV, for diffing against the platform's own template."""
    columns = {"vinted": VINTED_COLUMNS, "ebay_uk": EBAY_COLUMNS, "facebook": FACEBOOK_COLUMNS}[channel]
    return write_csv(path, columns, [])


def export_canonical(rows: Iterable[dict], path: str | Path) -> Path:
    """The full internal schema — this is what the ledger and Sheets use."""
    return write_csv(path, LEDGER_COLUMNS, rows)


def export_vinted(rows: Iterable[dict], path: str | Path) -> Path:
    out = []
    for row in rows:
        out.append(
            {
                "sku": row.get("sku", ""),
                "photos_folder": row.get("image_refs", ""),
                # Vinted's audience responds to the style title, not the SEO one.
                "title": row.get("title_style") or row.get("title_seo", ""),
                "description": row.get("description", ""),
                "category_path": _vinted_category_path(row),
                "brand": row.get("brand", ""),
                "size": row.get("size", ""),
                "condition": _VINTED_CONDITION.get(row.get("condition", ""), "Good"),
                "colour": row.get("colour", ""),
                "material": row.get("material", ""),
                "price_gbp": row.get("list_price", ""),
                "min_price_gbp": row.get("min_price", ""),
                "parcel_size": _PARCEL_SIZE.get(row.get("postage_band", ""), "Medium"),
                "keywords": row.get("keywords", ""),
                "status": row.get("status", ""),
            }
        )
    return write_csv(path, VINTED_COLUMNS, out)


def export_ebay(rows: Iterable[dict], path: str | Path) -> Path:
    out = []
    for index, row in enumerate(rows):
        list_price = row.get("list_price", "")
        out.append(
            {
                # File Exchange wants the full preamble on the first row only.
                "Action(SiteID=UK|Country=GB|Currency=GBP|Version=1193)": "Add",
                "CustomLabel": row.get("sku", ""),
                "Category": "",  # you fill this once per category — see README
                "Title": row.get("title_seo") or row.get("title_style", ""),
                "Subtitle": "",
                "Description": _html_description(row),
                "ConditionID": _EBAY_CONDITION_ID.get(row.get("condition", ""), "3000"),
                "PicURL": _pipe_to_bar(row.get("image_refs", "")),
                "Quantity": "1",
                "Format": "FixedPrice",
                "StartPrice": list_price,
                "BestOfferEnabled": "1",
                "BestOfferAutoAcceptPrice": row.get("min_price", ""),
                "MinimumBestOfferPrice": row.get("min_price", ""),
                "Duration": "GTC",
                "Location": row.get("location", "") or "Slough, Berkshire",
                "ShippingType": "Flat",
                "ShippingService-1:Option": "UK_OtherCourier3Days",
                # Free postage: the cost model in fees.py assumes you absorb it.
                "ShippingService-1:Cost": "0.00",
                "DispatchTimeMax": "1",
                "ReturnsAcceptedOption": "ReturnsAccepted",
                "C:Brand": row.get("brand", ""),
                "C:Size": row.get("size", ""),
                "C:Colour": row.get("colour", ""),
                "C:Material": row.get("material", ""),
            }
        )
        if index > 0:
            out[-1]["Action(SiteID=UK|Country=GB|Currency=GBP|Version=1193)"] = "Add"
    return write_csv(path, EBAY_COLUMNS, out)


def export_facebook(rows: Iterable[dict], path: str | Path) -> Path:
    out = []
    for row in rows:
        images = _split_refs(row.get("image_refs", ""))
        out.append(
            {
                "id": row.get("sku", ""),
                "title": row.get("title_style") or row.get("title_seo", ""),
                "description": row.get("description", ""),
                "availability": "in stock",
                "condition": _FB_CONDITION.get(row.get("condition", ""), "used_good"),
                "price": f"{row.get('list_price', '')} GBP",
                "link": row.get("source_url", ""),
                "image_link": images[0] if images else "",
                "brand": row.get("brand", ""),
                "google_product_category": _google_category(row.get("category", "")),
                "quantity_to_sell_on_facebook": "1",
                "sale_price": "",
                "item_group_id": "",
                "colour": row.get("colour", ""),
                "size": row.get("size", ""),
                "material": row.get("material", ""),
            }
        )
    return write_csv(path, FACEBOOK_COLUMNS, out)


EXPORTERS = {
    "vinted": export_vinted,
    "ebay_uk": export_ebay,
    "facebook": export_facebook,
    "canonical": export_canonical,
}


def export_all(rows: Sequence[dict], out_dir: str | Path, stem: str = "listings") -> dict[str, Path]:
    """Write the canonical CSV plus one file per channel present in the rows."""
    out_dir = Path(out_dir)
    written = {"canonical": export_canonical(rows, out_dir / f"{stem}_canonical.csv")}

    channels = {row.get("channel", "") for row in rows} - {""}
    for channel in sorted(channels):
        exporter = EXPORTERS.get(channel)
        if not exporter:
            continue
        subset = [r for r in rows if r.get("channel") == channel]
        written[channel] = exporter(subset, out_dir / f"{stem}_{channel}.csv")

    _write_export_readme(out_dir)
    return written


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _split_refs(raw: str) -> list[str]:
    return [part.strip() for part in str(raw or "").split("|") if part.strip()]


def _pipe_to_bar(raw: str) -> str:
    """eBay File Exchange separates multiple picture URLs with a pipe."""
    return "|".join(_split_refs(raw))


def _html_description(row: dict) -> str:
    """eBay renders HTML; convert our bullet lines into a simple list."""
    text = str(row.get("description", ""))
    if not text:
        return ""
    parts: list[str] = []
    bullets: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("•"):
            bullets.append(f"<li>{_escape(line.lstrip('• ').strip())}</li>")
            continue
        if bullets:
            parts.append("<ul>" + "".join(bullets) + "</ul>")
            bullets = []
        if line:
            parts.append(f"<p>{_escape(line)}</p>")
    if bullets:
        parts.append("<ul>" + "".join(bullets) + "</ul>")
    return "".join(parts)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _vinted_category_path(row: dict) -> str:
    return {
        "trainers": "Men > Shoes > Trainers",
        "clothing": "Men > Clothing",
        "retro_tech": "Entertainment > Electronics",
        "audio": "Entertainment > Electronics",
    }.get(row.get("category", ""), "")


def _google_category(category: str) -> str:
    return {
        "trainers": "Apparel & Accessories > Shoes",
        "clothing": "Apparel & Accessories > Clothing",
        "retro_tech": "Electronics",
        "audio": "Electronics > Audio",
    }.get(category, "")


_EXPORT_README = """\
Generated by resell-autopilot. Read this once, then you can ignore it.

listings_canonical.csv
    Every field the system knows about. This is the file to paste into Google
    Sheets and the file the reports script reads. Nothing is lost here.

listings_vinted.csv
    Vinted has NO bulk upload and NO public listing API. This is a worksheet:
    the columns are in the order the Vinted app asks for them, so you work down
    a row while the app is open. ~40 seconds per item.

listings_ebay_uk.csv
    eBay File Exchange "Add" format. Before your first upload:
      1. Seller Hub > Reports > Upload > download the current UK template.
      2. Compare its header row to this file's header row.
      3. Fill the `Category` column with the eBay category ID for each item
         (find it via eBay's category lookup; it's stable per category, so you
         set it once and reuse it).
    File Exchange needs a business-seller-eligible account on some sites; if
    the upload page isn't available to you, the same data can be typed into
    Seller Hub's bulk listing tool.

listings_facebook.csv
    Facebook commerce catalogue format. Commerce Manager > Catalogue > Data
    sources > Add items > Upload. Facebook Marketplace listings created this
    way are shipping listings; local-pickup listings must be created in the app.

Every price in these files already has fees, postage and packaging subtracted
in the profit columns of the canonical file. The list_price column is what you
should actually list at; min_price is your accept-an-offer floor.
"""


def _write_export_readme(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "README_EXPORTS.txt").write_text(_EXPORT_README, encoding="utf-8")
