"""Unit tests for price_list_parsing — no DB required."""

from decimal import Decimal

import pandas as pd
import pytest

from saleor.product.price_list_parsing import (
    ParsedRow,
    parse_category,
    parse_decimal,
    parse_hs_code,
    parse_image_url,
    parse_optional_str,
    parse_price,
    parse_product_code,
    parse_required_str,
    parse_row,
    parse_sheet,
    parse_sizes,
    parse_weight_kg,
)

# ---------------------------------------------------------------------------
# parse_required_str
# ---------------------------------------------------------------------------


def test_parse_required_str_valid():
    val, errs = parse_required_str("Adidas", "brand")
    assert val == "Adidas"
    assert errs == []


def test_parse_required_str_strips_whitespace():
    val, errs = parse_required_str("  IS1637  ", "product_code")
    assert val == "IS1637"
    assert errs == []


def test_parse_required_str_empty_string():
    val, errs = parse_required_str("", "product_code")
    assert val == ""
    assert errs == ["product_code: required"]


def test_parse_required_str_none():
    val, errs = parse_required_str(None, "brand")
    assert val == ""
    assert errs == ["brand: required"]


def test_parse_required_str_whitespace_only():
    val, errs = parse_required_str("   ", "brand")
    assert val == ""
    assert errs == ["brand: required"]


# ---------------------------------------------------------------------------
# parse_product_code
# ---------------------------------------------------------------------------


def test_parse_product_code_valid():
    val, errs = parse_product_code("IS1637")
    assert val == "IS1637"
    assert errs == []


def test_parse_product_code_with_hyphens_ok():
    val, errs = parse_product_code("ABC-123")
    assert val == "ABC-123"
    assert errs == []


def test_parse_product_code_with_spaces_errors():
    val, errs = parse_product_code("IS 1637")
    assert "product_code: must not contain spaces" in errs


def test_parse_product_code_leading_trailing_spaces_stripped_then_checked():
    # After strip, "IS1637" has no spaces — valid
    val, errs = parse_product_code("  IS1637  ")
    assert val == "IS1637"
    assert errs == []


def test_parse_product_code_internal_space_after_strip_errors():
    val, errs = parse_product_code("  IS 1637  ")
    assert "product_code: must not contain spaces" in errs


def test_parse_product_code_none_is_required_error():
    _, errs = parse_product_code(None)
    assert "product_code: required" in errs


# ---------------------------------------------------------------------------
# parse_optional_str
# ---------------------------------------------------------------------------


def test_parse_optional_str_value():
    assert parse_optional_str("Apparel") == "Apparel"


def test_parse_optional_str_strips():
    assert parse_optional_str("  Apparel  ") == "Apparel"


def test_parse_optional_str_none():
    assert parse_optional_str(None) == ""


# ---------------------------------------------------------------------------
# parse_category
# ---------------------------------------------------------------------------

VALID_CATS = {"Apparel", "Footwear", "Accessories"}


def test_parse_category_in_valid_set():
    val, errs = parse_category("Apparel", VALID_CATS)
    assert val == "Apparel"
    assert errs == []


def test_parse_category_not_in_valid_set():
    _, errs = parse_category("UnknownCategory", VALID_CATS)
    assert len(errs) == 1
    assert "UnknownCategory" in errs[0]
    assert "not found" in errs[0]


def test_parse_category_empty_is_ok():
    val, errs = parse_category("", VALID_CATS)
    assert val == ""
    assert errs == []


def test_parse_category_none_is_ok():
    val, errs = parse_category(None, VALID_CATS)
    assert val == ""
    assert errs == []


def test_parse_category_skips_validation_when_valid_categories_is_none():
    val, errs = parse_category("AnythingAtAll", None)
    assert val == "AnythingAtAll"
    assert errs == []


# ---------------------------------------------------------------------------
# parse_decimal
# ---------------------------------------------------------------------------


def test_parse_decimal_integer():
    val, errs = parse_decimal(40, "rrp")
    assert val == Decimal(40)
    assert errs == []


def test_parse_decimal_float():
    val, errs = parse_decimal(9.025, "sell_price")
    assert val == Decimal("9.025")
    assert errs == []


def test_parse_decimal_string_number():
    val, errs = parse_decimal("110.50", "rrp")
    assert val == Decimal("110.50")
    assert errs == []


