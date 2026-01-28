"""Tests for product ingestion utilities."""

import pytest

from saleor.attribute import AttributeInputType, AttributeType
from saleor.attribute.models import Attribute, AttributeValue
from saleor.attribute.models.product_variant import (
    AssignedVariantAttribute,
    AssignedVariantAttributeValue,
    AttributeVariant,
)
from saleor.product.ingestion import (
    IngestConfig,
    MissingDatabaseSetup,
    SizeQtyUnparseable,
    SpreadsheetColumnMapping,
    get_products_by_code_and_brand,
    get_size_to_variant_map,
    parse_sizes_and_qty,
)
from saleor.product.models import Product, ProductType, ProductVariant


def test_parse_sizes_and_qty_with_bracket_notation():
    """Test parsing sizes with quantity in brackets."""
    sizes_str = "6.5[1], 7[2], 7.5[9], 8[13]"

    sizes, quantities = parse_sizes_and_qty(sizes_str)

    assert sizes == ("6.5", "7", "7.5", "8")
    assert quantities == (1, 2, 9, 13)


def test_parse_sizes_and_qty_without_brackets():
    """Test parsing sizes without quantity brackets raises error."""
    sizes_str = "6.5, 7, 7.5"

    with pytest.raises(
        SizeQtyUnparseable, match="No valid size\\[qty\\] patterns found"
    ):
        parse_sizes_and_qty(sizes_str)


def test_parse_sizes_and_qty_empty_string():
    """Test parsing empty string returns empty tuples."""
    sizes, quantities = parse_sizes_and_qty("")

    assert sizes == ()
    assert quantities == ()


def test_parse_sizes_and_qty_mixed():
    """Test parsing mixed format (some with brackets, some without) extracts bracketed sizes."""
    sizes_str = "6.5[10], 7, 7.5[5]"

    # Mixed format is allowed - it extracts only the bracketed sizes
    sizes, quantities = parse_sizes_and_qty(sizes_str)

    assert sizes == ("6.5", "7.5")
    assert quantities == (10, 5)


def test_parse_sizes_and_qty_with_zero_quantity():
    """Test parsing sizes with explicit zero quantity."""
    sizes_str = "6.5[0], 7[2], 7.5[0]"

    sizes, quantities = parse_sizes_and_qty(sizes_str)

    assert sizes == ("6.5", "7", "7.5")
    assert quantities == (0, 2, 0)


def test_parse_sizes_and_qty_space_separated():
    """Test parsing space-separated sizes (e.g., 'S[5] M[10] L[3]')."""
    sizes_str = "S[5] M[10] L[3]"

    sizes, quantities = parse_sizes_and_qty(sizes_str)

    assert sizes == ("S", "M", "L")
    assert quantities == (5, 10, 3)


def test_parse_sizes_and_qty_space_separated_with_numbers():
    """Test parsing space-separated numeric sizes."""
    sizes_str = "8[5] 9[10] 10[3]"

    sizes, quantities = parse_sizes_and_qty(sizes_str)

    assert sizes == ("8", "9", "10")
    assert quantities == (5, 10, 3)


def test_spreadsheet_column_mapping_defaults():
    """Test SpreadsheetColumnMapping has correct defaults."""
    mapping = SpreadsheetColumnMapping()

    assert mapping.code == "Code"
    assert mapping.brand == "Brand"
    assert mapping.description == "Description"
    assert mapping.category == "Category"
    assert mapping.sizes == "Sizes"


def test_spreadsheet_column_mapping_custom():
    """Test SpreadsheetColumnMapping can be customized."""
    mapping = SpreadsheetColumnMapping(
        code="ProductCode", brand="BrandName", sizes="Size"
    )

    assert mapping.code == "ProductCode"
    assert mapping.brand == "BrandName"
    assert mapping.sizes == "Size"


def test_ingest_config_creation():
    """Test IngestConfig can be created with column mapping."""
    mapping = SpreadsheetColumnMapping()
    config = IngestConfig(
        column_mapping=mapping,
        warehouse_name="Dubai Warehouse",
        warehouse_address="123 Sheikh Zayed Rd, Dubai",
        warehouse_country="AE",
        sheet_name="Products",
        not_for_web=False,
        default_currency="AED",
    )

    assert config.warehouse_name == "Dubai Warehouse"
    assert config.warehouse_address == "123 Sheikh Zayed Rd, Dubai"
    assert config.sheet_name == "Products"
    assert config.not_for_web is False
    assert config.default_currency == "AED"
    assert config.column_mapping == mapping


def test_ingest_config_defaults():
    """Test IngestConfig has correct defaults."""
    mapping = SpreadsheetColumnMapping()
    config = IngestConfig(
        column_mapping=mapping,
        warehouse_name="Dubai Warehouse",
        warehouse_address="123 Sheikh Zayed Rd, Dubai",
        warehouse_country="AE",
    )

    assert config.warehouse_name == "Dubai Warehouse"
    assert config.warehouse_address == "123 Sheikh Zayed Rd, Dubai"
    assert config.sheet_name == "Sheet1"  # Default
    assert config.not_for_web is False  # Default
    assert config.default_currency is None  # Default


