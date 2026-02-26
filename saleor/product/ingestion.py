"""Product ingestion utilities."""

import logging
import re
from decimal import Decimal
from typing import TYPE_CHECKING

import attrs
import pandas as pd
from django.utils.text import slugify

from saleor.attribute.models import Attribute, AttributeValue
from saleor.attribute.models.product import AssignedProductAttributeValue
from saleor.attribute.models.product_variant import (
    AssignedVariantAttribute,
    AssignedVariantAttributeValue,
    AttributeVariant,
)
from saleor.warehouse.models import Stock, Warehouse

if TYPE_CHECKING:
    from saleor.channel.models import Channel
    from saleor.product.models import (
        Category,
        Product,
        ProductChannelListing,
        ProductMedia,
        ProductType,
        ProductVariant,
        ProductVariantChannelListing,
    )

logger = logging.getLogger(__name__)


# ============================================================================
# Custom Exceptions for Interactive Decision Points
# ============================================================================
# These exceptions allow the ingestion logic to remain pure while enabling
# interactive prompts in the command wrapper. When a decision is needed:
# 1. Utils raise specific exception with context
# 2. Command catches exception, prompts user
# 3. Command updates config with decision
# 4. Command retries with updated config


class SheetIntegrityError(Exception):
    """Something in this sheet is not as it should be.

    Subclasses show exactly what is wrong

    """


class SizeQtyUnparseable(SheetIntegrityError):
    """In a row with a Product Code, the SizeQty was not in size[qty] format."""


class CurrencyIncompatible(SheetIntegrityError):
    """The RRP and sell price are DIFFERENT currencies."""


class DuplicateProducts(SheetIntegrityError):
    """There exists multiple rows with (brand,product_code) matching."""


class MissingDatabaseSetup(SheetIntegrityError):
    """Required database entities (ProductTypes, Categories, Attributes) are missing."""


class DuplicateProductNames(SheetIntegrityError):
    """Multiple products in sheet have identical names, preventing slug generation."""


class MissingRequiredFields(SheetIntegrityError):
    """One or more rows have missing required fields."""


class OwnedWarehouseIngestionError(SheetIntegrityError):
    """Attempting to ingest products to an owned warehouse."""


class InteractiveDecisionRequired(Exception):
    """Base exception for decisions that can be made interactively or via config.

    Subclasses contain context needed for the command to prompt the user.

    Attributes:
        decision_type: Type of decision required (e.g., "stock_update_mode", "minimum_order_quantity")

    """

    def __init__(self, message: str, decision_type: str):
        super().__init__(message)
        self.decision_type = decision_type


class StockUpdateModeRequired(InteractiveDecisionRequired):
    """Stock update mode must be decided: REPLACE or ADD quantities.

    Raised when:
    - Products already exist in target warehouse
    - config.stock_update_mode is None

    Resolution:
    - Interactive: Command prompts user for choice
    - Programmatic: Set config.stock_update_mode = "replace" or "add"

    Attributes:
        products_in_warehouse: List of (ProductData, Product) tuples that exist
            in the target warehouse and need a decision
        warehouse_name: Name of warehouse for context

    """

    def __init__(
        self,
        products_in_warehouse: list[tuple["ProductData", "Product"]],
        warehouse_name: str,
    ):
        self.products_in_warehouse = products_in_warehouse
        self.warehouse_name = warehouse_name
        message = (
            f"Stock update mode required: {len(products_in_warehouse)} product(s) "
            f"already exist in warehouse '{warehouse_name}'. "
            "Set config.stock_update_mode to 'replace' (overwrite) or 'add' (increment)."
        )
        super().__init__(message, decision_type="stock_update_mode")


class MinimumOrderQuantityRequired(InteractiveDecisionRequired):
    """Minimum order quantity must be provided.

    Raised when:
    - Products are being ingested
    - config.minimum_order_quantity is None

    Resolution:
    - Interactive: Command prompts user for MOQ value
    - Programmatic: Set config.minimum_order_quantity = positive_integer

    Attributes:
        product_count: Number of products that need MOQ assigned

    """

    def __init__(self, product_count: int):
        self.product_count = product_count
        message = (
            f"Minimum order quantity required for {product_count} product(s). "
            "Set config.minimum_order_quantity to a positive integer."
        )
        super().__init__(message, decision_type="minimum_order_quantity")


class PriceInterpretationConfirmationRequired(InteractiveDecisionRequired):
    """Price interpretation must be confirmed.

    Raised when:
    - Products have pricing data
    - config.not_for_web is False (products are for sale)
    - config.confirm_price_interpretation is False

    Resolution:
    - Interactive: Command shows warning and gets confirmation
    - Programmatic: Set config.confirm_price_interpretation = True

    This ensures user understands that:
    1. Price column = sale price (what customers pay)
    2. Price does NOT include VAT
    """

    def __init__(self):
        message = (
            "Price interpretation confirmation required. "
            "Set config.confirm_price_interpretation = True after confirming that "
            "'Price' column contains sale price (excluding VAT)."
        )
        super().__init__(message, decision_type="confirm_price_interpretation")


class ColumnMappingRequired(InteractiveDecisionRequired):
    """Column mapping must be provided.

    Raised when:
    - config.column_mapping is None
    - User needs to map Excel columns to expected fields

    Resolution:
    - Interactive: Command prompts user to map columns
    - Programmatic: Set config.column_mapping to SpreadsheetColumnMapping instance

    Attributes:
        available_columns: List of column names found in Excel sheet

    """

    def __init__(self, available_columns: list[str]):
        self.available_columns = available_columns
        message = (
            f"Column mapping required. Found {len(available_columns)} columns in Excel: "
            f"{', '.join(str(c) for c in available_columns)}. "
            "Please provide column mapping."
        )
        super().__init__(message, decision_type="column_mapping")


@attrs.frozen
class SpreadsheetColumnMapping:
    """Maps Excel column names to internal field names.

    Set to None to skip optional columns.
    """

    code: str | None = "Code"
    brand: str | None = "Brand"
    description: str | None = "Description"
    category: str | None = "Category"
    sizes: str | None = "Sizes"  # Contains sizes with quantities: "8[5], 9[3]"
    rrp: str | None = "RRP"
    price: str | None = "Price"
    weight: str | None = "Weight"
    image: str | None = "Image"


@attrs.frozen
class IngestConfig:
    """Configuration for product ingestion.

    Pre-Conditions:
    1. Sheet has all of the SpreadsheetColumnMapping filled in.
    2. The sizes and quantities are in the "size[quantity]" format
    3. RRP, sell price and buy price are confirmed.
    4. The sheet MAY contain an image_url

    This contains all settings needed to ingest products.

    error_on_duplicates_in_sheet: Duplicates are judged by (brand,product_code). If we don't error behaviour is to ADD the list(size,quantity)
    if the duplicate rows do not have identical list(size,quantity). If they have
    identical values the duplicate is likely a mistake and so we keep only one of the
    rows.

    default_currency: If set, constraints on RRP, sell and buy price are relaxed so to
    use the fallback. If there is None set then ALL columns with a price must have a
    currency symbol. There is no ability to have the buy,sell and RRP in a different
    currency.

    not_for_web: If True, products are marked as unavailable on all channels (not published,
    not visible in listings). Prices are still required. Use this for wholesale-only products,
    trade show items, or products that exist in inventory but shouldn't be sold online.

    Interactive fields can be set only if we come to them and are nullable. They will
    fail raise a InteractiveDecisionRequired error if they are null, an interactive
    context can catch and handle the error.
    - column_mapping: Maps Excel column names to expected fields (None triggers prompt)
    - stock_update_mode: How to handle existing stock in this warehouse
    - minimum_order_quantity: MOQ value to assign to all products
    - confirm_price_interpretation: Whether user confirmed price meaning
    """

    warehouse_name: str
    warehouse_address: str
    warehouse_country: str  # ISO2 country code (e.g., "AE", "GB", "US")
    sheet_name: str = "Sheet1"
    header_row: int = 0  # Row containing column names (0-indexed)
    column_mapping: SpreadsheetColumnMapping | None = (
        None  # None triggers interactive prompt
    )
    not_for_web: bool = False
    default_currency: str | None = None
    dry_run: bool = False
    error_on_duplicates_in_sheet: bool = False
    # Interactive decision fields - None triggers prompt in command
    stock_update_mode: str | None = None  # "replace" | "add" | None
    minimum_order_quantity: int | None = None
    confirm_price_interpretation: bool = False


@attrs.frozen
class ProductData:
    """Data class representing a product from Excel.

    Currency Rules:
    - RRP and Price must be in GBP (currency field)
    - Buy Price is NOT recorded (we don't track cost prices)
    """

    product_code: str
    description: str
    category: str
    sizes: tuple[str, ...]
    qty: tuple[int, ...]
    brand: str
    rrp: Decimal | None
    price: Decimal | None
    currency: str  # Currency for RRP and Price (must be GBP)
    weight_kg: Decimal | None  # Weight in kilograms
    image_url: str | None