def test_parse_decimal_none_is_allowed():
    val, errs = parse_decimal(None, "buy_price")
    assert val is None
    assert errs == []


def test_parse_decimal_invalid_string():
    val, errs = parse_decimal("not-a-number", "rrp")
    assert val is None
    assert len(errs) == 1
    assert "rrp" in errs[0]


def test_parse_decimal_zero():
    val, errs = parse_decimal(0, "buy_price")
    assert val == Decimal(0)
    assert errs == []


# ---------------------------------------------------------------------------
# parse_price (non-negative decimal)
# ---------------------------------------------------------------------------


def test_parse_price_positive():
    val, errs = parse_price(9.99, "sell_price")
    assert val == Decimal("9.99")
    assert errs == []


def test_parse_price_zero_is_allowed():
    val, errs = parse_price(0, "sell_price")
    assert val == Decimal(0)
    assert errs == []


def test_parse_price_negative_errors():
    val, errs = parse_price(-1.0, "rrp")
    assert val == Decimal("-1.0")
    assert any("negative" in e for e in errs)


def test_parse_price_none_is_allowed():
    val, errs = parse_price(None, "buy_price")
    assert val is None
    assert errs == []


def test_parse_price_invalid_string():
    val, errs = parse_price("£9.99", "sell_price")
    assert val is None
    assert len(errs) == 1


# ---------------------------------------------------------------------------
# parse_weight_kg
# ---------------------------------------------------------------------------


def test_parse_weight_kg_valid():
    val, errs = parse_weight_kg(0.2)
    assert val == Decimal("0.2")
    assert errs == []


def test_parse_weight_kg_zero_is_allowed():
    val, errs = parse_weight_kg(0)
    assert val == Decimal(0)
    assert errs == []


def test_parse_weight_kg_none_is_allowed():
    val, errs = parse_weight_kg(None)
    assert val is None
    assert errs == []


def test_parse_weight_kg_negative_errors():
    _, errs = parse_weight_kg(-0.5)
    assert any("negative" in e for e in errs)


def test_parse_weight_kg_over_1000_errors():
    _, errs = parse_weight_kg(1001)
    assert any("1000" in e for e in errs)


def test_parse_weight_kg_exactly_1000_errors():
    _, errs = parse_weight_kg(1000.1)
    assert any("1000" in e for e in errs)


def test_parse_weight_kg_exactly_1000_is_boundary():
    # 1000 exactly is suspicious but we only error on > 1000
    val, errs = parse_weight_kg(1000)
    assert val == Decimal(1000)
    assert errs == []


def test_parse_weight_kg_likely_grams_errors():
    # Someone entered 1500 meaning 1500g = 1.5 kg; threshold is > 1000
    _, errs = parse_weight_kg(1500)
    assert any("1000" in e for e in errs)


# ---------------------------------------------------------------------------
# parse_image_url
# ---------------------------------------------------------------------------


def test_parse_image_url_valid_https_jpg():
    val, errs = parse_image_url("https://example.com/image.jpg")
    assert val == "https://example.com/image.jpg"
    assert errs == []


def test_parse_image_url_valid_http_png():
    val, errs = parse_image_url("http://cdn.example.com/path/to/img.png")
    assert errs == []


def test_parse_image_url_valid_with_query_params():
    val, errs = parse_image_url("https://cdn.example.com/img.webp?w=800&h=600")
    assert errs == []


@pytest.mark.parametrize(
    "ext", [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".avif"]
)
def test_parse_image_url_all_valid_extensions(ext):
    _, errs = parse_image_url(f"https://example.com/image{ext}")
    assert errs == []


def test_parse_image_url_invalid_extension():
    _, errs = parse_image_url("https://example.com/image.pdf")
    assert any(".pdf" in e for e in errs)


def test_parse_image_url_no_extension():
    _, errs = parse_image_url("https://example.com/image")
    assert any("(none)" in e for e in errs)


def test_parse_image_url_missing_scheme_errors():
    _, errs = parse_image_url("example.com/image.jpg")
    assert any("http" in e for e in errs)


def test_parse_image_url_ftp_scheme_errors():
    _, errs = parse_image_url("ftp://example.com/image.jpg")
    assert any("http" in e for e in errs)


