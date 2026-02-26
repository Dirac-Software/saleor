"""Parsing and validation logic for PriceList Excel sheets.

Each parser returns (value, errors) so all errors are collected per-row
rather than failing fast. parse_sheet converts the DataFrame into a list
of ParsedRow objects ready for bulk insertion.
"""

import math
import os
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import attrs
import pandas as pd

from .ingestion import SizeQtyUnparseable, parse_sizes_and_qty

VALID_IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".avif"}
)

VALID_IMAGE_MIME_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/tiff",
        "image/avif",
    }
)

# UK HS / commodity codes: 6 digits (HS), 8 digits (CN8 import), 10 digits (export CPC)
VALID_HS_CODE_LENGTHS = frozenset({6, 8, 10})


@attrs.frozen
class ParsedRow:
    row_index: int
    product_code: str
    brand: str
    description: str
    category: str
    sizes_and_qty: dict
    rrp: Decimal | None
    sell_price: Decimal | None
    buy_price: Decimal | None
    weight_kg: Decimal | None
    image_url: str
    hs_code: str
    currency: str
    is_valid: bool
    validation_errors: list[str]


# ---------------------------------------------------------------------------
# Field parsers — each returns (value, errors).
# None means the cell was empty (NaN normalised upstream by parse_sheet).
# ---------------------------------------------------------------------------


def parse_required_str(val, field_name: str) -> tuple[str, list[str]]:
    s = "" if val is None else str(val).strip()
    if not s:
        return "", [f"{field_name}: required"]
    return s, []


def parse_optional_str(val) -> str:
    return "" if val is None else str(val).strip()


def parse_product_code(val) -> tuple[str, list[str]]:
    s, errs = parse_required_str(val, "product_code")
    s = s.lower()
    if s and " " in s:
        errs.append("product_code: must not contain spaces")
    return s, errs


def parse_brand(val) -> tuple[str, list[str]]:
    s, errs = parse_required_str(val, "brand")
    return s.lower(), errs


def parse_category(val, valid_categories: set[str] | None) -> tuple[str, list[str]]:
    """Validate category against the DB set passed in from the task.

    valid_categories should be the intersection of Category names and ProductType
    names (both must exist). Pass None to skip DB validation (e.g. in unit tests).
    """
    s = parse_optional_str(val)
    if s and valid_categories is not None and s not in valid_categories:
        return s, [
            f"category: '{s}' not found — must exist as both a Category and ProductType"
        ]
    return s, []


def parse_decimal(val, field_name: str) -> tuple[Decimal | None, list[str]]:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None, []
    try:
        dec = Decimal(str(val))
    except InvalidOperation:
        return None, [f"{field_name}: cannot parse '{val}' as a number"]
    if not dec.is_finite():
        return None, [f"{field_name}: cannot parse '{val}' as a number"]
    return dec, []


def parse_price(val, field_name: str) -> tuple[Decimal | None, list[str]]:
    dec, errs = parse_decimal(val, field_name)
    if dec is not None and dec < 0:
        errs.append(f"{field_name}: must not be negative")
    return dec, errs


def parse_required_price(val, field_name: str) -> tuple[Decimal | None, list[str]]:
    dec, errs = parse_price(val, field_name)
    if dec is None and not errs:
        errs = [f"{field_name}: required"]
    return dec, errs


def parse_weight_kg(val) -> tuple[Decimal | None, list[str]]:
    dec, errs = parse_decimal(val, "weight_kg")
    if dec is not None:
        if dec < 0:
            errs.append("weight_kg: must not be negative")
        elif dec > 1000:
            errs.append(
                f"weight_kg: {dec} exceeds 1000 kg — value may have been entered in grams"
            )
    return dec, errs


def parse_image_url(val) -> tuple[str, list[str]]:
    url = parse_optional_str(val)
    if not url:
        return url, []
    if url.startswith("data:"):
        try:
            header = url.split(",", 1)[0]
            mime_type = header[5:].split(";")[0]
        except (IndexError, ValueError):
            return url, ["image_url: invalid data URI"]
        if mime_type not in VALID_IMAGE_MIME_TYPES:
            return url, [
                f"image_url: unsupported data URI type '{mime_type}' — "
                f"expected one of {', '.join(sorted(VALID_IMAGE_MIME_TYPES))}"
            ]
        return url, []
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url, ["image_url: must be an http or https URL"]
    if not parsed.netloc:
        return url, ["image_url: invalid URL — missing host"]
    # Strip query string before checking extension
    path_ext = os.path.splitext(parsed.path.lower())[1]
    if path_ext not in VALID_IMAGE_EXTENSIONS:
        label = path_ext if path_ext else "(none)"
        return url, [
            f"image_url: unsupported file type '{label}' — "
            f"expected one of {', '.join(sorted(VALID_IMAGE_EXTENSIONS))}"
        ]
    return url, []


