"""Product ingestion mutation."""

import logging

import graphene
from django.core.exceptions import ValidationError

from .....permission.enums import ProductPermissions
from .....product.error_codes import ProductErrorCode
from .....product.ingestion import (
    CurrencyIncompatible,
    DuplicateProductNames,
    DuplicateProducts,
    IngestConfig,
    MissingDatabaseSetup,
    MissingRequiredFields,
    SheetIntegrityError,
    SizeQtyUnparseable,
    SpreadsheetColumnMapping,
    ingest_products_from_excel,
)
from .....product.ingestion_file_storage import get_ingestion_file
from ....core import ResolveInfo
from ....core.doc_category import DOC_CATEGORY_PRODUCTS
from ....core.mutations import BaseMutation
from ....core.types import BaseInputObjectType, ProductError

logger = logging.getLogger(__name__)


class SpreadsheetColumnMappingInput(BaseInputObjectType):
    code = graphene.String(
        required=False,
        description="Column name containing product codes.",
    )
    brand = graphene.String(
        required=False,
        description="Column name containing brand names.",
    )
    description = graphene.String(
        required=False,
        description="Column name containing product descriptions/names.",
    )
    category = graphene.String(
        required=False,
        description="Column name containing category names.",
    )
    sizes = graphene.String(
        required=False,
        description="Column name containing sizes with quantities (e.g., '8[5], 9[3]').",
    )
    rrp = graphene.String(
        required=False,
        description="Column name containing recommended retail prices.",
    )
    price = graphene.String(
        required=False,
        description="Column name containing sale prices (excluding VAT).",
    )
    weight = graphene.String(
        required=False,
        description="Column name containing product weights (in grams).",
    )
    image = graphene.String(
        required=False,
        description="Column name containing image URLs.",
    )

    class Meta:
        doc_category = DOC_CATEGORY_PRODUCTS


class StockUpdateModeEnum(graphene.Enum):
    REPLACE = "replace"
    ADD = "add"

    class Meta:
        description = "Stock update mode for existing products."


class ProductIngestionConfigInput(BaseInputObjectType):
    file_id = graphene.ID(
        required=True,
        description="File ID from ProductIngestionFileUpload mutation.",
    )
    warehouse_name = graphene.String(
        required=True,
        description="Name of the warehouse to ingest products into.",
    )
    warehouse_address = graphene.String(
        required=True,
        description="Full address of the warehouse.",
    )
    warehouse_country = graphene.String(
        required=True,
        description="ISO2 country code for the warehouse (e.g., 'GB', 'AE', 'US').",
    )
    sheet_name = graphene.String(
        required=False,
        default_value="Sheet1",
        description="Name of the sheet to read.",
    )
    header_row = graphene.Int(
        required=False,
        default_value=0,
        description="Row number containing column headers (0-indexed).",
    )
    column_mapping = graphene.Field(
        SpreadsheetColumnMappingInput,
        required=True,
        description="Mapping of Excel columns to product fields.",
    )
    not_for_web = graphene.Boolean(
        required=False,
        default_value=False,
        description="If true, products won't be visible on storefront (wholesale/internal only).",
    )
    default_currency = graphene.String(
        required=False,
        description="Default currency if prices don't have currency symbols.",
    )
    dry_run = graphene.Boolean(
        required=False,
        default_value=False,
        description="If true, validate and process but don't save changes.",
    )
    error_on_duplicates_in_sheet = graphene.Boolean(
        required=False,
        default_value=False,
        description="If true, raise error on duplicate products in Excel. If false, merge duplicates.",
    )
    stock_update_mode = graphene.Field(
        StockUpdateModeEnum,
        required=False,
        description="How to handle stock for existing products: REPLACE (overwrite) or ADD (increment).",
    )
    minimum_order_quantity = graphene.Int(
        required=True,
        description="Minimum order quantity to assign to all products.",
    )
    confirm_price_interpretation = graphene.Boolean(
        required=True,
        description="Confirmation that 'Price' column contains sale price excluding VAT.",
    )

    class Meta:
        doc_category = DOC_CATEGORY_PRODUCTS


