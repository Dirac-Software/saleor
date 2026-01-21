import datetime
import logging
import uuid
from tempfile import NamedTemporaryFile
from typing import IO, TYPE_CHECKING, Any

import petl as etl
from django.conf import settings
from django.utils import timezone

from ...core.db.connection import allow_writer
from ...core.utils.batches import queryset_in_batches
from ...discount.models import VoucherCode
from ...giftcard.models import GiftCard
from ...product.models import Product
from .. import FileTypes
from ..notifications import send_export_download_link_notification
from .image_embedding import embed_images_in_excel
from .product_headers import get_product_export_fields_and_headers_info
from .products_data import get_products_data
from .variant_compression import get_products_data_compressed

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from ..models import ExportFile


BATCH_SIZE = 1000


def export_products(
    export_file: "ExportFile",
    scope: dict[str, str | dict],
    export_info: dict[str, list],
    file_type: str,
    delimiter: str = ",",
):
    from ...graphql.product.filters.product import ProductFilter

    file_name = get_filename("product", file_type)
    queryset = get_queryset(Product, ProductFilter, scope)

    (
        export_fields,
        file_headers,
        data_headers,
    ) = get_product_export_fields_and_headers_info(export_info)

    temporary_file = create_file_with_headers(file_headers, delimiter, file_type)

    # Check if we should compress variants
    compress_variants = export_info.get("compress_variants", False)

    if compress_variants:
        # Export with compressed variants (one row per product)
        export_products_compressed(
            queryset,
            export_info,
            data_headers,
            delimiter,
            temporary_file,
            file_type,
        )
    else:
        # Export with expanded variants (one row per variant)
        export_products_in_batches(
            queryset,
            export_info,
            set(export_fields),
            data_headers,
            delimiter,
            temporary_file,
            file_type,
        )

    # Embed images in Excel file if requested
    embed_images = export_info.get("embed_images", False)
    logger.info(
        "Image embedding: embed_images=%s, file_type=%s, file_headers=%s",
        embed_images,
        file_type,
        file_headers,
    )

    if embed_images and file_type == FileTypes.XLSX:
        # Identify which columns contain images
        image_columns = []
        if "product media" in file_headers:
            image_columns.append("product media")
        # Only include variant media if NOT compressing variants
        # (variant media doesn't make sense when there's one row per product)
        if "variant media" in file_headers and not compress_variants:
            image_columns.append("variant media")

        logger.info("Image columns to embed: %s", image_columns)

        # Embed images if there are any image columns
        if image_columns:
            logger.info("Starting image embedding for %s", temporary_file.name)
            embed_images_in_excel(temporary_file.name, image_columns)
            logger.info("Image embedding completed")
        else:
            logger.warning("No image columns found to embed")

    save_csv_file_in_export_file(export_file, temporary_file, file_name)
    temporary_file.close()
    send_export_download_link_notification(export_file, "products")


def export_gift_cards(
    export_file: "ExportFile",
    scope: dict[str, str | dict],
    file_type: str,
    delimiter: str = ",",
):
    from ...graphql.giftcard.filters import GiftCardFilter

    file_name = get_filename("gift_card", file_type)

    queryset = get_queryset(GiftCard, GiftCardFilter, scope)
    # only unused gift cards codes can be exported
    queryset = queryset.filter(used_by_email__isnull=True)

    export_fields = ["code"]
    temporary_file = create_file_with_headers(export_fields, delimiter, file_type)

    export_gift_cards_in_batches(
        queryset,
        export_fields,
        delimiter,
        temporary_file,
        file_type,
    )

    save_csv_file_in_export_file(export_file, temporary_file, file_name)
    temporary_file.close()
    send_export_download_link_notification(export_file, "gift cards")


def export_voucher_codes(
    export_file: "ExportFile",
    file_type: str,
    voucher_id: int | None = None,
    ids: list[int] | None = None,
    delimiter: str = ",",
):
    file_name = get_filename("voucher_code", file_type)

    qs = VoucherCode.objects.using(settings.DATABASE_CONNECTION_REPLICA_NAME).all()
    if voucher_id:
        qs = VoucherCode.objects.using(
            settings.DATABASE_CONNECTION_REPLICA_NAME
        ).filter(voucher_id=voucher_id)
    if ids:
        qs = VoucherCode.objects.using(
            settings.DATABASE_CONNECTION_REPLICA_NAME
        ).filter(id__in=ids)

    export_fields = ["code"]
    temporary_file = create_file_with_headers(export_fields, delimiter, file_type)

    export_voucher_codes_in_batches(
        qs,
        export_fields,
        delimiter,
        temporary_file,
        file_type,
    )

    save_csv_file_in_export_file(export_file, temporary_file, file_name)
    temporary_file.close()
    send_export_download_link_notification(export_file, "voucher codes")


def get_filename(model_name: str, file_type: str) -> str:
    hash = uuid.uuid4()
    return "{}_data_{}_{}.{}".format(
        model_name, timezone.now().strftime("%d_%m_%Y_%H_%M_%S"), hash, file_type
    )


def get_queryset(model, filter, scope: dict[str, str | dict]) -> "QuerySet":
    queryset = model.objects.using(settings.DATABASE_CONNECTION_REPLICA_NAME).all()
    if "ids" in scope:
        queryset = model.objects.using(
            settings.DATABASE_CONNECTION_REPLICA_NAME
        ).filter(pk__in=scope["ids"])
    elif "filter" in scope:
        queryset = filter(data=parse_input(scope["filter"]), queryset=queryset).qs

    queryset = queryset.order_by("pk")

    return queryset