def test_parse_image_url_empty_is_ok():
    val, errs = parse_image_url("")
    assert val == ""
    assert errs == []


def test_parse_image_url_none_is_ok():
    val, errs = parse_image_url(None)
    assert val == ""
    assert errs == []


# ---------------------------------------------------------------------------
# parse_hs_code
# ---------------------------------------------------------------------------


def test_parse_hs_code_8_digits_plain():
    val, errs = parse_hs_code("62046900")
    assert val == "62046900"
    assert errs == []


def test_parse_hs_code_6_digits():
    val, errs = parse_hs_code("620469")
    assert val == "620469"
    assert errs == []


def test_parse_hs_code_10_digits():
    val, errs = parse_hs_code("6204690000")
    assert val == "6204690000"
    assert errs == []


def test_parse_hs_code_with_spaces_normalised():
    val, errs = parse_hs_code("6204 69 00")
    assert val == "62046900"
    assert errs == []


def test_parse_hs_code_with_dots_normalised():
    val, errs = parse_hs_code("0601.10.00")
    assert val == "06011000"
    assert errs == []


def test_parse_hs_code_with_dashes_normalised():
    val, errs = parse_hs_code("6204-69-00")
    assert val == "62046900"
    assert errs == []


def test_parse_hs_code_leading_zeros_preserved():
    val, errs = parse_hs_code("0601.10.00")
    assert val == "06011000"
    assert val.startswith("0")


def test_parse_hs_code_7_digits_errors():
    _, errs = parse_hs_code("6204690")
    assert len(errs) == 1
    assert "6, 8, or 10" in errs[0]


def test_parse_hs_code_9_digits_errors():
    _, errs = parse_hs_code("620469000")
    assert len(errs) == 1


def test_parse_hs_code_none_is_ok():
    val, errs = parse_hs_code(None)
    assert val == ""
    assert errs == []


def test_parse_hs_code_empty_is_ok():
    val, errs = parse_hs_code("")
    assert val == ""
    assert errs == []


# ---------------------------------------------------------------------------
# parse_sizes
# ---------------------------------------------------------------------------


def test_parse_sizes_apparel_comma_separated():
    result, errs = parse_sizes("XS[20], M[50], L[50], XL[50], 3XL[20]")
    assert result == {"XS": 20, "M": 50, "L": 50, "XL": 50, "3XL": 20}
    assert errs == []


def test_parse_sizes_apparel_with_space_before_bracket():
    result, errs = parse_sizes("XS [20], M [50], L [50]")
    assert result == {"XS": 20, "M": 50, "L": 50}
    assert errs == []


def test_parse_sizes_numeric():
    result, errs = parse_sizes("32[58], 34[34], 36[23], 38[11], 40[12]")
    assert result == {"32": 58, "34": 34, "36": 23, "38": 11, "40": 12}
    assert errs == []


def test_parse_sizes_alpha_numeric_mixed():
    result, errs = parse_sizes("2[27], 4[34], 6[21], A38[8], A40[1]")
    assert result == {"2": 27, "4": 34, "6": 21, "A38": 8, "A40": 1}
    assert errs == []


def test_parse_sizes_space_separated_no_commas():
    result, errs = parse_sizes("5[3] XS[10]")
    assert result == {"5": 3, "XS": 10}
    assert errs == []


def test_parse_sizes_mixed_alpha_numeric_with_spaces_before_bracket():
    result, errs = parse_sizes("5 [3], XS[0], 10 [20]")
    assert result == {"5": 3, "XS": 0, "10": 20}
    assert errs == []


def test_parse_sizes_letter_in_quantity_is_invalid():
    # qty must be digits only — "10 [S]" has no valid matches
    result, errs = parse_sizes("10 [S]")
    assert result == {}
    assert len(errs) == 1
    assert "sizes:" in errs[0]


def test_parse_sizes_zero_quantity_allowed():
    result, errs = parse_sizes("XS[0], M[5]")
    assert result == {"XS": 0, "M": 5}
    assert errs == []


def test_parse_sizes_none_is_required_error():
    result, errs = parse_sizes(None)
    assert result == {}
    assert errs == ["sizes: required"]


def test_parse_sizes_empty_string_is_required_error():
    result, errs = parse_sizes("")
    assert result == {}
    assert errs == ["sizes: required"]