@attrs.frozen
class IngestionResult:
    """Result of product ingestion operation."""

    created_products: list["Product"]
    updated_products: list["Product"]
    total_products_processed: int
    total_variants_created: int
    total_variants_updated: int
    warehouse: Warehouse
    skipped_products: int = 0


def parse_sizes_and_qty(
    sizes_str: str, product_code: str | None = None
) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """Parse sizes and quantities from strings.

    Uses regex to find all size[qty] patterns, regardless of separator.
    Supports: "6.5[1], 7[1]" or "S[5] M[10] L[3]" or "5 [2], 4 [10]" (with spaces).

    Args:
        sizes_str: String containing sizes and quantities
        product_code: Product code for error messages (optional)

    Returns:
        Tuple of (sizes, quantities)

    Raises:
        SizeQtyUnparseable: If format is incorrect

    """
    if not sizes_str:
        return (), ()

    # Find all patterns of: anything[digits], with optional space before bracket.
    # Matches "8[5]", "S[10]", "6.5[3]", "XS [20]", "10 [3]", etc.
    pattern = r"([^\[\],\s]+)\s*\[(\d+)\]"
    matches = re.findall(pattern, sizes_str)

    if not matches:
        error_msg = (
            f"No valid size[qty] patterns found in: '{sizes_str}'"
            + (f" in product {product_code}" if product_code else "")
            + ". Expected format: 'size[quantity]' (e.g., '8[5], 9[3]' or 'S[5] M[10]' or '5 [2], 4 [10]')"
        )
        raise SizeQtyUnparseable(error_msg)

    sizes = []
    quantities = []

    for size, qty_str in matches:
        sizes.append(size.strip())
        quantities.append(int(qty_str))

    return tuple(sizes), tuple(quantities)


def read_excel_with_validation(
    file_path: str, sheet_name: str = "Sheet1", header_row: int = 0
) -> pd.DataFrame:
    """Read Excel file and validate sheet exists.

    Args:
        file_path: Path to Excel file
        sheet_name: Name of sheet to read (default: Sheet1)
        header_row: Row number to use as column names (0-indexed, default: 0)

    Returns:
        DataFrame with data

    Raises:
        FileNotFoundError: If file not found or sheet doesn't exist
        ValueError: If excel cannot be read by pandas

    """
    excel_file_obj = pd.ExcelFile(file_path)
    available_sheets = excel_file_obj.sheet_names

    # Use first sheet if the specified one doesn't exist
    actual_sheet_name = sheet_name
    if sheet_name not in available_sheets:
        raise FileNotFoundError(f"{sheet_name} is not in the given Excel file")

    # Read data using pandas with specified header row
    df = pd.read_excel(file_path, sheet_name=actual_sheet_name, header=header_row)

    return df


def get_required_attributes() -> dict[str, Attribute]:
    """Fetch commonly used attributes for product operations.

    Returns:
        Dictionary mapping attribute names to Attribute instances
        Keys: "Product Code", "Brand", "Size"

    Raises:
        CommandError: If any required attribute is missing

    """
    required_attrs = ["Product Code", "Brand", "Size"]

    attributes = Attribute.objects.filter(name__in=required_attrs)
    attribute_map = {attr.name: attr for attr in attributes}

    missing = set(required_attrs) - set(attribute_map.keys())
    if missing:
        raise MissingDatabaseSetup(
            f"Required attributes not found in database: {', '.join(sorted(missing))}. "
            f"Please create these attributes before ingesting products."
        )

    return attribute_map


def get_size_to_variant_map(product: "Product") -> dict[str, "ProductVariant"]:
    """Build a mapping of size -> variant for a product.

    Args:
        product: Product instance to get variants for

    Returns:
        Dictionary mapping size names to ProductVariant instances

    Raises:
        CommandError: If Size attribute not found

    """
    from saleor.product.models import ProductVariant

    size_attr = Attribute.objects.filter(name="Size").first()
    if not size_attr:
        raise MissingDatabaseSetup(
            "Size attribute not found in database. "
            "Please create a 'Size' attribute before ingesting products."
        )

    existing_variants = list(ProductVariant.objects.filter(product=product))
    if not existing_variants:
        return {}

    variant_ids = [v.pk for v in existing_variants]
    size_map: dict[int, str] = {
        sa.variant_id: sa.value.name
        for sa in AssignedVariantAttributeValue.objects.filter(
            variant_id__in=variant_ids, value__attribute=size_attr
        ).select_related("value")
        if sa.variant_id is not None
    }

    return {size_map[v.pk]: v for v in existing_variants if v.pk in size_map}


def get_products_by_code_and_brand(
    product_codes: list[str],
) -> dict[tuple[str, str], "Product"]:
    """Get products from database by Product Code and Brand.

    Args:
        product_codes: List of product codes to search for

    Returns:
        Dictionary mapping (product_code, brand_name) tuples to Product instances

    Raises:
        CommandError: If Product Code or Brand attributes not found

    """
    from saleor.attribute.models.product import AssignedProductAttributeValue
    from saleor.product.models import Product

    # Get Product Code and Brand attributes
    product_code_attr = Attribute.objects.filter(name="Product Code").first()
    brand_attr = Attribute.objects.filter(name="Brand").first()

    if not product_code_attr:
        raise MissingDatabaseSetup(
            "Product Code attribute not found in database. "
            "Please create a 'Product Code' attribute before ingesting products."
        )
    if not brand_attr:
        raise MissingDatabaseSetup(
            "Brand attribute not found in database. "
            "Please create a 'Brand' attribute before ingesting products."
        )

    matching_codes = AttributeValue.objects.filter(
        attribute=product_code_attr,
        name__iregex=r"^(" + "|".join(map(re.escape, product_codes)) + r")$",
    ).prefetch_related("productvalueassignment__product")

    # Collect all (product, code_name) pairs via the prefetch
    product_code_pairs: list[tuple[Product, str]] = [
        (assignment.product, code_value.name)
        for code_value in matching_codes
        for assignment in code_value.productvalueassignment.all()
    ]

    if not product_code_pairs:
        return {}

    # Batch-fetch all brand assignments in one query
    product_ids = [product.pk for product, _ in product_code_pairs]
    brand_map: dict[int, str] = {
        ba.product_id: ba.value.name
        for ba in AssignedProductAttributeValue.objects.filter(
            product_id__in=product_ids, value__attribute=brand_attr
        ).select_related("value")
    }

    return {
        (code_name.lower(), brand_map[product.pk].lower()): product
        for product, code_name in product_code_pairs
        if product.pk in brand_map
    }


def validate_warehouse_for_ingestion(warehouse: Warehouse) -> None:
    """Validate that warehouse can accept product ingestion.

    Product ingestion is only allowed for non-owned warehouses (is_owned=False).
    Owned warehouses (is_owned=True) have inventory tracked at the unit level
    and must use purchase orders for stock management.

    Args:
        warehouse: Warehouse to validate

    Raises:
        OwnedWarehouseIngestionError: If warehouse is owned (is_owned=True)

    """
    if warehouse.is_owned:
        raise OwnedWarehouseIngestionError(
            f"Cannot ingest products to owned warehouse '{warehouse.name}'. "
            f"Owned warehouses track inventory at the unit level and must use "
            f"purchase orders for stock management. Only non-owned warehouses "
            f"(is_owned=False) can accept product ingestion via this command."
        )
    logger.info(
        "Warehouse '%s' validated for ingestion (is_owned=False)", warehouse.name
    )


def create_warehouse_with_address(
    warehouse_name: str, address_string: str, country_code: str
) -> Warehouse:
    """Create a new warehouse with address.

    Args:
        warehouse_name: Name for the warehouse
        address_string: Full address as string (will be stored in street_address_1)
        country_code: ISO2 country code (e.g., "AE", "GB", "US")

    Returns:
        Created Warehouse instance

    Raises:
        ValueError: If warehouse with same slug already exists

    """
    from saleor.account.models import Address

    warehouse_slug = slugify(warehouse_name)

    # Check if warehouse already exists
    if Warehouse.objects.filter(slug=warehouse_slug).exists():
        raise ValueError(
            f"Warehouse with slug '{warehouse_slug}' already exists. "
            f"Please choose a different name."
        )

    # Create address
    address = Address.objects.create(
        street_address_1=address_string,
        city="",  # Not parsed from string
        country=country_code,
    )

    # Create warehouse (default is_owned=False for new warehouses)
    warehouse = Warehouse.objects.create(
        name=warehouse_name,
        slug=warehouse_slug,
        address=address,
        is_owned=False,
    )

    logger.info("Created warehouse: %s (slug: %s)", warehouse_name, warehouse_slug)
    return warehouse


def assign_warehouse_to_all_shipping_zones(warehouse: Warehouse) -> int:
    """Assign warehouse to all shipping zones.

    Args:
        warehouse: Warehouse to assign

    Returns:
        Number of shipping zones the warehouse was added to

    """
    from saleor.shipping.models import ShippingZone

    zones = ShippingZone.objects.all()
    count = 0

    for zone in zones:
        zone.warehouses.add(warehouse)
        count += 1
        logger.info(
            "Added warehouse '%s' to shipping zone '%s'", warehouse.name, zone.name
        )

    return count


