"""Utilities for compressing product variants in exports."""

import uuid
from collections import defaultdict

from django.conf import settings
from django.db.models import Avg, Sum

from ...product.models import ProductVariant, ProductVariantChannelListing


def compress_variants_data(
    queryset,
    warehouse_ids: list[str] | None = None,
    size_attribute_slug: str = "size",
) -> tuple[dict[int, str], dict[int, int]]:
    """Compress all variants for each product into Size[Quantity] format.

    Returns a tuple of:
    - dict mapping product_pk -> compressed string like "6[5], 7[10], 8[15]"
    - dict mapping product_pk -> total quantity

    Args:
        queryset: Product queryset
        warehouse_ids: List of warehouse IDs to filter stocks (None = all warehouses)
        size_attribute_slug: The slug of the size attribute (default: "size")

    Returns:
        Tuple of (compressed_variants_dict, total_quantities_dict)

    """
    result: dict[int, str] = {}
    total_quantities: dict[int, int] = {}

    # Force evaluate the queryset to avoid cross-database subquery issues
    product_ids = list(queryset.values_list("id", flat=True))

    if not product_ids:
        return result, total_quantities

    # Get all variants for products in queryset (using replica DB)
    variants = (
        ProductVariant.objects.using(settings.DATABASE_CONNECTION_REPLICA_NAME)
        .filter(product_id__in=product_ids)
        .select_related("product")
    )

    # Get variant IDs
    variant_ids = list(variants.values_list("id", flat=True))

    if not variant_ids:
        return result, total_quantities

    # Get size attribute values for all variants
    # Try common variations of "size" attribute slug
    size_slugs = [size_attribute_slug, "Size", "SIZE", "size"]

    from ...attribute.models import AssignedVariantAttribute

    variant_sizes = {}
    for slug in size_slugs:
        assigned_attrs = (
            AssignedVariantAttribute.objects.using(
                settings.DATABASE_CONNECTION_REPLICA_NAME
            )
            .filter(
                variant_id__in=variant_ids,
                assignment__attribute__slug=slug,
            )
            .select_related("variant")
            .prefetch_related("values")
        )

        for assigned_attr in assigned_attrs:
            variant_id = assigned_attr.variant_id
            # Get the first value (usually only one for size)
            values = assigned_attr.values.all()
            if values:
                # Try to get the value name or slug
                size_value = values[0].name or values[0].slug or str(values[0].value)
                variant_sizes[variant_id] = size_value

        # If we found sizes, break
        if variant_sizes:
            break

    # Get stock quantities for all variants
    from ...warehouse.models import Stock

    stock_query = Stock.objects.using(settings.DATABASE_CONNECTION_REPLICA_NAME).filter(
        product_variant_id__in=variant_ids
    )

    # Filter by warehouse if specified
    if warehouse_ids:
        # Convert warehouse IDs to proper type (UUID or int)
        converted_ids: list[uuid.UUID | int] = []
        for wh_id in warehouse_ids:
            if isinstance(wh_id, str):
                # Try to convert to UUID, fall back to int
                try:
                    converted_ids.append(uuid.UUID(wh_id))
                except ValueError:
                    converted_ids.append(int(wh_id))
            elif isinstance(wh_id, uuid.UUID | int):
                converted_ids.append(wh_id)

        import logging

        logger = logging.getLogger(__name__)
        logger.info(
            "Filtering stocks by warehouses: original_ids=%s, converted_ids=%s",
            warehouse_ids,
            converted_ids,
        )
        stock_query = stock_query.filter(warehouse_id__in=converted_ids)

    # Sum quantities by variant
    variant_quantities = stock_query.values("product_variant_id").annotate(
        total_qty=Sum("quantity")
    )

    import logging

    logger = logging.getLogger(__name__)
    variant_qty_list = list(variant_quantities)
    logger.info(
        "Stock query returned %s variant quantities. Sample: %s",
        len(variant_qty_list),
        variant_qty_list[:3] if variant_qty_list else "None",
    )

    variant_qty_map = {
        item["product_variant_id"]: item["total_qty"] for item in variant_qty_list
    }

    # Group variants by product and build compressed string
    product_variants = defaultdict(list)

    for variant in variants:
        product_id = variant.product_id
        variant_id = variant.id

        # Get size (fallback to SKU if no size attribute found)
        size = variant_sizes.get(variant_id)
        if not size:
            # Fallback: use SKU or variant ID
            size = variant.sku or f"Variant-{variant_id}"

        # Get quantity (default to 0 if no stock)
        quantity = variant_qty_map.get(variant_id, 0)

        product_variants[product_id].append((size, quantity))

    # Format as "Size[Quantity], Size[Quantity]" and calculate totals
    for product_id, variants_list in product_variants.items():
        # Sort by size (if numeric, sort numerically; otherwise alphabetically)
        try:
            # Try to sort numerically
            variants_list.sort(key=lambda x: float(x[0]))
        except (ValueError, TypeError):
            # Fall back to alphabetic sort
            variants_list.sort(key=lambda x: str(x[0]))

        # Format as "Size[Qty], Size[Qty], ..."
        compressed = ", ".join(f"{size}[{qty}]" for size, qty in variants_list)
        result[product_id] = compressed

        # Calculate total quantity
        total_qty = sum(qty for _, qty in variants_list)
        total_quantities[product_id] = total_qty

    return result, total_quantities


