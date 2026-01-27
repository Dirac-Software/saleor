"""Tests for product ingestion GraphQL mutations."""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from .....product.error_codes import ProductErrorCode

# GraphQL mutations
PRODUCT_INGESTION_UPLOAD_FILE_MUTATION = """
    mutation ProductIngestionUploadFile($file: Upload!, $sheetName: String, $headerRow: Int) {
        productIngestionUploadFile(input: {file: $file, sheetName: $sheetName, headerRow: $headerRow}) {
            fileId
            availableColumns
            rowCount
            sheetNames
            productErrors {
                field
                message
                code
            }
        }
    }
"""

PRODUCT_INGESTION_INGEST_MUTATION = """
    mutation ProductIngestionIngest($input: ProductIngestionConfigInput!) {
        productIngestionIngest(input: $input) {
            success
            createdProductsCount
            updatedProductsCount
            skippedProductsCount
            totalVariantsCreated
            totalVariantsUpdated
            warehouseName
            productErrors {
                field
                message
                code
            }
        }
    }
"""


@pytest.fixture
def excel_file():
    """Create a simple Excel file for testing."""
    # This would be replaced with an actual Excel file in real tests
    content = b"PK\x03\x04"  # Minimal ZIP header (Excel files are ZIP archives)
    return SimpleUploadedFile(
        "test.xlsx",
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def test_product_ingestion_upload_file_requires_permission(api_client, excel_file):
    """Test that upload file mutation requires MANAGE_PRODUCTS permission."""
    # given
    variables = {
        "file": None,  # GraphQL Upload scalar
        "sheetName": "Sheet1",
        "headerRow": 0,
    }

    # when
    response = api_client.post_graphql(
        PRODUCT_INGESTION_UPLOAD_FILE_MUTATION,
        variables,
    )

    # then
    assert_no_permission(response)


def test_product_ingestion_upload_file_invalid_file_type(
    staff_api_client, permission_manage_products
):
    """Test that non-Excel files are rejected."""
    # given
    staff_api_client.user.user_permissions.add(permission_manage_products)
    invalid_file = SimpleUploadedFile(
        "test.txt", b"not an excel file", content_type="text/plain"
    )

    variables = {
        "file": None,
        "sheetName": "Sheet1",
        "headerRow": 0,
    }

    # when
    with patch(
        "saleor.graphql.product.mutations.product_ingestion.product_ingestion_upload_file.save_uploaded_file"
    ):
        # Mock the file validation to actually check the file
        response = staff_api_client.post_multipart(
            PRODUCT_INGESTION_UPLOAD_FILE_MUTATION,
            {"file": invalid_file},
            variables,
        )

    # then - should fail validation
    content = response.json()
    assert content["data"]["productIngestionUploadFile"] is None


def test_product_ingestion_ingest_requires_permission(api_client):
    """Test that ingest mutation requires MANAGE_PRODUCTS permission."""
    # given
    variables = {
        "input": {
            "fileId": "test-file-id",
            "warehouseName": "Test Warehouse",
            "warehouseAddress": "123 Test St",
            "warehouseCountry": "GB",
            "columnMapping": {
                "code": "Code",
                "brand": "Brand",
                "description": "Description",
                "category": "Category",
                "sizes": "Sizes",
                "price": "Price",
            },
            "minimumOrderQuantity": 10,
            "confirmPriceInterpretation": True,
        }
    }

    # when
    response = api_client.post_graphql(
        PRODUCT_INGESTION_INGEST_MUTATION,
        variables,
    )

    # then
    assert_no_permission(response)


def test_product_ingestion_ingest_file_not_found(
    staff_api_client, permission_manage_products
):
    """Test that ingest fails if file ID is invalid."""
    # given
    staff_api_client.user.user_permissions.add(permission_manage_products)

    variables = {
        "input": {
            "fileId": "invalid-file-id",
            "warehouseName": "Test Warehouse",
            "warehouseAddress": "123 Test St",
            "warehouseCountry": "GB",
            "columnMapping": {
                "code": "Code",
                "brand": "Brand",
                "description": "Description",
                "category": "Category",
                "sizes": "Sizes",
                "price": "Price",
            },
            "minimumOrderQuantity": 10,
            "confirmPriceInterpretation": True,
        }
    }

    # when
    response = staff_api_client.post_graphql(
        PRODUCT_INGESTION_INGEST_MUTATION,
        variables,
    )

    # then
    content = response.json()
    errors = content["data"]["productIngestionIngest"]["productErrors"]
    assert len(errors) > 0
    assert errors[0]["code"] == ProductErrorCode.FILE_NOT_FOUND.name
    assert "not found or expired" in errors[0]["message"].lower()


def test_product_ingestion_ingest_invalid_moq(
    staff_api_client, permission_manage_products
):
    """Test that negative MOQ is rejected."""
    # given
    staff_api_client.user.user_permissions.add(permission_manage_products)

    variables = {
        "input": {
            "fileId": "test-file-id",
            "warehouseName": "Test Warehouse",
            "warehouseAddress": "123 Test St",
            "warehouseCountry": "GB",
            "columnMapping": {
                "code": "Code",
            },
            "minimumOrderQuantity": -1,  # Invalid
            "confirmPriceInterpretation": True,
        }
    }

    # when
    response = staff_api_client.post_graphql(
        PRODUCT_INGESTION_INGEST_MUTATION,
        variables,
    )

    # then
    content = response.json()
    errors = content["data"]["productIngestionIngest"]["productErrors"]
    assert len(errors) > 0
    assert errors[0]["code"] == ProductErrorCode.INVALID.name
    assert "minimum order quantity" in errors[0]["message"].lower()


def assert_no_permission(response):
    """Assert no permission error."""
    content = response.json()
    assert "errors" in content
    assert any("permission" in str(error).lower() for error in content["errors"])