def assign_warehouse_to_all_channels(warehouse: Warehouse) -> int:
    """Assign warehouse to all channels.

    Args:
        warehouse: Warehouse to assign

    Returns:
        Number of channels the warehouse was added to

    """
    from saleor.channel.models import Channel

    channels = Channel.objects.all()
    count = 0

    for channel in channels:
        warehouse.channels.add(channel)
        count += 1
        logger.info(
            "Added warehouse '%s' to channel '%s'", warehouse.name, channel.name
        )

    return count


def _validate_column_mapping(
    df: pd.DataFrame, column_mapping: SpreadsheetColumnMapping
) -> None:
    """Validate that all non-None columns in mapping exist in DataFrame.

    Args:
        df: DataFrame to validate
        column_mapping: Column name mapping to check

    Raises:
        SheetIntegrityError: If required columns are missing

    """
    available_columns = set(df.columns)
    missing_columns = []

    # Check each non-None column in the mapping
    for field_name, column_name in [
        ("code", column_mapping.code),
        ("brand", column_mapping.brand),
        ("description", column_mapping.description),
        ("category", column_mapping.category),
        ("sizes", column_mapping.sizes),
        ("rrp", column_mapping.rrp),
        ("price", column_mapping.price),
        ("weight", column_mapping.weight),
        ("image", column_mapping.image),
    ]:
        if column_name is not None and column_name not in available_columns:
            missing_columns.append(f"{field_name}='{column_name}'")

    if missing_columns:
        # Convert all column names to strings for display (pandas can use ints for unnamed cols)
        available_cols_str = ", ".join(str(col) for col in sorted(available_columns))
        raise SheetIntegrityError(
            f"Missing required columns in Excel sheet. "
            f"Expected columns not found: {', '.join(missing_columns)}. "
            f"Available columns: {available_cols_str}"
        )


def process_excel_row(
    row: pd.Series, column_mapping: SpreadsheetColumnMapping, config: IngestConfig
) -> ProductData | None:
    """Process a single Excel row into ProductData.

    Args:
        row: Pandas Series representing one row
        column_mapping: Column name mapping
        config: Ingest configuration

    Returns:
        ProductData instance or None if row should be skipped

    Raises:
        SizeQtyUnparseable: If size[qty] notation is malformed
        CurrencyIncompatible: If RRP and sell price have different currencies

    """
    # Extract values using column mapping
    code = (
        str(row[column_mapping.code]).strip()
        if column_mapping.code in row and pd.notna(row[column_mapping.code])
        else None
    )
    description = (
        str(row[column_mapping.description]).strip()
        if column_mapping.description in row
        and pd.notna(row[column_mapping.description])
        else ""
    )
    category = (
        str(row[column_mapping.category]).strip()
        if column_mapping.category in row and pd.notna(row[column_mapping.category])
        else ""
    )
    sizes_str = (
        str(row[column_mapping.sizes]).strip()
        if column_mapping.sizes in row and pd.notna(row[column_mapping.sizes])
        else ""
    )
    brand = (
        str(row[column_mapping.brand]).strip().title()
        if column_mapping.brand in row and pd.notna(row[column_mapping.brand])
        else ""
    )

    # Extract image URL if present
    image_url = None
    if (
        column_mapping.image
        and column_mapping.image in row
        and pd.notna(row[column_mapping.image])
    ):
        image_url = str(row[column_mapping.image]).strip()

    # Extract weight if present (in kilograms)
    weight_kg = None
    if (
        column_mapping.weight
        and column_mapping.weight in row
        and pd.notna(row[column_mapping.weight])
    ):
        weight_str = str(row[column_mapping.weight]).strip()
        try:
            # Remove any non-numeric characters except decimal point
            weight_kg = float("".join(c for c in weight_str if c.isdigit() or c == "."))
        except (ValueError, TypeError):
            logger.warning("Product %s: Invalid weight value '%s'", code, weight_str)
            weight_kg = None

    # Extract numeric values from price fields (remove currency symbols)
    rrp_str = (
        str(row[column_mapping.rrp])
        if column_mapping.rrp
        and column_mapping.rrp in row
        and pd.notna(row[column_mapping.rrp])
        else ""
    )
    price_str = (
        str(row[column_mapping.price])
        if column_mapping.price
        and column_mapping.price in row
        and pd.notna(row[column_mapping.price])
        else ""
    )

    # Remove currency symbols and parse as float, or None if no price available
    rrp = (
        float("".join(c for c in rrp_str if c.isdigit() or c == "."))
        if any(c.isdigit() for c in rrp_str)
        else None
    )
    price = (
        float("".join(c for c in price_str if c.isdigit() or c == "."))
        if any(c.isdigit() for c in price_str)
        else None
    )

    # Validate required fields - raise exception with ALL missing fields at once
    missing_fields = []

    if not code:
        missing_fields.append("Product Code")
    if not description:
        missing_fields.append("Description")
    if not category:
        missing_fields.append("Category")
    if not brand:
        missing_fields.append("Brand")
    if not sizes_str:
        missing_fields.append("Sizes")
    if not price:
        missing_fields.append("Price")

    # If any required fields are missing, raise exception
    if missing_fields:
        row_identifier = f"Product {code}" if code else "Row"
        raise MissingRequiredFields(
            f"{row_identifier}: Missing required field(s): {', '.join(missing_fields)}"
        )

    # Parse sizes and quantities (raises SizeQtyUnparseable if malformed)
    sizes, quantities = parse_sizes_and_qty(sizes_str, product_code=code)

    if not sizes:
        raise MissingRequiredFields(
            f"Product {code}: No valid sizes found in Sizes column"
        )

    # Detect currency (RRP/Price must be GBP)
    currency = _detect_currency_from_row(row, column_mapping, config)
    logger.info("Product %s: Currency=%s", code, currency)

    # Create ProductData instance
    if code is None or description is None or category is None or brand is None:
        raise ValueError(
            f"Required field is None after validation — "
            f"code={code!r}, description={description!r}, "
            f"category={category!r}, brand={brand!r}"
        )
    product_data = ProductData(
        product_code=code,
        description=description,
        category=category,
        sizes=sizes,
        qty=quantities,
        brand=brand,
        rrp=Decimal(str(rrp)) if rrp is not None else None,
        price=Decimal(str(price)) if price is not None else None,
        currency=currency,
        weight_kg=Decimal(str(weight_kg)) if weight_kg is not None else None,
        image_url=image_url,
    )

    return product_data


def _detect_currency_from_row(
    row: pd.Series, column_mapping: SpreadsheetColumnMapping, config: IngestConfig
) -> str:
    """Detect currency from price fields.

    Currency Rules:
    - RRP and Price MUST be in GBP (£)
    - Buy Price is NOT tracked/recorded

    Args:
        row: DataFrame row with price data
        column_mapping: Column name mapping
        config: Ingest configuration

    Returns:
        Currency code (always "GBP")

    Raises:
        CurrencyIncompatible: If RRP or Price are not in GBP

    """

    # Extract currency from each price field
    def extract_currency(price_str: str) -> str | None:
        """Extract currency symbol from price string."""
        if "£" in price_str:
            return "GBP"
        if "$" in price_str:
            return "USD"
        if "€" in price_str:
            return "EUR"
        return None

    code = str(row[column_mapping.code]) if column_mapping.code in row else "Unknown"

    # Check RRP and Price columns - must be GBP
    rrp_str = (
        str(row[column_mapping.rrp])
        if column_mapping.rrp
        and column_mapping.rrp in row
        and pd.notna(row[column_mapping.rrp])
        else ""
    )
    price_str = (
        str(row[column_mapping.price])
        if column_mapping.price
        and column_mapping.price in row
        and pd.notna(row[column_mapping.price])
        else ""
    )

    rrp_currency = extract_currency(rrp_str) if rrp_str else None
    price_currency = extract_currency(price_str) if price_str else None

    # Validate RRP is GBP (if provided)
    if rrp_currency and rrp_currency != "GBP":
        raise CurrencyIncompatible(
            f"Product {code}: RRP must be in GBP (£). Found: {rrp_currency}"
        )

    # Validate Price is GBP (if provided)
    if price_currency and price_currency != "GBP":
        raise CurrencyIncompatible(
            f"Product {code}: Price must be in GBP (£). Found: {price_currency}"
        )

    # All prices are in GBP
    return "GBP"