def get_compressed_variant_prices(
    queryset, channel_ids: list[str] | None = None
) -> dict[tuple[int, int], dict[str, float | str | None]]:
    """Calculate mean variant prices per product per channel.

    Args:
        queryset: Product queryset
        channel_ids: List of channel IDs to filter (None = all channels)

    Returns:
        Dictionary mapping (product_id, channel_id) -> {price_amount, cost_price_amount, currency, preorder_quantity_threshold}

    """
    if not channel_ids:
        return {}

    # Force evaluate the queryset to avoid cross-database subquery issues
    product_ids = list(queryset.values_list("id", flat=True))

    if not product_ids:
        return {}

    # Get all variants for products in queryset
    variant_ids = list(
        ProductVariant.objects.using(settings.DATABASE_CONNECTION_REPLICA_NAME)
        .filter(product_id__in=product_ids)
        .values_list("id", flat=True)
    )

    if not variant_ids:
        return {}

    # Get variant channel listings and calculate mean prices per product per channel

    result: dict[tuple[int, int], dict[str, float | str | None]] = {}

    # Convert channel IDs to proper type (UUID or int)
    converted_channel_ids: list[uuid.UUID | int] = []
    for ch_id in channel_ids:
        if isinstance(ch_id, str):
            # Try to convert to UUID, fall back to int
            try:
                converted_channel_ids.append(uuid.UUID(ch_id))
            except ValueError:
                converted_channel_ids.append(int(ch_id))
        elif isinstance(ch_id, uuid.UUID | int):
            converted_channel_ids.append(ch_id)

    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        "Querying prices for channels: original_ids=%s, converted_ids=%s, variant_count=%s",
        channel_ids,
        converted_channel_ids,
        len(variant_ids),
    )

    # Get channel listings with product info
    listings = (
        ProductVariantChannelListing.objects.using(
            settings.DATABASE_CONNECTION_REPLICA_NAME
        )
        .filter(variant_id__in=variant_ids, channel_id__in=converted_channel_ids)
        .select_related("variant", "channel")
        .values("variant__product_id", "channel_id", "currency")
        .annotate(
            avg_price=Avg("price_amount"),
            avg_cost_price=Avg("cost_price_amount"),
        )
    )

    listings_list = list(listings)
    logger.info(
        "Price query returned %s listings. Sample: %s",
        len(listings_list),
        listings_list[:3] if listings_list else "None",
    )

    for listing in listings_list:
        product_id = listing["variant__product_id"]
        channel_id = listing["channel_id"]
        key = (product_id, channel_id)

        result[key] = {
            "price_amount": listing["avg_price"],
            "cost_price_amount": listing["avg_cost_price"],
            "currency": listing["currency"],
            "preorder_quantity_threshold": None,  # Not meaningful for compressed view
        }

    return result