class ProductIngestionIngest(BaseMutation):
    success = graphene.Field(
        graphene.Boolean,
        description="Whether the ingestion completed successfully.",
    )
    created_products_count = graphene.Field(
        graphene.Int,
        description="Number of products created.",
    )
    updated_products_count = graphene.Field(
        graphene.Int,
        description="Number of products updated.",
    )
    skipped_products_count = graphene.Field(
        graphene.Int,
        description="Number of products skipped (exist in other warehouses).",
    )
    total_variants_created = graphene.Field(
        graphene.Int,
        description="Total number of product variants created.",
    )
    total_variants_updated = graphene.Field(
        graphene.Int,
        description="Total number of product variants updated.",
    )
    warehouse_name = graphene.Field(
        graphene.String,
        description="Name of the warehouse products were ingested into.",
    )

    class Arguments:
        input = ProductIngestionConfigInput(
            required=True,
            description="Configuration for product ingestion.",
        )

    class Meta:
        description = (
            "Ingest products from uploaded Excel file. "
            "Creates new products and variants, updates existing ones. "
            "Handles stock, pricing, attributes, and channel listings."
        )
        doc_category = DOC_CATEGORY_PRODUCTS
        permissions = (ProductPermissions.MANAGE_PRODUCTS,)
        error_type_class = ProductError
        error_type_field = "product_errors"

    @classmethod
    def validate_input(cls, input_data):
        """Validate ingestion configuration."""
        # Validate minimum order quantity
        moq = input_data.get("minimum_order_quantity")
        if moq is not None and moq < 1:
            raise ValidationError(
                {
                    "minimum_order_quantity": ValidationError(
                        "Minimum order quantity must be at least 1.",
                        code=ProductErrorCode.INVALID.value,
                    )
                }
            )

        # Validate country code (basic check)
        country = input_data.get("warehouse_country", "")
        if len(country) != 2:
            raise ValidationError(
                {
                    "warehouse_country": ValidationError(
                        "Country code must be ISO2 format (2 characters, e.g., 'GB').",
                        code=ProductErrorCode.INVALID.value,
                    )
                }
            )

    @classmethod
    def perform_mutation(cls, _root, info: ResolveInfo, /, **data):
        input_data = data["input"]

        # Validate input
        cls.validate_input(input_data)

        # Retrieve file from cache
        file_id = input_data["file_id"]
        file_path = get_ingestion_file(file_id)

        if not file_path:
            raise ValidationError(
                {
                    "file_id": ValidationError(
                        "File not found or expired. Please upload the file again.",
                        code=ProductErrorCode.FILE_NOT_FOUND.value,
                    )
                }
            )

        # Build column mapping
        column_mapping_data = input_data.get("column_mapping", {})
        column_mapping = SpreadsheetColumnMapping(
            code=column_mapping_data.get("code"),
            brand=column_mapping_data.get("brand"),
            description=column_mapping_data.get("description"),
            category=column_mapping_data.get("category"),
            sizes=column_mapping_data.get("sizes"),
            rrp=column_mapping_data.get("rrp"),
            price=column_mapping_data.get("price"),
            weight=column_mapping_data.get("weight"),
            image=column_mapping_data.get("image"),
        )

        # Build config
        config = IngestConfig(
            warehouse_name=input_data["warehouse_name"],
            warehouse_address=input_data["warehouse_address"],
            warehouse_country=input_data["warehouse_country"].upper(),
            sheet_name=input_data.get("sheet_name", "Sheet1"),
            header_row=input_data.get("header_row", 0),
            column_mapping=column_mapping,
            not_for_web=input_data.get("not_for_web", False),
            default_currency=input_data.get("default_currency"),
            dry_run=input_data.get("dry_run", False),
            error_on_duplicates_in_sheet=input_data.get(
                "error_on_duplicates_in_sheet", False
            ),
            stock_update_mode=input_data.get("stock_update_mode"),
            minimum_order_quantity=input_data["minimum_order_quantity"],
            confirm_price_interpretation=input_data["confirm_price_interpretation"],
        )

        try:
            # Run ingestion
            result = ingest_products_from_excel(config, file_path)

            logger.info(
                "Product ingestion completed: %d created, %d updated, %d skipped",
                len(result.created_products),
                len(result.updated_products),
                result.skipped_products,
            )

            return ProductIngestionIngest(
                success=True,
                created_products_count=len(result.created_products),
                updated_products_count=len(result.updated_products),
                skipped_products_count=result.skipped_products,
                total_variants_created=result.total_variants_created,
                total_variants_updated=result.total_variants_updated,
                warehouse_name=result.warehouse.name,
            )

        except SheetIntegrityError as e:
            logger.error("Sheet integrity error: %s", str(e))
            raise ValidationError(
                {
                    "input": ValidationError(
                        str(e),
                        code=ProductErrorCode.SHEET_INTEGRITY_ERROR.value,
                    )
                }
            ) from e
        except SizeQtyUnparseable as e:
            logger.error("Size/Quantity parsing error: %s", str(e))
            raise ValidationError(
                {
                    "column_mapping": ValidationError(
                        str(e),
                        code=ProductErrorCode.SIZE_QTY_UNPARSEABLE.value,
                    )
                }
            ) from e
        except CurrencyIncompatible as e:
            logger.error("Currency compatibility error: %s", str(e))
            raise ValidationError(
                {
                    "input": ValidationError(
                        str(e),
                        code=ProductErrorCode.CURRENCY_INCOMPATIBLE.value,
                    )
                }
            ) from e
        except DuplicateProducts as e:
            logger.error("Duplicate products error: %s", str(e))
            raise ValidationError(
                {
                    "error_on_duplicates_in_sheet": ValidationError(
                        str(e),
                        code=ProductErrorCode.DUPLICATE_PRODUCTS.value,
                    )
                }
            ) from e
        except DuplicateProductNames as e:
            logger.error("Duplicate product names error: %s", str(e))
            raise ValidationError(
                {
                    "input": ValidationError(
                        str(e),
                        code=ProductErrorCode.DUPLICATE_PRODUCT_NAMES.value,
                    )
                }
            ) from e
        except MissingDatabaseSetup as e:
            logger.error("Missing database setup: %s", str(e))
            raise ValidationError(
                {
                    "input": ValidationError(
                        str(e),
                        code=ProductErrorCode.MISSING_DATABASE_SETUP.value,
                    )
                }
            ) from e
        except MissingRequiredFields as e:
            logger.error("Missing required fields: %s", str(e))
            raise ValidationError(
                {
                    "column_mapping": ValidationError(
                        str(e),
                        code=ProductErrorCode.MISSING_REQUIRED_FIELDS.value,
                    )
                }
            ) from e
        except Exception as e:
            logger.exception("Unexpected error during ingestion: %s", str(e))
            raise ValidationError(
                {
                    "input": ValidationError(
                        f"Unexpected error during ingestion: {str(e)}",
                        code=ProductErrorCode.GRAPHQL_ERROR.value,
                    )
                }
            ) from e