def deduplicate_products(
    products: list[ProductData], config: IngestConfig
) -> list[ProductData]:
    """De-duplicate products by (brand, product_code).

    Also validates that product names (descriptions) are unique to avoid slug conflicts.

    Behavior depends on config.error_on_duplicates_in_sheet:
    - If True: Raise DuplicateProducts exception
    - If False: Handle duplicates:
        - If (sizes, qty) tuples are identical: keep one, delete duplicates
        - If different: merge sizes/qty, keep highest price and its RRP

    Args:
        products: List of ProductData instances
        config: Ingest configuration

    Returns:
        De-duplicated list of ProductData

    Raises:
        DuplicateProducts: If duplicates found and config.error_on_duplicates_in_sheet is True
        DuplicateProductNames: If duplicate product names (descriptions) found after deduplication

    """
    from collections import defaultdict

    # First, group by (brand, product_code) tuple
    key_to_products: dict[tuple[str, str], list[ProductData]] = defaultdict(list)
    for product in products:
        key = (product.brand, product.product_code)
        key_to_products[key].append(product)

    # Find duplicates
    duplicates = {
        key: prods for key, prods in key_to_products.items() if len(prods) > 1
    }

    if duplicates and config.error_on_duplicates_in_sheet:
        # Error mode - raise exception
        duplicate_keys = [f"{brand}/{code}" for brand, code in duplicates.keys()]
        raise DuplicateProducts(
            f"Found {len(duplicates)} duplicate (brand, product_code) combinations: "
            f"{', '.join(duplicate_keys)}. Set error_on_duplicates_in_sheet=False "
            f"to merge duplicates automatically."
        )

    # Merge mode - deduplicate
    deduplicated = []

    for (brand, code), product_list in key_to_products.items():
        if len(product_list) == 1:
            # No duplicates
            deduplicated.append(product_list[0])
            continue

        # Found duplicates
        logger.info("Found %d duplicates for %s/%s", len(product_list), brand, code)

        # Check if all have identical (sizes, qty) tuples
        first_tuple = (product_list[0].sizes, product_list[0].qty)
        all_identical = all((p.sizes, p.qty) == first_tuple for p in product_list)

        if all_identical:
            # All identical - keep first, delete rest
            deduplicated.append(product_list[0])
            logger.info(
                "  All identical, keeping 1, deleting %d", len(product_list) - 1
            )
        else:
            # Different - merge them
            merged_product = _merge_products(product_list)
            deduplicated.append(merged_product)
            logger.info(
                "  Merging %d products with different sizes/quantities",
                len(product_list),
            )

    # After deduplication, check for duplicate product names (would cause slug conflicts)
    slug_to_products: dict[str, list[ProductData]] = defaultdict(list)
    for product in deduplicated:
        slug = slugify(product.description)
        slug_to_products[slug].append(product)

    duplicates_in_names = {
        slug: prods for slug, prods in slug_to_products.items() if len(prods) > 1
    }

    if duplicates_in_names:
        error_msg = (
            f"Found {len(duplicates_in_names)} duplicate product names in Excel:\n"
        )
        for slug, prods in duplicates_in_names.items():
            error_msg += f"\n  Slug '{slug}' would be created from:\n"
            for prod in prods:
                error_msg += f"    - Code: {prod.product_code}, Brand: {prod.brand}, Name: {prod.description}\n"
        error_msg += "\nThese are DIFFERENT products (different codes or brands) but have the same name.\n"
        error_msg += (
            "This would cause slug conflicts. Please ensure product names are unique."
        )
        raise DuplicateProductNames(error_msg)

    return deduplicated


def _merge_products(product_list: list[ProductData]) -> ProductData:
    """Merge multiple products with same code but different sizes/quantities.

    Keep highest price and corresponding RRP.
    Combine all sizes and quantities, deduplicating by size and summing quantities.
    """
    # Find product with highest price
    highest_price_product = max(product_list, key=lambda p: p.price or 0)

    # Combine all sizes and quantities, deduplicating by size
    size_to_qty: dict[str, int] = {}
    for product in product_list:
        for size, qty in zip(product.sizes, product.qty, strict=False):
            if size in size_to_qty:
                # Size already exists, add the quantities
                size_to_qty[size] += qty
            else:
                size_to_qty[size] = qty

    # Convert back to tuples
    all_sizes = tuple(size_to_qty.keys())
    all_qty = tuple(size_to_qty.values())

    # Create merged product
    merged = ProductData(
        product_code=highest_price_product.product_code,
        description=highest_price_product.description,
        category=highest_price_product.category,
        sizes=all_sizes,
        qty=all_qty,
        brand=highest_price_product.brand,
        rrp=highest_price_product.rrp,
        price=highest_price_product.price,
        currency=highest_price_product.currency,
        weight_kg=highest_price_product.weight_kg,
        image_url=highest_price_product.image_url,
    )

    logger.info(
        "  Keeping price %s %s",
        highest_price_product.price,
        highest_price_product.currency,
    )
    logger.info("  Combined to %d unique variants", len(all_sizes))

    return merged


def validate_product_types(products: list[ProductData]) -> dict[str, "ProductType"]:
    """Validate that all categories exist as ProductTypes.

    Args:
        products: List of ProductData instances

    Returns:
        Mapping of category_name -> ProductType object

    Raises:
        CommandError: If any category is missing

    """
    from saleor.product.models import ProductType

    # Get unique categories from products
    unique_categories = {p.category for p in products if p.category}

    logger.info(
        "Validating %d unique categories against ProductTypes...",
        len(unique_categories),
    )

    # Fetch all matching ProductTypes
    product_types = ProductType.objects.filter(name__in=unique_categories)
    product_type_map = {pt.name: pt for pt in product_types}

    # Check for missing categories
    missing = unique_categories - set(product_type_map.keys())
    if missing:
        raise MissingDatabaseSetup(
            f"ProductType validation failed! Missing ProductTypes: {', '.join(sorted(missing))}. "
            f"Please create these ProductTypes in the database before ingesting products."
        )

    logger.info("All categories found in ProductTypes")
    return product_type_map


def validate_categories(products: list[ProductData]) -> dict[str, "Category"]:
    """Validate that all categories exist as Categories.

    Args:
        products: List of ProductData instances

    Returns:
        Mapping of category_name -> Category object

    Raises:
        CommandError: If any category is missing

    """
    from saleor.product.models import Category

    # Get unique categories from products
    unique_categories = {p.category for p in products if p.category}

    logger.info(
        "Validating %d unique categories against Categories...", len(unique_categories)
    )

    # Fetch all matching Categories
    categories = Category.objects.filter(name__in=unique_categories)
    category_map = {cat.name: cat for cat in categories}

    # Check for missing categories
    missing = unique_categories - set(category_map.keys())
    if missing:
        raise MissingDatabaseSetup(
            f"Category validation failed! Missing Categories: {', '.join(sorted(missing))}. "
            f"Please create these Categories in the database before ingesting products."
        )

    logger.info("All categories found in Categories")
    return category_map


def validate_attributes() -> dict[str, Attribute]:
    """Validate that required attributes exist.

    Validates: RRP, Product Code, Size, Minimum Order Quantity, Brand.

    Returns:
        Mapping of attribute_name -> Attribute object

    Raises:
        CommandError: If any attribute is missing

    """
    required_attributes = [
        "RRP",
        "Product Code",
        "Size",
        "Minimum Order Quantity",
        "Brand",
    ]

    logger.info("Validating required attributes: %s...", ", ".join(required_attributes))

    # Fetch all matching Attributes
    attributes = Attribute.objects.filter(name__in=required_attributes)
    attribute_map = {attr.name: attr for attr in attributes}

    # Check for missing attributes
    missing = set(required_attributes) - set(attribute_map.keys())
    if missing:
        raise MissingDatabaseSetup(
            f"Attribute validation failed! Missing Attributes: {', '.join(sorted(missing))}. "
            f"Please create these Attributes in the database before ingesting products."
        )

    logger.info("All required attributes found")
    return attribute_map


def validate_product_type_attributes(
    product_type_map: dict[str, "ProductType"], attribute_map: dict[str, Attribute]
) -> None:
    """Validate that all required attributes are properly assigned to each product type.

    Product-level: Product Code, RRP, Minimum Order Quantity, Brand
    Variant-level: Size

    Also validates that attributes are NOT in the wrong level.

    Raises:
        CommandError: If any product type is missing required attributes

    """
    logger.info("Validating product type attribute assignments...")

    product_level_attrs = ["Product Code", "RRP", "Minimum Order Quantity", "Brand"]
    variant_level_attrs = ["Size"]

    errors = []

    for _category_name, product_type in product_type_map.items():
        logger.info("  Checking product type: %s", product_type.name)

        # Check product-level attributes
        assigned_product_attrs = set(
            product_type.attributeproduct.values_list("attribute__name", flat=True)
        )
        missing_product_attrs = set(product_level_attrs) - assigned_product_attrs

        if missing_product_attrs:
            error_msg = f"Product type '{product_type.name}' is missing product attributes: {', '.join(missing_product_attrs)}"
            errors.append(error_msg)
            logger.error("    %s", error_msg)

        # Check variant-level attributes
        assigned_variant_attrs = set(
            product_type.attributevariant.values_list("attribute__name", flat=True)
        )
        missing_variant_attrs = set(variant_level_attrs) - assigned_variant_attrs

        if missing_variant_attrs:
            error_msg = f"Product type '{product_type.name}' is missing variant attributes: {', '.join(missing_variant_attrs)}"
            errors.append(error_msg)
            logger.error("    %s", error_msg)

        # Check for misplaced attributes
        misplaced_in_variants = set(product_level_attrs) & assigned_variant_attrs
        if misplaced_in_variants:
            error_msg = f"Product type '{product_type.name}' has product-level attributes incorrectly assigned as variant attributes: {', '.join(misplaced_in_variants)}"
            errors.append(error_msg)
            logger.error("    %s", error_msg)

        misplaced_in_products = set(variant_level_attrs) & assigned_product_attrs
        if misplaced_in_products:
            error_msg = f"Product type '{product_type.name}' has variant-level attributes incorrectly assigned as product attributes: {', '.join(misplaced_in_products)}"
            errors.append(error_msg)
            logger.error("    %s", error_msg)

        if (
            not missing_product_attrs
            and not missing_variant_attrs
            and not misplaced_in_variants
            and not misplaced_in_products
        ):
            logger.info("    All attributes configured correctly")

    if errors:
        raise MissingDatabaseSetup(
            f"Product type attribute validation failed! {len(errors)} issue(s) found:\n"
            + "\n".join(f"  • {error}" for error in errors)
            + "\nPlease configure the attributes correctly in Saleor admin."
        )

    logger.info("All product types have required attributes configured correctly")


