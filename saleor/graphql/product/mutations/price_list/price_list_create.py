"""PriceList create mutation."""

import logging
from typing import cast

import graphene
from django.core.exceptions import ValidationError

from .....channel.models import Channel
from .....permission.enums import ProductPermissions
from .....product.error_codes import ProductErrorCode
from .....product.models import PriceList
from .....warehouse.models import Warehouse
from ....core import ResolveInfo
from ....core.doc_category import DOC_CATEGORY_PRODUCTS
from ....core.mutations import BaseMutation
from ....core.types import BaseInputObjectType, ProductError, Upload

logger = logging.getLogger(__name__)

VALID_FIELD_NAMES = {
    "product_code",
    "brand",
    "description",
    "category",
    "sizes",
    "rrp",
    "sell_price",
    "weight_kg",
    "image_url",
    "buy_price",
    "hs_code",
}


class PriceListColumnMapInput(BaseInputObjectType):
    """Maps integer column indices (0-based) to price list item fields."""

    product_code = graphene.Int(description="Column index for product code.")
    brand = graphene.Int(description="Column index for brand.")
    description = graphene.Int(description="Column index for description.")
    category = graphene.Int(description="Column index for category.")
    sizes = graphene.Int(
        description="Column index for sizes with quantities (e.g. 'XS[20], M[50]')."
    )
    rrp = graphene.Int(description="Column index for RRP.")
    sell_price = graphene.Int(
        description="Column index for sell price (the price shown on the web)."
    )
    weight_kg = graphene.Int(description="Column index for weight in kg.")
    image_url = graphene.Int(description="Column index for image URL.")
    buy_price = graphene.Int(description="Column index for buy/cost price.")
    hs_code = graphene.Int(description="Column index for HS code (UK).")

    class Meta:
        doc_category = DOC_CATEGORY_PRODUCTS


class PriceListCreateInput(BaseInputObjectType):
    warehouse_id = graphene.ID(
        required=True,
        description="Warehouse this price list belongs to.",
    )
    name = graphene.String(
        description="Human-readable name for the price list.",
    )
    channel_ids = graphene.List(
        graphene.NonNull(graphene.ID),
        description="Channels this price list should be active in. At least one required.",
    )
    file = Upload(
        description="Excel file (.xlsx or .xls) containing the price list.",
    )
    sheet_name = graphene.String(
        default_value="Sheet1",
        description="Name of the sheet to read from the Excel file.",
    )
    header_row = graphene.Int(
        default_value=0,
        description="Row number containing column headers (0-indexed).",
    )
    column_map = graphene.Field(
        PriceListColumnMapInput,
        required=True,
        description="Maps column indices to price list item fields.",
    )
    default_currency = graphene.String(
        required=True,
        description="Currency code for all prices in this sheet (e.g. 'GBP').",
    )
    google_drive_url = graphene.String(
        description="Google Drive URL for the source sheet. Used for tracking only.",
    )

    class Meta:
        doc_category = DOC_CATEGORY_PRODUCTS


class PriceListCreate(BaseMutation):
    price_list = graphene.Field(
        "saleor.graphql.product.types.price_list.PriceList",
        description="The created price list.",
    )

    class Arguments:
        input = PriceListCreateInput(required=True)

    class Meta:
        description = (
            "Create a price list from an Excel file and queue it for processing. "
            "The file is saved to storage and parsed asynchronously. "
            "Check processing_completed_at / processing_failed_at for status."
        )
        doc_category = DOC_CATEGORY_PRODUCTS
        permissions = (ProductPermissions.MANAGE_PRODUCTS,)
        error_type_class = ProductError
        error_type_field = "product_errors"

    @classmethod
    def perform_mutation(cls, _root, info: ResolveInfo, /, **data):
        inp = data["input"]

        errors = {}

        channel_ids = inp.get("channel_ids") or []
        if not channel_ids:
            errors["channel_ids"] = ValidationError(
                "At least one channel is required.",
                code=ProductErrorCode.REQUIRED.value,
            )

        default_currency = inp.get("default_currency") or ""
        if not default_currency:
            errors["default_currency"] = ValidationError(
                "Currency is required.",
                code=ProductErrorCode.REQUIRED.value,
            )

        channels: list[Channel] = []
        if channel_ids and default_currency and "channel_ids" not in errors:
            channels = [
                cast(
                    Channel,
                    cls.get_node_or_error(
                        info, cid, field="channel_ids", only_type="Channel"
                    ),
                )
                for cid in channel_ids
            ]
            mismatched_channels = [
                ch for ch in channels if ch.currency_code != default_currency
            ]
            if mismatched_channels:
                errors["channel_ids"] = ValidationError(
                    f"All channels must use the price list currency ({default_currency}).",
                    code=ProductErrorCode.INVALID.value,
                )

        file_ref = inp.get("file")
        file = (
            info.context.FILES.get(file_ref) if isinstance(file_ref, str) else file_ref
        )
        if not file:
            errors["file"] = ValidationError(
                "No file provided.",
                code=ProductErrorCode.REQUIRED.value,
            )
        elif not cast(str, file.name).lower().endswith((".xlsx", ".xls")):
            errors["file"] = ValidationError(
                "File must be an Excel file (.xlsx or .xls).",
                code=ProductErrorCode.INVALID.value,
            )

        if errors:
            raise ValidationError(errors)

        warehouse = cast(
            Warehouse,
            cls.get_node_or_error(
                info, inp["warehouse_id"], field="warehouse_id", only_type="Warehouse"
            ),
        )

        column_map_input = inp.get("column_map") or {}
        provided_indices = [idx for idx in column_map_input.values() if idx is not None]
        if len(provided_indices) != len(set(provided_indices)):
            raise ValidationError(
                {
                    "column_map": ValidationError(
                        "Each column index must be unique; duplicate indices found.",
                        code=ProductErrorCode.INVALID.value,
                    )
                }
            )
        column_map = {
            str(idx): field
            for field, idx in column_map_input.items()
            if idx is not None
        }

        price_list = PriceList.objects.create(
            warehouse=warehouse,
            name=inp.get("name") or "",
            excel_file=file,
            google_drive_url=inp.get("google_drive_url") or "",
            config={
                "sheet_name": inp.get("sheet_name", "Sheet1"),
                "header_row": inp.get("header_row", 0),
                "column_map": column_map,
                "default_currency": default_currency,
            },
        )

        price_list.channels.set(channels)
        price_list.process()

        return PriceListCreate(price_list=price_list)
