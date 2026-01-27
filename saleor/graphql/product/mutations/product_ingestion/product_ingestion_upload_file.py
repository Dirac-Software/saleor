"""Product ingestion file upload mutation."""

import logging

import graphene
from django.core.exceptions import ValidationError

from .....permission.enums import ProductPermissions
from .....product.error_codes import ProductErrorCode
from .....product.ingestion import read_excel_with_validation
from .....product.ingestion_file_storage import save_uploaded_file
from ....core import ResolveInfo
from ....core.doc_category import DOC_CATEGORY_PRODUCTS
from ....core.mutations import BaseMutation
from ....core.types import BaseInputObjectType, ProductError, Upload

logger = logging.getLogger(__name__)


class ProductIngestionFileUploadInput(BaseInputObjectType):
    file = Upload(
        required=True,
        description="Excel file to upload for product ingestion (.xlsx or .xls).",
    )
    sheet_name = graphene.String(
        required=False,
        default_value="Sheet1",
        description="Name of the sheet to read from the Excel file.",
    )
    header_row = graphene.Int(
        required=False,
        default_value=0,
        description="Row number containing column headers (0-indexed).",
    )

    class Meta:
        doc_category = DOC_CATEGORY_PRODUCTS


class ProductIngestionFileUpload(BaseMutation):
    file_id = graphene.Field(
        graphene.ID,
        description="Unique identifier for the uploaded file. Use this in ProductIngestionIngest.",
    )
    available_columns = graphene.List(
        graphene.String,
        description="List of column names found in the Excel sheet.",
    )
    row_count = graphene.Field(
        graphene.Int,
        description="Number of data rows in the sheet (excluding header).",
    )
    sheet_names = graphene.List(
        graphene.String,
        description="List of all sheet names in the Excel file.",
    )

    class Arguments:
        input = ProductIngestionFileUploadInput(
            required=True,
            description="Fields required to upload Excel file for analysis.",
        )

    class Meta:
        description = (
            "Upload and analyze an Excel file for product ingestion. "
            "Returns file ID and available columns for mapping configuration."
        )
        doc_category = DOC_CATEGORY_PRODUCTS
        permissions = (ProductPermissions.MANAGE_PRODUCTS,)
        error_type_class = ProductError
        error_type_field = "product_errors"

    @classmethod
    def validate_file(cls, file):
        """Validate that uploaded file is an Excel file."""
        if not file:
            raise ValidationError(
                {
                    "file": ValidationError(
                        "No file provided.",
                        code=ProductErrorCode.REQUIRED.value,
                    )
                }
            )

        # Get filename - handle both string and file object
        if isinstance(file, str):
            filename = file.lower()
        elif hasattr(file, "name"):
            filename = file.name.lower()
        else:
            # If we can't determine filename, let it through and fail later with better error
            return

        # Check file extension
        if not filename.endswith((".xlsx", ".xls")):
            raise ValidationError(
                {
                    "file": ValidationError(
                        "File must be an Excel file (.xlsx or .xls).",
                        code=ProductErrorCode.INVALID.value,
                    )
                }
            )

    @classmethod
    def perform_mutation(cls, _root, info: ResolveInfo, /, **data):
        input_data = data["input"]
        file_ref = input_data["file"]
        sheet_name = input_data.get("sheet_name", "Sheet1")
        header_row = input_data.get("header_row", 0)

        # Get actual file from request context (GraphQL multipart upload)
        if isinstance(file_ref, str):
            file = info.context.FILES.get(file_ref)
            if not file:
                raise ValidationError(
                    {
                        "file": ValidationError(
                            "File not found in request.",
                            code=ProductErrorCode.REQUIRED.value,
                        )
                    }
                )
        else:
            file = file_ref

        # Validate file
        cls.validate_file(file)

        try:
            # Save file temporarily
            file_id, file_path = save_uploaded_file(file)

            # Get filename for logging
            filename = file.name if hasattr(file, "name") else str(file)
            logger.info(
                "Uploaded file %s saved as %s (path: %s)", filename, file_id, file_path
            )

            # Check file exists and has size
            import os

            if not os.path.exists(file_path):
                raise FileNotFoundError(f"Saved file not found at {file_path}")

            file_size = os.path.getsize(file_path)
            logger.info("File size: %d bytes", file_size)

            if file_size == 0:
                raise ValueError("Uploaded file is empty (0 bytes)")

            # Analyze the file using pandas
            import pandas as pd

            # First, get all sheet names
            excel_file = pd.ExcelFile(file_path)
            logger.info("Available sheets: %s", excel_file.sheet_names)

            # If sheet_name not in available sheets, use first sheet
            if sheet_name not in excel_file.sheet_names:
                logger.warning(
                    "Sheet '%s' not found. Available sheets: %s. Using first sheet: %s",
                    sheet_name,
                    excel_file.sheet_names,
                    excel_file.sheet_names[0] if excel_file.sheet_names else "NONE",
                )
                if not excel_file.sheet_names:
                    raise ValueError("Excel file contains no sheets")
                sheet_name = excel_file.sheet_names[0]

            # Now read the data
            df = read_excel_with_validation(file_path, sheet_name, header_row)
            logger.info(
                "Read sheet '%s': %d rows, %d columns. Columns: %s",
                sheet_name,
                len(df),
                len(df.columns),
                list(df.columns)[:10],  # First 10 columns
            )

            # Convert columns to strings (pandas can use ints for unnamed columns)
            available_columns = [str(col) for col in df.columns]

            return ProductIngestionFileUpload(
                file_id=file_id,
                available_columns=available_columns,
                row_count=len(df),
                sheet_names=excel_file.sheet_names,
            )

        except FileNotFoundError as e:
            raise ValidationError(
                {
                    "file": ValidationError(
                        f"Sheet '{sheet_name}' not found in Excel file. {str(e)}",
                        code=ProductErrorCode.NOT_FOUND.value,
                    )
                }
            ) from e
        except Exception as e:
            logger.exception("Error processing Excel file: %s", str(e))
            raise ValidationError(
                {
                    "file": ValidationError(
                        f"Failed to process Excel file: {str(e)}",
                        code=ProductErrorCode.INVALID.value,
                    )
                }
            ) from e