def separate_existing_products_by_warehouse(
    existing_products_map: dict[ProductData, "Product"], warehouse: Warehouse
) -> tuple[
    dict[ProductData, "Product"],
    dict[ProductData, "Product"],
]:
    """Separate existing products by whether they have stock in target warehouse.

    Args:
        existing_products_map: Map of ProductData to existing Product instances
        warehouse: Target warehouse to check

    Returns:
        Tuple of (products_in_warehouse, products_elsewhere):
        - products_in_warehouse: Products with stock in target warehouse
        - products_elsewhere: Products without stock in target warehouse

    """
    from saleor.product.models import ProductVariant

    products_in_warehouse = {}
    products_elsewhere = {}

    for product_data, existing_product in existing_products_map.items():
        variants = ProductVariant.objects.filter(product=existing_product)
        has_stock_in_warehouse = Stock.objects.filter(
            product_variant__in=variants, warehouse=warehouse
        ).exists()

        if has_stock_in_warehouse:
            products_in_warehouse[product_data] = existing_product
        else:
            products_elsewhere[product_data] = existing_product

    logger.info(
        "Warehouse '%s': %d products present, %d products in other warehouses",
        warehouse.name,
        len(products_in_warehouse),
        len(products_elsewhere),
    )

    return products_in_warehouse, products_elsewhere


def check_stock_update_mode_and_raise(
    existing_products_map: dict[ProductData, "Product"],
    warehouse: Warehouse,
    config: IngestConfig,
) -> None:
    """Check if products exist in warehouse and raise exception if mode not set.

    Args:
        existing_products_map: Map of ProductData to existing Product instances
        warehouse: Target warehouse to check
        config: Ingestion configuration

    Raises:
        StockUpdateModeRequired: If products exist in warehouse and
            config.stock_update_mode is None

    """
    if not existing_products_map:
        return  # No existing products, nothing to check

    # Separate products by warehouse
    products_in_warehouse, _ = separate_existing_products_by_warehouse(
        existing_products_map, warehouse
    )

    # If products exist in this warehouse and mode not set, raise exception
    if products_in_warehouse and config.stock_update_mode is None:
        products_list = [
            (product_data, product)
            for product_data, product in products_in_warehouse.items()
        ]
        raise StockUpdateModeRequired(products_list, warehouse.name)


def check_minimum_order_quantity_and_raise(
    product_count: int, config: IngestConfig
) -> None:
    """Check if MOQ is set and raise exception if not.

    Args:
        product_count: Number of products being ingested
        config: Ingestion configuration

    Raises:
        MinimumOrderQuantityRequired: If config.minimum_order_quantity is None

    """
    if config.minimum_order_quantity is None:
        raise MinimumOrderQuantityRequired(product_count)


def check_price_interpretation_and_raise(config: IngestConfig) -> None:
    """Check if price interpretation is confirmed and raise exception if not.

    Args:
        config: Ingestion configuration

    Raises:
        PriceInterpretationConfirmationRequired: If config.confirm_price_interpretation
            is False and products are for shop (not_for_web=False)

    """
    if not config.not_for_web and not config.confirm_price_interpretation:
        raise PriceInterpretationConfirmationRequired()


@attrs.frozen
class PreparedProductsData:
    """Result of product preparation phase."""

    products: list[ProductData]
    product_type_map: dict[str, "ProductType"]
    category_map: dict[str, "Category"]
    attribute_map: dict[str, Attribute]
    new_products: list[ProductData]
    existing_products_map: dict[ProductData, "Product"]


def prepare_products_for_ingestion(
    file_path: str,
    config: IngestConfig,
) -> PreparedProductsData:
    """Prepare products from Excel file for ingestion.

    This function handles:
    - Reading Excel file
    - Parsing rows into ProductData
    - De-duplication
    - All validations
    - Checking for existing products

    Args:
        file_path: Path to Excel file
        config: Ingestion configuration

    Returns:
        PreparedProductsData with validated products and mappings

    Raises:
        CommandError: If validation fails

    """
    logger.info("Reading products from: %s", file_path)

    # Read Excel file
    df = read_excel_with_validation(file_path, config.sheet_name, config.header_row)
    logger.info("Total rows in Excel: %d", len(df))

    # Check if column mapping is provided (None triggers interactive prompt)
    if config.column_mapping is None:
        available_columns = [str(col) for col in df.columns]
        raise ColumnMappingRequired(available_columns)

    # Validate that required columns exist in the DataFrame
    _validate_column_mapping(df, config.column_mapping)

    logger.info("Columns found: %s", ", ".join(str(col) for col in df.columns))

    products = []
    errors = []
    for row_idx, row in df.iterrows():
        try:
            product_data = process_excel_row(row, config.column_mapping, config)
            if product_data:
                products.append(product_data)
        except SheetIntegrityError as e:
            # Collect error with row number for better debugging
            errors.append(
                f"Row {row_idx + 2}: {str(e)}"
            )  # +2 for Excel row (header + 0-indexed)

    # If there were errors, raise them all at once
    if errors:
        error_summary = (
            f"Found {len(errors)} error(s) while processing Excel rows:\n"
            + "\n".join(f"  • {error}" for error in errors)
        )
        raise SheetIntegrityError(error_summary)

    logger.info("Parsed %d products from Excel", len(products))

    # Deduplicate products (raises DuplicateProducts if config.error_on_duplicates_in_sheet)
    products = deduplicate_products(products, config)
    logger.info("After de-duplication: %d products", len(products))

    # Validation phase
    logger.info("=== Validation Phase ===")
    product_type_map = validate_product_types(products)
    category_map = validate_categories(products)
    attribute_map = validate_attributes()
    validate_product_type_attributes(product_type_map, attribute_map)

    # Check for existing products
    new_products, existing_products_map = check_existing_products(products)

    logger.info("All validations passed!")

    return PreparedProductsData(
        products=products,
        product_type_map=product_type_map,
        category_map=category_map,
        attribute_map=attribute_map,
        new_products=new_products,
        existing_products_map=existing_products_map,
    )


def check_existing_products(
    products: list[ProductData],
) -> tuple[list[ProductData], dict[ProductData, "Product"]]:
    """Check which products already exist in the database.

    Note: Duplicate name validation is handled in deduplicate_products() phase.

    Args:
        products: List of ProductData to check

    Returns:
        Tuple of (new_products, existing_products_map) where:
        - new_products: list of ProductData that don't exist in DB
        - existing_products_map: dict mapping ProductData to existing Product instances

    """
    from saleor.product.models import Product

    logger.info("Checking for existing products in database...")

    # Generate slugs and codes for all products
    slug_to_products: dict[str, list[ProductData]] = {}
    product_code_to_products: dict[str, list[ProductData]] = {}

    for product in products:
        slug = slugify(product.description)
        if slug not in slug_to_products:
            slug_to_products[slug] = []
        slug_to_products[slug].append(product)

        code = product.product_code
        if code not in product_code_to_products:
            product_code_to_products[code] = []
        product_code_to_products[code].append(product)

    # Check against existing products in database by BOTH slug AND product code
    all_slugs = list(slug_to_products.keys())
    all_codes = list(product_code_to_products.keys())

    existing_by_slug = {p.slug: p for p in Product.objects.filter(slug__in=all_slugs)}

    # Get existing products by product code attribute
    product_code_attr = Attribute.objects.filter(name="Product Code").first()
    existing_by_code = {}
    if product_code_attr:
        matching_codes = AttributeValue.objects.filter(
            attribute=product_code_attr, name__in=all_codes
        ).prefetch_related("productvalueassignment__product")

        for code_value in matching_codes:
            for assignment in code_value.productvalueassignment.all():
                existing_by_code[code_value.name] = assignment.product

    # Separate new vs existing products
    new_products = []
    existing_products_map = {}

    for product_data in products:
        slug = slugify(product_data.description)
        code = product_data.product_code

        # Check if exists by slug OR product code
        existing_product = existing_by_slug.get(slug) or existing_by_code.get(code)

        if existing_product:
            existing_products_map[product_data] = existing_product
            logger.info(
                "  Found existing: %s - %s",
                product_data.product_code,
                product_data.description,
            )
        else:
            new_products.append(product_data)

    if existing_products_map:
        logger.warning(
            "Found %d product(s) that already exist in database",
            len(existing_products_map),
        )

    if new_products:
        logger.info("%d new product(s) will be created", len(new_products))

    return new_products, existing_products_map