def test_get_size_to_variant_map(product_with_variants, size_attribute):
    """Test getting size to variant mapping for a product."""
    product = product_with_variants
    variants = ProductVariant.objects.filter(product=product)

    # Clean up any existing assignments to avoid conflicts with --reuse-db
    AssignedVariantAttributeValue.objects.filter(variant__product=product).delete()
    AssignedVariantAttribute.objects.filter(variant__product=product).delete()

    # Assign size attributes to variants
    attr_variant, _ = AttributeVariant.objects.get_or_create(
        attribute=size_attribute, product_type=product.product_type
    )

    size_values = {}
    for idx, variant in enumerate(variants):
        size_name = f"Size_{idx}"
        size_value, _ = AttributeValue.objects.get_or_create(
            attribute=size_attribute,
            slug=f"size-{idx}",
            defaults={"name": size_name},
        )
        size_values[size_name] = variant

        assigned_attr, _ = AssignedVariantAttribute.objects.get_or_create(
            variant=variant, assignment=attr_variant
        )
        AssignedVariantAttributeValue.objects.get_or_create(
            value=size_value, assignment=assigned_attr, variant=variant
        )

    # Test the function
    size_to_variant = get_size_to_variant_map(product)

    assert len(size_to_variant) == len(variants)
    for size_name, variant in size_values.items():
        assert size_to_variant[size_name] == variant


def test_get_size_to_variant_map_no_size_attribute(simple_product):
    """Test get_size_to_variant_map raises error if Size attribute missing."""
    # Delete Size attribute if it exists
    Attribute.objects.filter(name="Size").delete()

    with pytest.raises(MissingDatabaseSetup, match="Size attribute not found"):
        get_size_to_variant_map(simple_product)


def test_get_products_by_code_and_brand(
    simple_product, product_code_attribute, brand_attribute
):
    """Test getting products by code and brand."""
    from saleor.attribute.models.product import AssignedProductAttributeValue

    # Assign product code and brand to product
    code_value = AttributeValue.objects.create(
        attribute=product_code_attribute, name="TEST-001", slug="test-001"
    )
    brand_value = AttributeValue.objects.create(
        attribute=brand_attribute, name="TestBrand", slug="testbrand"
    )

    AssignedProductAttributeValue.objects.create(
        product=simple_product, value=code_value
    )
    AssignedProductAttributeValue.objects.create(
        product=simple_product, value=brand_value
    )

    # Test the function
    result = get_products_by_code_and_brand(["TEST-001"])

    assert ("TEST-001", "TestBrand") in result
    assert result[("TEST-001", "TestBrand")] == simple_product


def test_get_products_by_code_and_brand_no_attribute(simple_product):
    """Test get_products_by_code_and_brand raises error if attributes missing."""
    # Delete Product Code attribute if it exists
    Attribute.objects.filter(name="Product Code").delete()

    with pytest.raises(MissingDatabaseSetup, match="Product Code attribute not found"):
        get_products_by_code_and_brand(["TEST-001"])


@pytest.fixture
def size_attribute():
    """Create Size attribute fixture."""
    attr, _ = Attribute.objects.get_or_create(
        slug="size",
        defaults={
            "name": "Size",
            "type": AttributeType.PRODUCT_TYPE,
            "input_type": AttributeInputType.DROPDOWN,
        },
    )
    return attr


@pytest.fixture
def product_code_attribute():
    """Create Product Code attribute fixture."""
    attr, _ = Attribute.objects.get_or_create(
        slug="product-code",
        defaults={
            "name": "Product Code",
            "type": AttributeType.PRODUCT_TYPE,
            "input_type": AttributeInputType.PLAIN_TEXT,
        },
    )
    return attr


@pytest.fixture
def brand_attribute():
    """Create Brand attribute fixture."""
    attr, _ = Attribute.objects.get_or_create(
        slug="brand",
        defaults={
            "name": "Brand",
            "type": AttributeType.PRODUCT_TYPE,
            "input_type": AttributeInputType.PLAIN_TEXT,
        },
    )
    return attr


@pytest.fixture
def simple_product(db):
    """Create a minimal product for testing."""
    product_type = ProductType.objects.create(
        name="Test Type",
        slug="test-type",
        has_variants=True,
    )
    product = Product.objects.create(
        name="Test Product",
        slug="test-product",
        product_type=product_type,
    )
    return product


@pytest.fixture
def product_with_variants(simple_product):
    """Create product with multiple variants."""
    product = simple_product
    # Create 3 variants for testing
    for i in range(3):
        ProductVariant.objects.create(product=product, name=f"Variant {i}", sku=None)

    return product