def test_parse_sizes_invalid_format():
    result, errs = parse_sizes("INVALID SIZES NO BRACKETS")
    assert result == {}
    assert len(errs) == 1


# ---------------------------------------------------------------------------
# parse_row
# ---------------------------------------------------------------------------

VALID_RAW = {
    "product_code": "IS1637",
    "brand": "Adidas",
    "description": "TIRO24 C TRPNTW",
    "category": "Apparel",
    "sizes": "XS[20], M[50], L[50], XL[50], 3XL[20]",
    "rrp": 40.0,
    "sell_price": 9.025,
    "buy_price": None,
    "weight_kg": 0.2,
    "image_url": None,
    "hs_code": None,
}


def test_parse_row_valid():
    row = parse_row(0, VALID_RAW, "GBP", valid_categories={"Apparel"})
    assert row.product_code == "IS1637"
    assert row.brand == "Adidas"
    assert row.sizes_and_qty == {"XS": 20, "M": 50, "L": 50, "XL": 50, "3XL": 20}
    assert row.rrp == Decimal("40.0")
    assert row.sell_price == Decimal("9.025")
    assert row.buy_price is None
    assert row.weight_kg == Decimal("0.2")
    assert row.currency == "GBP"
    assert row.is_valid is True
    assert row.validation_errors == []


def test_parse_row_preserves_row_index():
    row = parse_row(42, VALID_RAW, "GBP")
    assert row.row_index == 42


def test_parse_row_collects_all_errors_not_fail_fast():
    raw = {
        "product_code": "BAD CODE",  # spaces
        "brand": None,  # required
        "sizes": "INVALID",  # unparseable
        "rrp": -5.0,  # negative
        "weight_kg": 5000,  # over 1000 kg
        "image_url": "ftp://x.com/img.jpg",  # bad scheme
        "hs_code": "1234567",  # 7 digits
    }
    row = parse_row(0, raw, "GBP")
    assert row.is_valid is False
    text = " ".join(row.validation_errors)
    assert "product_code" in text
    assert "brand" in text
    assert "sizes" in text
    assert "rrp" in text
    assert "weight_kg" in text
    assert "image_url" in text
    assert "hs_code" in text


def test_parse_row_missing_optional_fields_valid():
    raw = {
        "product_code": "HY4520",
        "brand": "Adidas",
        "sizes": "XS[16], S[26], M[92]",
    }
    row = parse_row(0, raw, "GBP")
    assert row.is_valid is True
    assert row.description == ""
    assert row.category == ""
    assert row.buy_price is None
    assert row.hs_code == ""


def test_parse_row_category_validated_against_set():
    raw = {**VALID_RAW, "category": "NotInDB"}
    row = parse_row(0, raw, "GBP", valid_categories={"Apparel", "Footwear"})
    assert row.is_valid is False
    assert any("NotInDB" in e for e in row.validation_errors)


# ---------------------------------------------------------------------------
# parse_sheet
# ---------------------------------------------------------------------------

HK_COLUMN_MAP = {
    0: "brand",
    1: "product_code",
    2: "description",
    3: "rrp",
    4: "sell_price",
    6: "category",
    12: "weight_kg",
    13: "sizes",
}

HK_HEADERS = [
    "Brand",
    "Article",
    "Description",
    "RRP (GBP)",
    "Sale Price (GBP)",
    "Quantity",
    "Category",
    "Gender",
    "Sizing (UK)",
    "Category.1",
    "Gender.1",
    "Rounded RRP (GBP)",
    "unit_weight_kg",
    "updated_sizing",
    "issues",
    "updated_description",
]

HK_ROWS = [
    [
        "Adidas",
        "IS1637",
        "TIRO24 C TRPNTW",
        40,
        9.025,
        190,
        "Apparel",
        "Women",
        "XS[20]",
        "Apparel",
        "Women",
        40,
        0.2,
        "XS[20], M[50], L[50], XL[50], 3XL[20]",
        None,
        "",
    ],
    [
        "Adidas",
        "HY4520",
        "aSMC TST LS HO",
        110,
        23.69,
        155,
        "Apparel",
        "Women",
        "XS[16]",
        "Apparel",
        "Women",
        110,
        0.2,
        "XS[16], S[26], M[92], L[21]",
        None,
        "",
    ],
    [
        "Adidas",
        "H59015",
        "BLOUSON",
        110,
        24.99,
        138,
        "Apparel",
        "Women",
        "32[58]",
        "Apparel",
        "Women",
        110,
        0.2,
        "32[58], 34[34], 36[23], 38[11], 40[12]",
        None,
        "",
    ],
]


