"""Utilities for compressing product variants in exports."""

from collections import defaultdict

from django.conf import settings
from django.db.models import Sum

from ...product.models import ProductVariant


def compress_variants_data(
    queryset,
    warehouse_ids: list[str] | None = None,
    size_attribute_slug: str = "size",
) -> dict[int, str]:
    """Compress all variants for each product into Size[Quantity] format.

    Returns a dict mapping product_pk -> compressed string like "6[5], 7[10], 8[15]"

    Args:
        queryset: Product queryset
        warehouse_ids: List of warehouse IDs to filter stocks (None = all warehouses)
        size_attribute_slug: The slug of the size attribute (default: "size")

    Returns:
        Dictionary mapping product_id to compressed variant string

    """
    result: dict[int, str] = {}

    # Force evaluate the queryset to avoid cross-database subquery issues
    product_ids = list(queryset.values_list("id", flat=True))

    if not product_ids:
        return result

    # Get all variants for products in queryset (using replica DB)
    variants = (
        ProductVariant.objects.using(settings.DATABASE_CONNECTION_REPLICA_NAME)
        .filter(product_id__in=product_ids)
        .select_related("product")
    )

    # Get variant IDs
    variant_ids = list(variants.values_list("id", flat=True))

    if not variant_ids:
        return result

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
        stock_query = stock_query.filter(warehouse_id__in=warehouse_ids)

    # Sum quantities by variant
    variant_quantities = stock_query.values("product_variant_id").annotate(
        total_qty=Sum("quantity")
    )

    variant_qty_map = {
        item["product_variant_id"]: item["total_qty"] for item in variant_quantities
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

    # Format as "Size[Quantity], Size[Quantity]"
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

    return result


def get_products_data_compressed(
    queryset,
    export_fields: set[str],
    attribute_ids: list[str] | None,
    warehouse_ids: list[str] | None,
    channel_ids: list[str] | None,
    requested_fields: list[str] | None = None,
    size_attribute_slug: str = "size",
) -> list[dict[str, str | bool]]:
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

    # Get compressed variant data
    compressed_variants = compress_variants_data(
        queryset, warehouse_ids, size_attribute_slug
    )

    # Build final data
    for product_data in products_data:
        pk = product_data["id"]
        product_data["id"] = graphene.Node.to_global_id("Product", pk)

        product_relations_data = products_relations_data.get(pk, {})

        # Add compressed variants
        compressed_variant_str = compressed_variants.get(pk, "")

        data = {
            **product_data,
            **product_relations_data,
            "variants__size_quantity": compressed_variant_str,
        }

        products_data_list.append(data)

    return products_data_list