# ============================================================================
# Database Operations
# ============================================================================


def get_exchange_rates() -> dict[str, float]:
    """Fetch current exchange rates from Frankfurter API.

    Returns:
        Dictionary mapping currency codes to exchange rates (relative to base currency)

    """
    from saleor.core.http_client import HTTPClient

    try:
        response = HTTPClient.send_request(
            "GET",
            "https://api.frankfurter.app/latest",
            timeout=10,
            allow_redirects=True,
        )
        response.raise_for_status()
        data = response.json()
        rates = data.get("rates", {})
        logger.info("Fetched exchange rates: %s", rates)
        return rates
    except Exception as e:
        logger.error("Failed to fetch exchange rates: %s", e)
        logger.warning("Using default exchange rates (1:1)")
        return {}


def convert_price(
    price: float, from_currency: str, to_currency: str, exchange_rates: dict[str, float]
) -> Decimal:
    """Convert price from one currency to another.

    Args:
        price: Price amount to convert
        from_currency: Source currency code (e.g., "GBP")
        to_currency: Target currency code (e.g., "USD")
        exchange_rates: Dictionary of exchange rates

    Returns:
        Converted price as Decimal

    """
    if from_currency == to_currency:
        return Decimal(str(price))

    # If no exchange rates available, return as-is
    if not exchange_rates:
        logger.warning(
            "No exchange rates available, using price as-is: %s %s",
            price,
            from_currency,
        )
        return Decimal(str(price))

    # Get rates for both currencies
    from_rate = exchange_rates.get(from_currency, 1.0)
    to_rate = exchange_rates.get(to_currency, 1.0)

    # Convert: price / from_rate * to_rate
    converted = (price / from_rate) * to_rate
    result = Decimal(str(converted)).quantize(Decimal("0.01"))

    logger.debug(
        "Converted %s %s to %s %s (rates: %s=%s, %s=%s)",
        price,
        from_currency,
        result,
        to_currency,
        from_currency,
        from_rate,
        to_currency,
        to_rate,
    )

    return result


def create_product(
    product_data: ProductData,
    product_type: "ProductType",
    category: "Category",
) -> "Product":
    """Create a Product instance.

    Args:
        product_data: Product data from Excel
        product_type: ProductType to assign
        category: Category to assign

    Returns:
        Created Product instance

    """
    from saleor.product.models import Product

    base_slug = slugify(f"{product_data.description}-{product_data.product_code}")
    slug = base_slug
    counter = 1
    while Product.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1

    product = Product.objects.create(
        name=product_data.description,
        slug=slug,
        product_type=product_type,
        category=category,
    )

    logger.info("Created product: %s (ID: %s)", product.name, product.id)
    return product


def create_product_channel_listing(
    product: "Product",
    channel: "Channel",
    visible_in_listings: bool = False,
    *,
    is_published: bool = True,
    available_for_purchase: bool = True,
) -> "ProductChannelListing":
    """Create ProductChannelListing for a product.

    Args:
        product: Product instance
        channel: Channel to create listing for
        visible_in_listings: If True, show the product in listing/search pages.
        is_published: If False, product is hidden from the storefront entirely.
        available_for_purchase: If False, product cannot be added to cart/orders.

    Returns:
        Created ProductChannelListing instance

    """
    from django.utils import timezone

    from saleor.product.models import ProductChannelListing

    listing = ProductChannelListing.objects.create(
        product=product,
        channel=channel,
        currency=channel.currency_code,
        is_published=is_published,
        visible_in_listings=visible_in_listings,
        available_for_purchase_at=timezone.now() if available_for_purchase else None,
    )

    logger.debug(
        "Created channel listing for %s on %s (published: %s, visible: %s)",
        product.name,
        channel.name,
        listing.is_published,
        listing.visible_in_listings,
    )
    return listing


def create_product_media(product: "Product", image_url: str) -> "ProductMedia | None":
    """Create ProductMedia from image URL.

    Args:
        product: Product instance
        image_url: URL of image to fetch and attach. Supports both http(s):// URLs
            and data: URIs (e.g. data:image/jpeg;base64,...).

    Returns:
        Created ProductMedia instance, or None if fetch failed

    """
    import base64
    import mimetypes
    import uuid
    from urllib.parse import urlparse

    from django.core.files.base import ContentFile

    from saleor.product.models import ProductMedia

    try:
        if image_url.startswith("data:"):
            header, encoded = image_url.split(",", 1)
            mime_type = header[5:].split(";")[0]
            image_data = base64.b64decode(encoded)
            ext_with_dot = mimetypes.guess_extension(mime_type) or ".jpg"
            ext = ext_with_dot.lstrip(".")
            if ext in ("jpe", "jpeg"):
                ext = "jpg"
        else:
            from saleor.core.http_client import HTTPClient

            response = HTTPClient.send_request(
                "GET",
                image_url,
                timeout=30,
                allow_redirects=True,
            )
            response.raise_for_status()
            image_data = response.content

            parsed_url = urlparse(image_url)
            url_path = parsed_url.path.split("/")[-1]
            if "." in url_path and not url_path.startswith("."):
                ext = url_path.split(".")[-1].split("?")[0]
            else:
                ext = "jpg"

        filename = f"{uuid.uuid4()}.{ext}"
        image_file = ContentFile(image_data, name=filename)

        media = ProductMedia.objects.create(
            product=product,
            alt=product.name,
        )
        media.image.save(filename, image_file, save=True)

        logger.info(
            "Created product media for %s from %s", product.name, image_url[:80]
        )
        return media

    except Exception as e:
        logger.error("Failed to create product media for %s: %s", product.name, e)
        return None


def assign_product_attributes(
    product: "Product",
    product_data: ProductData,
    attribute_map: dict[str, Attribute],
    moq_value: int,
) -> None:
    """Assign product-level attributes (Product Code, RRP, MOQ, Brand).

    Args:
        product: Product instance
        product_data: Product data from Excel
        attribute_map: Map of attribute names to Attribute instances
        moq_value: Minimum order quantity value

    """
    # Product Code
    code_attr = attribute_map["Product Code"]
    code_slug = slugify(product_data.product_code)
    code_value, _ = AttributeValue.objects.get_or_create(
        attribute=code_attr,
        slug=code_slug,
        defaults={
            "name": product_data.product_code,
            "plain_text": product_data.product_code,
        },
    )
    AssignedProductAttributeValue.objects.create(product=product, value=code_value)

    # RRP (if present)
    if product_data.rrp:
        rrp_attr = attribute_map["RRP"]
        rrp_slug = slugify(str(product_data.rrp))
        rrp_value, _ = AttributeValue.objects.get_or_create(
            attribute=rrp_attr,
            slug=rrp_slug,
            defaults={
                "name": str(product_data.rrp),
                "plain_text": str(product_data.rrp),
            },
        )
        AssignedProductAttributeValue.objects.create(product=product, value=rrp_value)

    # MOQ
    moq_attr = attribute_map["Minimum Order Quantity"]
    moq_slug = slugify(str(moq_value))
    moq_attr_value, _ = AttributeValue.objects.get_or_create(
        attribute=moq_attr,
        slug=moq_slug,
        defaults={"name": str(moq_value), "plain_text": str(moq_value)},
    )
    AssignedProductAttributeValue.objects.create(product=product, value=moq_attr_value)

    # Brand
    brand_attr = attribute_map["Brand"]
    brand_slug = slugify(product_data.brand)
    brand_value, _ = AttributeValue.objects.get_or_create(
        attribute=brand_attr,
        slug=brand_slug,
        defaults={"name": product_data.brand, "plain_text": product_data.brand},
    )
    AssignedProductAttributeValue.objects.create(product=product, value=brand_value)

    logger.debug(
        "Assigned attributes to %s: code=%s, rrp=%s, moq=%s, brand=%s",
        product.name,
        product_data.product_code,
        product_data.rrp,
        moq_value,
        product_data.brand,
    )


def create_variant(
    product: "Product", size: str, weight_kg: float | None = None
) -> "ProductVariant":
    """Create a ProductVariant for a given size.

    Args:
        product: Product instance
        size: Size value (e.g., "8", "M", "10.5")
        weight_kg: Weight in kilograms (optional)

    Returns:
        Created ProductVariant instance

    """
    from measurement.measures import Weight

    from saleor.product.models import ProductVariant

    # Convert weight to Weight object if provided
    weight_obj = Weight(kg=weight_kg) if weight_kg is not None else None

    variant = ProductVariant.objects.create(
        product=product,
        name=size,
        sku=f"{product.slug}-{slugify(size)}",
        weight=weight_obj,
    )

    logger.debug("Created variant: %s for %s", variant.name, product.name)
    return variant