def parse_input(data: Any) -> dict[str, str | dict]:
    """Parse input into correct data types.

    Scope coming from Celery will be passed as strings.
    """
    if "attributes" in data:
        serialized_attributes = []

        for attr in data.get("attributes") or []:
            if "date_time" in attr:
                if gte := attr["date_time"].get("gte"):
                    attr["date_time"]["gte"] = datetime.datetime.fromisoformat(gte)
                if lte := attr["date_time"].get("lte"):
                    attr["date_time"]["lte"] = datetime.datetime.fromisoformat(lte)

            if "date" in attr:
                if gte := attr["date"].get("gte"):
                    attr["date"]["gte"] = datetime.date.fromisoformat(gte)
                if lte := attr["date"].get("lte"):
                    attr["date"]["lte"] = datetime.date.fromisoformat(lte)

            serialized_attributes.append(attr)

        if serialized_attributes:
            data["attributes"] = serialized_attributes

    return data


def create_file_with_headers(file_headers: list[str], delimiter: str, file_type: str):
    table = etl.wrap([file_headers])

    if file_type == FileTypes.CSV:
        temp_file = NamedTemporaryFile("ab+", suffix=".csv")
        etl.tocsv(table, temp_file.name, delimiter=delimiter)
    else:
        temp_file = NamedTemporaryFile("ab+", suffix=".xlsx")
        etl.io.xlsx.toxlsx(table, temp_file.name)

    return temp_file


def export_products_in_batches(
    queryset: "QuerySet",
    export_info: dict[str, list],
    export_fields: set[str],
    headers: list[str],
    delimiter: str,
    temporary_file: Any,
    file_type: str,
):
    warehouses = export_info.get("warehouses")
    attributes = export_info.get("attributes")
    channels = export_info.get("channels")

    for batch_pks in queryset_in_batches(queryset, BATCH_SIZE):
        product_batch = (
            Product.objects.using(settings.DATABASE_CONNECTION_REPLICA_NAME)
            .filter(pk__in=batch_pks)
            .prefetch_related(
                "attributevalues",
                "variants",
                "collections",
                "media",
                "product_type",
                "category",
            )
        )
        export_data = get_products_data(
            product_batch, export_fields, attributes, warehouses, channels
        )

        append_to_file(export_data, headers, temporary_file, file_type, delimiter)


def export_products_compressed(
    queryset: "QuerySet",
    export_info: dict[str, list],
    headers: list[str],
    delimiter: str,
    temporary_file: Any,
    file_type: str,
):
    """Export products with compressed variants (one row per product)."""
    from collections import ChainMap

    from . import ProductExportFields

    warehouses = export_info.get("warehouses")
    attributes = export_info.get("attributes")
    channels = export_info.get("channels")
    requested_fields = export_info.get("fields", [])

    # Build export_fields set from requested fields
    fields_mapping = dict(
        ChainMap(*reversed(ProductExportFields.HEADERS_TO_FIELDS_MAPPING.values()))
    )
    export_fields_set = set()
    if requested_fields:
        for field in requested_fields:
            if field in fields_mapping:
                export_fields_set.add(fields_mapping[field])

    for batch_pks in queryset_in_batches(queryset, BATCH_SIZE):
        product_batch = (
            Product.objects.using(settings.DATABASE_CONNECTION_REPLICA_NAME)
            .filter(pk__in=batch_pks)
            .prefetch_related(
                "attributevalues",
                "variants",
                "collections",
                "media",
                "product_type",
                "category",
            )
        )

        # Use compressed data function
        export_data = get_products_data_compressed(
            product_batch,
            export_fields_set,  # Pass proper export fields
            attributes,
            warehouses,
            channels,
            requested_fields,
        )

        append_to_file(export_data, headers, temporary_file, file_type, delimiter)


def export_gift_cards_in_batches(
    queryset: "QuerySet",
    export_fields: list[str],
    delimiter: str,
    temporary_file: Any,
    file_type: str,
):
    for batch_pks in queryset_in_batches(queryset, BATCH_SIZE):
        gift_card_batch = GiftCard.objects.using(
            settings.DATABASE_CONNECTION_REPLICA_NAME
        ).filter(pk__in=batch_pks)

        export_data = list(gift_card_batch.values(*export_fields))

        append_to_file(export_data, export_fields, temporary_file, file_type, delimiter)


def export_voucher_codes_in_batches(
    queryset: "QuerySet",
    export_fields: list[str],
    delimiter: str,
    temporary_file: Any,
    file_type: str,
):
    for batch_pks in queryset_in_batches(queryset, BATCH_SIZE):
        voucher_codes_batch = VoucherCode.objects.using(
            settings.DATABASE_CONNECTION_REPLICA_NAME
        ).filter(pk__in=batch_pks)

        export_data = list(voucher_codes_batch.values(*export_fields))

        append_to_file(export_data, export_fields, temporary_file, file_type, delimiter)


def append_to_file(
    export_data: list[dict[str, str | bool]],
    headers: list[str],
    temporary_file: Any,
    file_type: str,
    delimiter: str,
):
    table = etl.fromdicts(export_data, header=headers, missing="")

    if file_type == FileTypes.CSV:
        etl.io.csv.appendcsv(table, temporary_file.name, delimiter=delimiter)
    else:
        etl.io.xlsx.appendxlsx(table, temporary_file.name)


@allow_writer()
def save_csv_file_in_export_file(
    export_file: "ExportFile", temporary_file: IO[bytes], file_name: str
):
    export_file.content_file.save(file_name, temporary_file)
