from unittest.mock import MagicMock, patch

import pytest
from django.core.files import File

from ....attribute.models import Attribute, AttributeProduct, AttributeValue
from ....attribute.models.product import AssignedProductAttributeValue
from ... import FileTypes
from ...utils.export import (
    ORDER_EXPORT_HEADERS,
    ORDER_LINE_HEADERS,
    ORDER_SUMMARY_HEADERS,
    _build_order_rows,
    export_orders,
)


@pytest.fixture
def product_code_attribute(db):
    attr = Attribute.objects.create(
        slug="product-code",
        name="Product Code",
        input_type="plain-text",
    )
    return attr


@pytest.fixture
def order_with_product_code(order_with_lines, product_code_attribute):
    for line in order_with_lines.lines.select_related("variant__product"):
        product = line.variant.product
        if not AttributeProduct.objects.filter(
            attribute=product_code_attribute, product_type=product.product_type
        ).exists():
            AttributeProduct.objects.create(
                attribute=product_code_attribute,
                product_type=product.product_type,
                sort_order=0,
            )
        value = AttributeValue.objects.create(
            attribute=product_code_attribute,
            name="PC-001",
            slug=f"pc-001-{product.pk}",
        )
        AssignedProductAttributeValue.objects.get_or_create(
            product=product, value=value
        )
    return order_with_lines


def test_order_line_headers_include_product_code():
    assert "Product Code" in ORDER_LINE_HEADERS
    assert "Product Code" in ORDER_EXPORT_HEADERS
    assert "Product Code" not in ORDER_SUMMARY_HEADERS


def test_build_order_rows_product_code(order_with_product_code):
    # given
    order = order_with_product_code
    slug = "product-code"

    # when
    summary, line_rows = _build_order_rows(order, slug)

    # then
    assert summary["Number"] == str(order.number)
    for row in line_rows:
        assert "Product Code" in row
        assert row["Product Code"] == "PC-001"


def test_build_order_rows_no_variant(order_with_lines):
    # given — detach variants so variant_id is null
    order_with_lines.lines.update(variant=None)
    order_with_lines.refresh_from_db()

    # when
    summary, line_rows = _build_order_rows(order_with_lines, "product-code")

    # then
    for row in line_rows:
        assert row["Product Code"] == ""


def test_build_order_rows_missing_attribute(order_with_lines):
    # given — no product-code attribute assigned to products
    _, line_rows = _build_order_rows(order_with_lines, "product-code")

    # then
    for row in line_rows:
        assert row["Product Code"] == ""


@patch("saleor.csv.utils.export.send_export_download_link_notification")
@patch("saleor.csv.utils.export.save_csv_file_in_export_file")
def test_export_orders_uses_site_setting_slug(
    save_file_mock,
    send_email_mock,
    order_with_product_code,
    user_export_file,
    site_settings,
):
    # given
    site_settings.invoice_product_code_attribute = "product-code"
    site_settings.save(update_fields=["invoice_product_code_attribute"])

    mock_file = MagicMock(spec=File)
    save_file_mock.return_value = mock_file

    # when
    export_orders(user_export_file, {"all": ""}, FileTypes.CSV)

    # then — no errors and save was called
    save_file_mock.assert_called_once()


@patch("saleor.csv.utils.export.send_export_download_link_notification")
@patch("saleor.csv.utils.export.save_csv_file_in_export_file")
def test_export_orders_xlsx_uses_site_setting_slug(
    save_file_mock,
    send_email_mock,
    order_with_product_code,
    user_export_file,
    site_settings,
):
    # given
    site_settings.invoice_product_code_attribute = "product-code"
    site_settings.save(update_fields=["invoice_product_code_attribute"])

    mock_file = MagicMock(spec=File)
    save_file_mock.return_value = mock_file

    # when
    export_orders(user_export_file, {"all": ""}, FileTypes.XLSX)

    # then
    save_file_mock.assert_called_once()