def assign_variant_attributes(
    variant: "ProductVariant",
    size: str,
    attribute_map: dict[str, Attribute],
    product_type: "ProductType",
) -> None:
    """Assign variant-level attributes (Size).

    Args:
        variant: ProductVariant instance
        size: Size value
        attribute_map: Map of attribute names to Attribute instances
        product_type: ProductType of the product

    """
    size_attr = attribute_map["Size"]

    # Get or create size value (lookup by slug to avoid conflicts)
    size_slug = slugify(f"size-{size}")
    size_value, _ = AttributeValue.objects.get_or_create(
        attribute=size_attr,
        slug=size_slug,
        defaults={"name": size},
    )

    # Get or create AttributeVariant assignment
    attr_variant, _ = AttributeVariant.objects.get_or_create(
        attribute=size_attr,
        product_type=product_type,
    )

    # Get or create AssignedVariantAttribute
    assigned_attr, _ = AssignedVariantAttribute.objects.get_or_create(
        variant=variant,
        assignment=attr_variant,
    )

    # Create AssignedVariantAttributeValue
    AssignedVariantAttributeValue.objects.create(
        value=size_value,
        assignment=assigned_attr,
        variant=variant,
    )

    logger.debug("Assigned size attribute '%s' to variant %s", size, variant.name)


def create_variant_channel_listing(
    variant: "ProductVariant",
    channel: "Channel",
    product_data: ProductData,
    exchange_rates: dict[str, float],
    not_for_web: bool = False,
) -> "ProductVariantChannelListing":
    """Create ProductVariantChannelListing with price conversion.

    Args:
        variant: ProductVariant instance
        channel: Channel to create listing for
        product_data: Product data containing pricing info
        exchange_rates: Exchange rate dictionary
        not_for_web: Not used for variant listings (availability is controlled at product level)

    Returns:
        Created ProductVariantChannelListing instance

    """
    from saleor.product.models import ProductVariantChannelListing

    # Price is always required - convert to channel currency
    if product_data.price is None:
        raise ValueError(
            f"Price is required for variant {variant.name}. "
            f"Use product-level not_for_web flag to mark products as unavailable, "
            f"but prices must still be set."
        )

    price = convert_price(
        float(product_data.price),
        product_data.currency,
        channel.currency_code,
        exchange_rates,
    )

    listing = ProductVariantChannelListing.objects.create(
        variant=variant,
        channel=channel,
        currency=channel.currency_code,
        price_amount=price,
        discounted_price_amount=price,
    )

    logger.debug(
        "Created variant listing: %s on %s at %s %s",
        variant.name,
        channel.name,
        price,
        channel.currency_code,
    )
    return listing


def create_stock(
    variant: "ProductVariant",
    warehouse: Warehouse,
    quantity: int,
) -> Stock:
    """Create Stock entry for a variant in a warehouse.

    Args:
        variant: ProductVariant instance
        warehouse: Warehouse instance
        quantity: Stock quantity

    Returns:
        Created Stock instance

    """
    stock = Stock.objects.create(
        product_variant=variant,
        warehouse=warehouse,
        quantity=quantity,
    )

    logger.debug(
        "Created stock: %s in %s with quantity %d",
        variant.name,
        warehouse.name,
        quantity,
    )
    return stock


def update_stock(
    variant: "ProductVariant",
    warehouse: Warehouse,
    quantity: int,
    mode: str,
) -> Stock:
    """Update or create Stock entry for a variant in a warehouse.

    Args:
        variant: ProductVariant instance
        warehouse: Warehouse instance
        quantity: Stock quantity to add or set
        mode: "replace" or "add"

    Returns:
        Updated or created Stock instance

    """
    stock, created = Stock.objects.get_or_create(
        product_variant=variant,
        warehouse=warehouse,
        defaults={"quantity": quantity},
    )

    if not created:
        old_qty = stock.quantity
        if mode == "replace":
            stock.quantity = quantity
        else:  # mode == "add"
            stock.quantity += quantity
        stock.save()

        logger.info(
            "Updated stock for %s in %s: %d → %d (%s mode)",
            variant.name,
            warehouse.name,
            old_qty,
            stock.quantity,
            mode,
        )
    else:
        logger.info(
            "Created stock for %s in %s: %d", variant.name, warehouse.name, quantity
        )

    return stock


def ingest_new_products(
    products: list[ProductData],
    product_type_map: dict[str, "ProductType"],
    category_map: dict[str, "Category"],
    attribute_map: dict[str, Attribute],
    channels: list["Channel"],
    warehouse: Warehouse,
    exchange_rates: dict[str, float],
    moq_value: int,
    not_for_web: bool = False,
) -> list["Product"]:
    """Ingest new products into the database.

    Args:
        products: List of ProductData to ingest
        product_type_map: Map of category names to ProductType instances
        category_map: Map of category names to Category instances
        attribute_map: Map of attribute names to Attribute instances
        channels: List of Channel instances
        warehouse: Warehouse to create stock in
        exchange_rates: Exchange rate dictionary
        moq_value: Minimum order quantity
        not_for_web: If True, mark products as unpublished

    Returns:
        List of created Product instances

    """
    created_products = []

    logger.info("Ingesting %d new products...", len(products))

    for product_data in products:
        logger.info(
            "Processing: %s - %s", product_data.product_code, product_data.description
        )

        # 1. Create Product
        product = create_product(
            product_data,
            product_type_map[product_data.category],
            category_map[product_data.category],
        )

        # 2. Create ProductChannelListings for all channels
        for channel in channels:
            create_product_channel_listing(
                product,
                channel,
                visible_in_listings=not not_for_web,
                is_published=not not_for_web,
                available_for_purchase=not not_for_web,
            )

        # 3. Create ProductMedia (if image URL exists)
        if product_data.image_url:
            create_product_media(product, product_data.image_url)

        # 4. Assign product-level attributes
        assign_product_attributes(product, product_data, attribute_map, moq_value)

        # 5. Create variants for each size
        for size, qty in zip(product_data.sizes, product_data.qty, strict=False):
            variant = create_variant(
                product,
                size,
                weight_kg=float(product_data.weight_kg)
                if product_data.weight_kg is not None
                else None,
            )

            # 6. Assign variant-level attributes
            assign_variant_attributes(
                variant,
                size,
                attribute_map,
                product_type_map[product_data.category],
            )

            # 7. Create channel listings with converted prices
            for channel in channels:
                create_variant_channel_listing(
                    variant, channel, product_data, exchange_rates, not_for_web
                )

            # 8. Create stock
            create_stock(variant, warehouse, qty)

        logger.info(
            "Created product: %s with %d variants",
            product.name,
            len(product_data.sizes),
        )
        created_products.append(product)

    logger.info("Successfully created %d products", len(created_products))
    return created_products


def update_variant_channel_listing_prices(
    variant: "ProductVariant",
    channels: list["Channel"],
    product_data: ProductData,
    exchange_rates: dict[str, float],
    not_for_web: bool = False,
) -> None:
    """Update variant channel listing prices to max(existing, new).

    For each channel:
    - Convert new price from Excel to channel currency
    - Update price to max(existing_price, new_price)

    Note: not_for_web only affects product-level availability, not variant prices.
    Prices are always updated regardless of not_for_web setting.

    Args:
        variant: ProductVariant to update
        channels: List of channels to update
        product_data: Product data with new prices
        exchange_rates: Exchange rate dictionary
        not_for_web: Not used (kept for API compatibility)

    """
    from saleor.product.models import ProductVariantChannelListing

    for channel in channels:
        listing = ProductVariantChannelListing.objects.filter(
            variant=variant, channel=channel
        ).first()

        if not listing:
            # No listing exists - create it
            create_variant_channel_listing(
                variant, channel, product_data, exchange_rates, not_for_web
            )
            continue

        # Listing exists - update with max price logic
        # Price is required - raise error if missing
        if product_data.price is None:
            raise ValueError(
                f"Price is required for variant {variant.name}. "
                f"Prices must be set for all products, including not_for_web products."
            )

        new_price = convert_price(
            float(product_data.price),
            product_data.currency,
            channel.currency_code,
            exchange_rates,
        )

        # Use max of existing and new price
        if listing.price_amount is None:
            listing.price_amount = new_price
            logger.info(
                "  Set price for %s on %s: %s %s (was None)",
                variant.name,
                channel.name,
                new_price,
                channel.currency_code,
            )
        else:
            old_price = listing.price_amount
            listing.price_amount = max(old_price, new_price)
            if listing.price_amount != old_price:
                logger.info(
                    "  Updated price for %s on %s: %s → %s %s",
                    variant.name,
                    channel.name,
                    old_price,
                    listing.price_amount,
                    channel.currency_code,
                )

        listing.save()