@pytest.fixture
def hk_df():
    return pd.DataFrame(HK_ROWS, columns=HK_HEADERS)


def test_parse_sheet_returns_correct_row_count(hk_df):
    rows = parse_sheet(hk_df, HK_COLUMN_MAP, "GBP")
    assert len(rows) == len(HK_ROWS)


def test_parse_sheet_all_rows_are_parsed_rows(hk_df):
    rows = parse_sheet(hk_df, HK_COLUMN_MAP, "GBP")
    assert all(isinstance(r, ParsedRow) for r in rows)


def test_parse_sheet_sequential_indices(hk_df):
    rows = parse_sheet(hk_df, HK_COLUMN_MAP, "GBP")
    assert [r.row_index for r in rows] == list(range(len(HK_ROWS)))


def test_parse_sheet_maps_columns_correctly(hk_df):
    rows = parse_sheet(hk_df, HK_COLUMN_MAP, "GBP")
    first = rows[0]
    assert first.brand == "Adidas"
    assert first.product_code == "IS1637"
    assert first.rrp == Decimal(40)
    assert first.weight_kg == Decimal("0.2")
    assert first.category == "Apparel"


def test_parse_sheet_uses_col_13_for_sizes(hk_df):
    rows = parse_sheet(hk_df, HK_COLUMN_MAP, "GBP")
    assert rows[0].sizes_and_qty == {"XS": 20, "M": 50, "L": 50, "XL": 50, "3XL": 20}


def test_parse_sheet_numeric_sizes(hk_df):
    rows = parse_sheet(hk_df, HK_COLUMN_MAP, "GBP")
    h59 = next(r for r in rows if r.product_code == "H59015")
    assert h59.sizes_and_qty == {"32": 58, "34": 34, "36": 23, "38": 11, "40": 12}


def test_parse_sheet_propagates_currency(hk_df):
    rows = parse_sheet(hk_df, HK_COLUMN_MAP, "GBP")
    assert all(r.currency == "GBP" for r in rows)


def test_parse_sheet_nan_cells_become_none(hk_df):
    rows = parse_sheet(hk_df, HK_COLUMN_MAP, "GBP")
    assert all(r.buy_price is None for r in rows)
    assert all(r.image_url == "" for r in rows)


def test_parse_sheet_category_validated(hk_df):
    rows = parse_sheet(hk_df, HK_COLUMN_MAP, "GBP", valid_categories={"Apparel"})
    assert all(r.is_valid for r in rows)


def test_parse_sheet_invalid_category_marks_row_invalid(hk_df):
    rows = parse_sheet(hk_df, HK_COLUMN_MAP, "GBP", valid_categories={"Footwear"})
    assert all(not r.is_valid for r in rows)
    assert all(any("not found" in e for e in r.validation_errors) for r in rows)


def test_parse_sheet_invalid_row_marked_invalid():
    bad = [
        [
            "",
            "",
            "desc",
            "not-a-number",
            9.0,
            10,
            "Apparel",
            "Men",
            "BAD",
            "Apparel",
            "Men",
            50,
            0.2,
            "BAD SIZES",
            None,
            "",
        ]
    ]
    df = pd.DataFrame(bad, columns=HK_HEADERS)
    rows = parse_sheet(df, HK_COLUMN_MAP, "GBP")
    assert rows[0].is_valid is False
    text = " ".join(rows[0].validation_errors)
    assert "product_code" in text
    assert "brand" in text
    assert "sizes" in text


def test_parse_sheet_empty_dataframe():
    df = pd.DataFrame([], columns=HK_HEADERS)
    assert parse_sheet(df, HK_COLUMN_MAP, "GBP") == []


def test_parse_sheet_skips_unmapped_columns(hk_df):
    minimal = {0: "brand", 1: "product_code", 13: "sizes"}
    rows = parse_sheet(hk_df, minimal, "GBP")
    assert rows[0].brand == "Adidas"
    assert rows[0].rrp is None
    assert rows[0].description == ""