def get_products_data_compressed(
    queryset,
    export_fields: set[str],
    attribute_ids: list[str] | None,
    warehouse_ids: list[str] | None,
    channel_ids: list[str] | None,
    requested_fields: list[str] | None = None,
    size_attribute_slug: str = "size",
) -> list[dict[str, str | bool | float | int | None]]:
    """Get products data with compressed variants (one row per product).

    This is an alternative to get_products_data that compresses all variants
    into a single "Size[Quantity]" field.
    """
    import logging

    import graphene
    from django.db.models import Case, CharField, When
    from django.db.models import Value as V
    from django.db.models.functions import Cast, Concat

    from . import ProductExportFields
    from .products_data import get_products_relations_data

    logger = logging.getLogger(__name__)
    products_data_list = []

    # Get product-level fields (exclude variant-specific fields)
    all_product_fields = ProductExportFields.HEADERS_TO_FIELDS_MAPPING["fields"]
    product_many_to_many = ProductExportFields.HEADERS_TO_FIELDS_MAPPING.get(
        "product_many_to_many", {}
    )

    # Start with ID
    fields_to_export = {"id"}

    # Build export_fields set for relations data (collections, images, etc.)
    product_level_export_fields = set()

    # Add requested fields that are product-level (not variant-level)
    if requested_fields:
        for field_name in requested_fields:
            # Check regular fields
            lookup = all_product_fields.get(field_name)
            if lookup and not lookup.startswith("variants__"):
                fields_to_export.add(lookup)

            # Check many-to-many fields (like "product media")
            many_lookup = product_many_to_many.get(field_name)
            if many_lookup:
                product_level_export_fields.add(many_lookup)

    # Get product data (one row per product, no variants)
    products_data = (
        queryset.annotate(
            product_weight=Case(
                When(weight__isnull=False, then=Concat("weight", V(" g"))),
                default=V(""),
                output_field=CharField(),
            ),
            description_as_str=Cast("description", CharField()),
        )
        .order_by("pk")
        .values(*fields_to_export)
        .distinct("pk")
    )

    # Get product relations data (collections, images, etc.)
    logger.info(
        "Compressed export: requested_fields=%s, product_level_export_fields=%s",
        requested_fields,
        product_level_export_fields,
    )
    products_relations_data = get_products_relations_data(
        queryset, product_level_export_fields, attribute_ids, channel_ids
    )

    # Get compressed variant data and total quantities
    compressed_variants, total_quantities = compress_variants_data(
        queryset, warehouse_ids, size_attribute_slug
    )

    # Get mean variant prices per product per channel
    compressed_prices = get_compressed_variant_prices(queryset, channel_ids)

    # Get channel slugs for header formatting
    from ...channel.models import Channel

    channel_slugs = {}
    if channel_ids:
        channels = Channel.objects.using(
            settings.DATABASE_CONNECTION_REPLICA_NAME
        ).filter(pk__in=channel_ids)
        channel_slugs = {ch.id: ch.slug for ch in channels}

    # Build final data
    for product_data in products_data:
        pk = product_data["id"]
        product_data["id"] = graphene.Node.to_global_id("Product", pk)

        product_relations_data: dict[str, str | bool | float | int | None] = dict(
            products_relations_data.get(pk, {})
        )

        # Add compressed variants and total quantity
        compressed_variant_str = compressed_variants.get(pk, "")
        total_qty = total_quantities.get(pk, 0)

        # Add mean variant prices for each channel
        if channel_ids:
            for channel_id in channel_ids:
                channel_slug = channel_slugs.get(int(channel_id), "")
                price_data = compressed_prices.get((pk, int(channel_id)), {})

                # Add variant channel listing fields with mean values
                if price_data:
                    product_relations_data[f"{channel_slug} (channel price amount)"] = (
                        price_data.get("price_amount")
                    )
                    product_relations_data[
                        f"{channel_slug} (channel variant currency code)"
                    ] = price_data.get("currency")
                    product_relations_data[
                        f"{channel_slug} (channel variant cost price)"
                    ] = price_data.get("cost_price_amount")
                    product_relations_data[
                        f"{channel_slug} (channel variant preorder quantity threshold)"
                    ] = price_data.get("preorder_quantity_threshold")

        data = {
            **product_data,
            **product_relations_data,
            "variants__size_quantity": compressed_variant_str,
            "variants__total_quantity": total_qty,
        }

        products_data_list.append(data)

    return products_data_list