def update_existing_products(
    existing_products_map: dict[ProductData, "Product"],
    attribute_map: dict[str, Attribute],
    channels: list["Channel"],
    warehouse: Warehouse,
    exchange_rates: dict[str, float],
    stock_update_mode: str,
    not_for_web: bool = False,
) -> list["Product"]:
    """Update existing products with new variants/quantities.

    For existing variants:
    - Updates stock (REPLACE or ADD based on stock_update_mode)
    - Updates price to max(existing, new)

    Args:
        existing_products_map: Map of ProductData to existing Product instances
        attribute_map: Map of attribute names to Attribute instances
        channels: List of Channel instances
        warehouse: Warehouse to update stock in
        exchange_rates: Exchange rate dictionary
        stock_update_mode: "replace" or "add"
        not_for_web: If True, use price of 0

    Returns:
        List of updated Product instances

    """
    updated_products = []

    logger.info("Updating %d existing products...", len(existing_products_map))

    for product_data, existing_product in existing_products_map.items():
        logger.info("Updating: %s", existing_product.name)

        # Get existing variants for this product with their sizes
        size_to_variant = get_size_to_variant_map(existing_product)

        # Process each size in the Excel data
        for size, qty in zip(product_data.sizes, product_data.qty, strict=False):
            existing_variant = size_to_variant.get(size)

            if existing_variant:
                # Variant exists - update stock AND prices
                update_stock(existing_variant, warehouse, qty, stock_update_mode)

                # Update prices: price = max(existing, new)
                update_variant_channel_listing_prices(
                    existing_variant,
                    channels,
                    product_data,
                    exchange_rates,
                    not_for_web,
                )
            else:
                # Variant doesn't exist - create it
                variant = create_variant(
                    existing_product,
                    size,
                    weight_kg=float(product_data.weight_kg)
                    if product_data.weight_kg is not None
                    else None,
                )

                # Assign variant attributes
                assign_variant_attributes(
                    variant,
                    size,
                    attribute_map,
                    existing_product.product_type,
                )

                # Create channel listings with new prices
                for channel in channels:
                    create_variant_channel_listing(
                        variant, channel, product_data, exchange_rates, not_for_web
                    )

                # Create stock
                create_stock(variant, warehouse, qty)
                logger.info(
                    "Created new variant: %s for %s", size, existing_product.name
                )

        updated_products.append(existing_product)

    logger.info("Successfully updated %d products", len(updated_products))
    return updated_products


def ingest_products_from_excel(
    config: IngestConfig,
    excel_file: str,
) -> IngestionResult:
    """Ingest products from Excel file into warehouse.

    This function orchestrates the entire ingestion process:
    1. Validates and prepares products from Excel
    2. Checks for interactive decisions (raises exceptions if needed)
    3. Creates/gets warehouse
    4. Performs database operations in a transaction
    5. Returns result

    Args:
        config: IngestConfig with all settings
        excel_file: Path to Excel file

    Returns:
        IngestionResult with statistics

    Raises:
        InteractiveDecisionRequired: If a decision needs to be made
        CommandError: If validation fails

    """
    from django.db import transaction

    from saleor.channel.models import Channel

    logger.info("=" * 80)
    logger.info("Starting product ingestion from Excel")
    logger.info("=" * 80)

    # Step 1: Prepare products (validation + parsing)
    logger.info("\n=== Step 1: Preparing Products ===")
    prepared = prepare_products_for_ingestion(excel_file, config)

    # Step 2: Create or get warehouse (needed before checking stock update mode)
    logger.info("\n=== Step 2: Setting Up Warehouse ===")

    warehouse_slug = slugify(config.warehouse_name)
    try:
        warehouse = Warehouse.objects.get(slug=warehouse_slug)
        logger.info("Using existing warehouse '%s'", warehouse.name)

        # Validate warehouse for ingestion (raises exception if owned)
        validate_warehouse_for_ingestion(warehouse)
    except Warehouse.DoesNotExist:
        # Create address first (required for warehouse)
        from saleor.account.models import Address

        address = Address.objects.create(
            street_address_1=config.warehouse_address,
            city="",
            country=config.warehouse_country,
        )

        # Now create warehouse with address (default is_owned=False)
        warehouse = Warehouse.objects.create(
            slug=warehouse_slug,
            name=config.warehouse_name,
            address=address,
            is_owned=False,
        )

        # Validate warehouse for ingestion (raises exception if owned)
        validate_warehouse_for_ingestion(warehouse)

        logger.info(
            "Created warehouse '%s' — auto-assigned to all channels and shipping zones",
            warehouse.name,
        )

    # Step 3: Check for interactive decisions (now that we have warehouse)
    logger.info("\n=== Step 3: Checking Interactive Decisions ===")
    check_minimum_order_quantity_and_raise(len(prepared.products), config)
    check_price_interpretation_and_raise(config)
    check_stock_update_mode_and_raise(
        prepared.existing_products_map,
        warehouse,
        config,
    )

    # Step 4: Get channels and exchange rates
    logger.info("\n=== Step 4: Fetching Channels and Exchange Rates ===")
    channels = list(Channel.objects.all())
    logger.info("Found %d channels: %s", len(channels), [c.name for c in channels])

    exchange_rates = get_exchange_rates()

    # Step 5: Perform database operations in a transaction
    logger.info("\n=== Step 5: Ingesting Products ===")
    if config.dry_run:
        logger.info("DRY-RUN MODE: Changes will be rolled back")

    with transaction.atomic():
        # Ingest new products
        created_products = []
        if prepared.new_products:
            assert config.minimum_order_quantity is not None  # Validated earlier
            created_products = ingest_new_products(
                prepared.new_products,
                prepared.product_type_map,
                prepared.category_map,
                prepared.attribute_map,
                channels,
                warehouse,
                exchange_rates,
                config.minimum_order_quantity,
                config.not_for_web,
            )

        # Update existing products
        updated_products = []
        skipped_products = 0
        if prepared.existing_products_map:
            # Separate by warehouse
            products_in_warehouse, products_elsewhere = (
                separate_existing_products_by_warehouse(
                    prepared.existing_products_map, warehouse
                )
            )

            # Update products in this warehouse ONLY
            if products_in_warehouse:
                assert config.stock_update_mode is not None  # Validated earlier
                updated_products.extend(
                    update_existing_products(
                        products_in_warehouse,
                        prepared.attribute_map,
                        channels,
                        warehouse,
                        exchange_rates,
                        config.stock_update_mode,
                        config.not_for_web,
                    )
                )

            # Skip products that exist in other warehouses
            if products_elsewhere:
                skipped_products = len(products_elsewhere)
                logger.warning(
                    "Skipping %d product(s) that exist in other warehouses. We only update products already in warehouse '%s'. To add these products to this warehouse, remove them from other warehouses first.",
                    skipped_products,
                    warehouse.name,
                )
                for product_data, product in list(products_elsewhere.items())[:5]:
                    logger.warning(
                        "  - %s (%s): %s",
                        product_data.product_code,
                        product_data.brand,
                        product.name,
                    )
                if len(products_elsewhere) > 5:
                    logger.warning("  ... and %s more", len(products_elsewhere) - 5)

        # Update discounted prices for all affected products
        logger.info("\n=== Step 6: Updating Discounted Prices ===")
        from saleor.product.models import Product
        from saleor.product.utils.variant_prices import (
            update_discounted_prices_for_promotion,
        )

        all_products = created_products + updated_products
        product_ids = [p.id for p in all_products]
        products_queryset = Product.objects.filter(id__in=product_ids)
        update_discounted_prices_for_promotion(products_queryset)

        # Rollback transaction if dry-run
        if config.dry_run:
            logger.info("DRY-RUN MODE: Rolling back all changes")
            transaction.set_rollback(True)

    # Step 7: Update search vectors for all affected products (after transaction commits)
    if product_ids and not config.dry_run:
        logger.info("Updating search vector for %d product(s)", len(product_ids))
        from saleor.product.search import update_products_search_vector

        update_products_search_vector(product_ids)

    # Step 8: Calculate statistics
    total_variants_created = sum(p.variants.count() for p in created_products)
    total_variants_updated = sum(
        p.variants.filter(channel_listings__channel__in=channels).count()
        for p in updated_products
    )

    result = IngestionResult(
        created_products=created_products,
        updated_products=updated_products,
        total_products_processed=len(created_products) + len(updated_products),
        total_variants_created=total_variants_created,
        total_variants_updated=total_variants_updated,
        warehouse=warehouse,
        skipped_products=skipped_products,
    )

    logger.info("%s", "\n" + "=" * 80)
    logger.info("Ingestion Complete!")
    logger.info("=" * 80)
    logger.info(
        "Created: %d products (%d variants)",
        len(created_products),
        total_variants_created,
    )
    logger.info(
        "Updated: %d products (%d variants)",
        len(updated_products),
        total_variants_updated,
    )
    if skipped_products > 0:
        logger.warning(
            "Skipped: %d products (exist in other warehouses)", skipped_products
        )
    logger.info("Warehouse: %s", warehouse.name)
    all_product_ids = [p.pk for p in created_products] + [
        p.pk for p in updated_products
    ]
    if all_product_ids and not config.dry_run:
        logger.info(
            "Search indexes will be updated in the background for %d product(s)",
            len(all_product_ids),
        )
    logger.info("=" * 80)

    return result


def ingest_config_to_dict(config: IngestConfig) -> dict:
    """Serialize IngestConfig to a JSON-serializable dict for storage."""
    return attrs.asdict(config)


def ingest_config_from_dict(data: dict) -> IngestConfig:
    """Deserialize IngestConfig from a stored dict."""
    d = data.copy()
    mapping_data = d.pop("column_mapping", None)
    mapping = SpreadsheetColumnMapping(**mapping_data) if mapping_data else None
    return IngestConfig(column_mapping=mapping, **d)