def parse_hs_code(val) -> tuple[str, list[str]]:
    """Parse and normalise a UK HS / commodity code.

    Strips spaces, dots, dashes and other separators. Accepts 6 digits
    (international HS), 8 digits (UK CN8 import) or 10 digits (UK CPC export).
    Returns the normalised all-digit string.
    """
    s = parse_optional_str(val)
    if not s:
        return s, []
    # Normalise: strip all non-digit characters (spaces, dots, dashes)
    digits = re.sub(r"\D", "", s)
    if len(digits) not in VALID_HS_CODE_LENGTHS:
        return s, [
            f"hs_code: expected 6, 8, or 10 digits after removing separators "
            f"(got {len(digits)}) — received '{s}'"
        ]
    return digits, []


def parse_sizes(val) -> tuple[dict, list[str]]:
    s = "" if val is None else str(val).strip()
    if not s:
        return {}, ["sizes: required"]
    try:
        sizes, qtys = parse_sizes_and_qty(s)
        return dict(zip(sizes, qtys, strict=False)), []
    except SizeQtyUnparseable as e:
        return {}, [f"sizes: {e}"]


# ---------------------------------------------------------------------------
# Row and sheet parsers
# ---------------------------------------------------------------------------


def parse_row(
    row_index: int,
    raw: dict,
    currency: str,
    valid_categories: set[str] | None = None,
) -> ParsedRow:
    """Parse a single pre-normalised row dict into a ParsedRow.

    ``raw`` must use field-name keys with NaN already replaced with None
    (handled by parse_sheet).
    """
    errors: list[str] = []

    def collect(result):
        value, errs = result
        errors.extend(errs)
        return value

    product_code = collect(parse_product_code(raw.get("product_code")))
    brand = collect(parse_brand(raw.get("brand")))
    category = collect(parse_category(raw.get("category"), valid_categories))
    sizes_and_qty = collect(parse_sizes(raw.get("sizes")))
    rrp = collect(parse_price(raw.get("rrp"), "rrp"))
    sell_price = collect(parse_required_price(raw.get("sell_price"), "sell_price"))
    buy_price = collect(parse_price(raw.get("buy_price"), "buy_price"))
    weight_kg = collect(parse_weight_kg(raw.get("weight_kg")))
    image_url = collect(parse_image_url(raw.get("image_url")))
    hs_code = collect(parse_hs_code(raw.get("hs_code")))

    return ParsedRow(
        row_index=row_index,
        product_code=product_code,
        brand=brand,
        description=parse_optional_str(raw.get("description")),
        category=category,
        sizes_and_qty=sizes_and_qty,
        rrp=rrp,
        sell_price=sell_price,
        buy_price=buy_price,
        weight_kg=weight_kg,
        image_url=image_url,
        hs_code=hs_code,
        currency=currency,
        is_valid=len(errors) == 0,
        validation_errors=errors,
    )


def parse_sheet(
    df: "pd.DataFrame",
    column_map: dict[int, str],
    default_currency: str,
    valid_categories: set[str] | None = None,
    header_row: int = 0,
) -> list[ParsedRow]:
    """Parse a DataFrame into a list of ParsedRow.

    Uses to_dict('records') rather than iterrows() for performance.
    NaN values are normalised to None before parsing so individual parsers
    only need to handle None.

    row_index is the 1-based Excel row number of the data row, so error
    messages reference the row the user actually sees in their spreadsheet.
    """
    valid_cols = sorted(c for c in column_map if c < len(df.columns))
    sub = df.iloc[:, valid_cols].copy()
    sub.columns = pd.Index([column_map[c] for c in valid_cols])
    sub = sub.astype(object).where(pd.notna(sub), other=None)
    rows = sub.to_dict("records")
    # header_row is 0-based; data rows start one row below it.
    # +2 converts to 1-based Excel row number (1 for header, +1 for data offset).
    first_data_excel_row = header_row + 2
    return [
        parse_row(first_data_excel_row + idx, raw, default_currency, valid_categories)
        for idx, raw in enumerate(rows)
    ]
