import logging
import os
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from uuid import UUID

import attrs
import pandas as pd
from celery.utils.log import get_task_logger
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from ..attribute.models import Attribute
from ..celeryconf import app
from ..core.db.connection import allow_writer
from ..core.exceptions import PreorderAllocationError
from ..discount import PromotionType
from ..discount.models import Promotion, PromotionRule
from ..plugins.manager import get_plugins_manager
from ..warehouse.management import deactivate_preorder_for_variant
from ..webhook.event_types import WebhookEventAsyncType
from ..webhook.utils import get_webhooks_for_event
from . import PriceListStatus
from .lock_objects import product_qs_select_for_update
from .models import (
    Category,
    PriceList,
    PriceListItem,
    Product,
    ProductChannelListing,
    ProductType,
    ProductVariant,
)
from .search import update_products_search_vector
from .utils.product import mark_products_in_channels_as_dirty
from .utils.variant_prices import update_discounted_prices_for_promotion
from .utils.variants import (
    fetch_variants_for_promotion_rules,
    generate_and_set_variant_name,
)

logger = logging.getLogger(__name__)
task_logger = get_task_logger(f"{__name__}.celery")

PRODUCTS_BATCH_SIZE = 300

VARIANTS_UPDATE_BATCH = 500
# Results in update time ~0.2s
DISCOUNTED_PRODUCT_BATCH = 2000
# Results in update time ~2s when 600 channels exist
PROMOTION_RULE_BATCH_SIZE = 50


def _variants_in_batches(variants_qs):
    """Slice a variants queryset into batches."""
    start_pk = 0

    while True:
        variants = list(
            variants_qs.order_by("pk").filter(pk__gt=start_pk)[:VARIANTS_UPDATE_BATCH]
        )
        if not variants:
            break
        yield variants
        start_pk = variants[-1].pk


def _update_variants_names(instance: ProductType, saved_attributes: Iterable):
    """Product variant names are created from names of assigned attributes.

    After change in attribute value name, we update the names for all product variants
    that lack names and use these attributes.
    """
    initial_attributes = set(instance.variant_attributes.all())
    attributes_changed = initial_attributes.intersection(saved_attributes)
    if not attributes_changed:
        return

    variants = ProductVariant.objects.using(
        settings.DATABASE_CONNECTION_REPLICA_NAME
    ).filter(
        name="",
        product__in=instance.products.all(),
        product__product_type__variant_attributes__in=attributes_changed,
    )

    for variants_batch in _variants_in_batches(variants):
        variants_to_update = [
            generate_and_set_variant_name(variant, variant.sku, save=False)
            for variant in variants_batch
        ]
        ProductVariant.objects.bulk_update(variants_to_update, ["name", "updated_at"])


@app.task
def update_variants_names(product_type_pk: int, saved_attributes_ids: list[int]):
    try:
        instance = ProductType.objects.using(
            settings.DATABASE_CONNECTION_REPLICA_NAME
        ).get(pk=product_type_pk)
    except ObjectDoesNotExist:
        logger.warning("Cannot find product type with id: %s.", product_type_pk)
        return
    saved_attributes = Attribute.objects.using(
        settings.DATABASE_CONNECTION_REPLICA_NAME
    ).filter(pk__in=saved_attributes_ids)
    with allow_writer():
        _update_variants_names(instance, saved_attributes)


@app.task
@allow_writer()
def update_products_discounted_prices_of_promotion_task(promotion_pk: UUID):
    # FIXME: Should be removed in Saleor 3.21

    # In case of triggering this task by old server worker, mark promotion
    # as dirty. The reclacultion will happen in the background
    PromotionRule.objects.filter(promotion_id=promotion_pk).update(variants_dirty=True)


def _get_channel_to_products_map(rule_to_variant_list):
    variant_ids = {
        rule_to_variant.productvariant_id for rule_to_variant in rule_to_variant_list
    }
    variant_id_with_product_id_qs = (
        ProductVariant.objects.using(settings.DATABASE_CONNECTION_REPLICA_NAME)
        .filter(id__in=variant_ids)
        .values_list("id", "product_id")
    )
    variant_id_to_product_id_map = {}
    for variant_id, product_id in variant_id_with_product_id_qs:
        variant_id_to_product_id_map[variant_id] = product_id

    rule_ids = {
        rule_to_variant.promotionrule_id for rule_to_variant in rule_to_variant_list
    }
    PromotionChannel = PromotionRule.channels.through
    promotion_channel_qs = (
        PromotionChannel.objects.using(settings.DATABASE_CONNECTION_REPLICA_NAME)
        .filter(promotionrule_id__in=rule_ids)
        .values_list("promotionrule_id", "channel_id")
    )

    rule_to_channels_map = defaultdict(set)
    for promotionrule_id, channel_id in promotion_channel_qs.iterator(chunk_size=1000):
        rule_to_channels_map[promotionrule_id].add(channel_id)
    channel_to_products_map = defaultdict(set)
    for rule_to_variant in rule_to_variant_list:
        channel_ids = rule_to_channels_map[rule_to_variant.promotionrule_id]
        for channel_id in channel_ids:
            try:
                product_id = variant_id_to_product_id_map[
                    rule_to_variant.productvariant_id
                ]
            except KeyError:
                continue
            channel_to_products_map[channel_id].add(product_id)

    return channel_to_products_map


def _get_existing_rule_variant_list(rules: QuerySet[PromotionRule]):
    PromotionRuleVariant = PromotionRule.variants.through
    existing_rules_variants = (
        PromotionRuleVariant.objects.using(settings.DATABASE_CONNECTION_REPLICA_NAME)
        .filter(Exists(rules.filter(pk=OuterRef("promotionrule_id"))))
        .all()
        .values_list(
            "promotionrule_id",
            "productvariant_id",
        )
    )
    return [
        PromotionRuleVariant(promotionrule_id=rule_id, productvariant_id=variant_id)
        for rule_id, variant_id in existing_rules_variants
    ]


@app.task
@allow_writer()
def update_variant_relations_for_active_promotion_rules_task():
    promotions = (
        Promotion.objects.using(settings.DATABASE_CONNECTION_REPLICA_NAME)
        .active()
        .filter(type=PromotionType.CATALOGUE)
    )

    rules = (
        PromotionRule.objects.order_by("id")
        .using(settings.DATABASE_CONNECTION_REPLICA_NAME)
        .filter(
            Exists(promotions.filter(id=OuterRef("promotion_id"))), variants_dirty=True
        )
        .exclude(
            Q(reward_value__isnull=True) | Q(reward_value=0) | Q(catalogue_predicate={})
        )[:PROMOTION_RULE_BATCH_SIZE]
    )
    if ids := list(rules.values_list("pk", flat=True)):
        # fetch rules to get a qs without slicing
        rules = PromotionRule.objects.using(
            settings.DATABASE_CONNECTION_REPLICA_NAME
        ).filter(pk__in=ids)

        # Fetch existing variant relations to also mark products which are no longer
        # in the promotion as dirty
        existing_variant_relation = _get_existing_rule_variant_list(rules)

        new_rule_to_variant_list = fetch_variants_for_promotion_rules(rules=rules)
        channel_to_product_map = _get_channel_to_products_map(
            existing_variant_relation + new_rule_to_variant_list
        )
        with transaction.atomic():
            promotion_rule_ids = list(
                PromotionRule.objects.select_for_update(of=("self",))
                .filter(pk__in=ids, variants_dirty=True)
                .order_by("pk")
                .values_list("id", flat=True)
            )
            PromotionRule.objects.filter(pk__in=promotion_rule_ids).update(
                variants_dirty=False
            )

        mark_products_in_channels_as_dirty(channel_to_product_map, allow_replica=True)
        update_variant_relations_for_active_promotion_rules_task.delay()


@app.task
@allow_writer()
def update_products_discounted_prices_for_promotion_task(
    product_ids: Iterable[int],
    start_id: UUID | None = None,
    *,
    rule_ids: list[UUID] | None = None,
):
    # FIXME: Should be removed in Saleor 3.21

    # In case of triggered the task by old server worker, mark all active promotions as
    # dirty. This will make the same re-calculation as the old task.
    PromotionRule.objects.filter(variants_dirty=False).update(variants_dirty=True)


@app.task
@allow_writer()
def recalculate_discounted_price_for_products_task():
    """Recalculate discounted price for products."""
    listings = (
        ProductChannelListing.objects.using(settings.DATABASE_CONNECTION_REPLICA_NAME)
        .filter(discounted_price_dirty=True)
        .order_by("id")[:DISCOUNTED_PRODUCT_BATCH]
    )
    listing_details = listings.values_list(
        "id",
        "product_id",
    )
    products_ids = {product_id for _, product_id in listing_details}
    listing_ids = {listing_id for listing_id, _ in listing_details}
    if products_ids:
        products = Product.objects.using(
            settings.DATABASE_CONNECTION_REPLICA_NAME
        ).filter(id__in=products_ids)
        update_discounted_prices_for_promotion(products, only_dirty_products=True)
        with transaction.atomic():
            channel_listings_ids = list(
                ProductChannelListing.objects.select_for_update(of=("self",))
                .filter(id__in=listing_ids, discounted_price_dirty=True)
                .order_by("pk")
                .values_list("id", flat=True)
            )
            ProductChannelListing.objects.filter(id__in=channel_listings_ids).update(
                discounted_price_dirty=False
            )
        recalculate_discounted_price_for_products_task.delay()


@app.task
@allow_writer()
def update_discounted_prices_task(product_ids: Iterable[int]):
    # FIXME: Should be removed in Saleor 3.21

    # in case triggering the task by old server worker, we will just mark the products
    # as dirty. The recalculation will happen in the background.
    ProductChannelListing.objects.filter(product_id__in=product_ids).update(
        discounted_price_dirty=True
    )


@app.task
@allow_writer()
def deactivate_preorder_for_variants_task():
    variants_to_clean = _get_preorder_variants_to_clean()

    for variant in variants_to_clean:
        try:
            deactivate_preorder_for_variant(variant)
        except PreorderAllocationError as e:
            task_logger.warning(str(e))


def _get_preorder_variants_to_clean():
    return ProductVariant.objects.filter(
        is_preorder=True, preorder_end_date__lt=timezone.now()
    )


@app.task
@allow_writer()
def mark_products_search_vector_as_dirty(product_ids: list[int]):
    """Mark products as needing search index updates."""
    if not product_ids:
        return
    with transaction.atomic():
        ids = product_qs_select_for_update().filter(pk__in=product_ids).values("id")
        Product.objects.filter(id__in=ids).update(search_index_dirty=True)


@app.task(
    queue=settings.UPDATE_SEARCH_VECTOR_INDEX_QUEUE_NAME,
    expires=settings.BEAT_UPDATE_SEARCH_EXPIRE_AFTER_SEC,
)
def update_products_search_vector_task():
    products = (
        Product.objects.using(settings.DATABASE_CONNECTION_REPLICA_NAME)
        .filter(search_index_dirty=True)
        .order_by("updated_at")[:PRODUCTS_BATCH_SIZE]
        .values_list("id", flat=True)
    )
    with allow_writer():
        update_products_search_vector(products)


@app.task(queue=settings.COLLECTION_PRODUCT_UPDATED_QUEUE_NAME)
@allow_writer()
def collection_product_updated_task(product_ids):
    manager = get_plugins_manager(allow_replica=True)
    products = list(
        Product.objects.using(settings.DATABASE_CONNECTION_REPLICA_NAME).filter(
            id__in=product_ids
        )
    )
    replica_products_count = len(products)
    if replica_products_count != len(product_ids):
        products = list(Product.objects.filter(id__in=product_ids))
        if len(products) != replica_products_count:
            logger.warning(
                "collection_product_updated_task fetched %s products from replica, "
                "but %s from writer.",
                replica_products_count,
                len(products),
            )
    webhooks = get_webhooks_for_event(WebhookEventAsyncType.PRODUCT_UPDATED)
    for product in products:
        manager.product_updated(product, webhooks=webhooks)


@app.task
@allow_writer()
def process_price_list_task(price_list_id: int):
    from .price_list_parsing import parse_sheet

    price_list = PriceList.objects.get(pk=price_list_id)

    try:
        config = price_list.config
        sheet_name = config.get("sheet_name", "Sheet1")
        header_row = config.get("header_row", 0)
        column_map = {int(k): v for k, v in config["column_map"].items()}
        default_currency = config.get("default_currency", "")

        suffix = os.path.splitext(price_list.excel_file.name)[1] or ".xlsx"
        fd, temp_path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as tmp:
                with price_list.excel_file.open("rb") as src:
                    tmp.write(src.read())
            df = pd.read_excel(temp_path, sheet_name=sheet_name, header=header_row)
        finally:
            os.unlink(temp_path)

        _raw_categories = set(
            Category.objects.filter(
                name__in=ProductType.objects.values("name")
            ).values_list("name", flat=True)
        )
        valid_categories = _raw_categories if _raw_categories else None

        parsed_rows = parse_sheet(
            df, column_map, default_currency, valid_categories, header_row=header_row
        )

        # Deduplicate (product_code, brand) in memory before touching the DB
        seen_keys: set[tuple[str, str]] = set()
        deduped = []
        for row in parsed_rows:
            if row.is_valid:
                key = (row.product_code, row.brand)
                if key in seen_keys:
                    row = attrs.evolve(
                        row,
                        is_valid=False,
                        validation_errors=list(row.validation_errors)
                        + [
                            f"duplicate product_code+brand in this sheet: {row.product_code}"
                        ],
                    )
                else:
                    seen_keys.add(key)
            deduped.append(row)

        with transaction.atomic():
            PriceListItem.objects.filter(price_list=price_list).delete()
            PriceListItem.objects.bulk_create(
                [
                    PriceListItem(
                        price_list=price_list,
                        row_index=row.row_index,
                        product_code=row.product_code,
                        brand=row.brand,
                        description=row.description,
                        category=row.category,
                        sizes_and_qty=row.sizes_and_qty,
                        rrp=row.rrp,
                        sell_price=row.sell_price,
                        buy_price=row.buy_price,
                        weight_kg=row.weight_kg,
                        image_url=row.image_url,
                        hs_code=row.hs_code,
                        currency=row.currency,
                        is_valid=row.is_valid,
                        validation_errors=row.validation_errors,
                    )
                    for row in deduped
                ]
            )

            # Batch-populate product FK for valid items
            valid_items = list(price_list.items.filter(is_valid=True))
            if valid_items:
                from .ingestion import (
                    MissingDatabaseSetup,
                    get_products_by_code_and_brand,
                )

                try:
                    codes = [i.product_code for i in valid_items]
                    product_map = get_products_by_code_and_brand(codes)
                    updates = []
                    for item in valid_items:
                        p = product_map.get((item.product_code, item.brand))
                        if p:
                            item.product_id = p.pk
                            updates.append(item)
                    if updates:
                        PriceListItem.objects.bulk_update(updates, ["product_id"])
                except MissingDatabaseSetup:
                    logger.warning(
                        "Skipping product FK population for price list %s: "
                        "required attributes not found in database.",
                        price_list_id,
                    )

            price_list.processing_completed_at = timezone.now()
            price_list.processing_failed_at = None
            price_list.save(
                update_fields=["processing_completed_at", "processing_failed_at"]
            )
    except Exception:
        price_list.processing_completed_at = None
        price_list.processing_failed_at = timezone.now()
        price_list.save(
            update_fields=["processing_completed_at", "processing_failed_at"]
        )
        raise
    finally:
        PriceList.objects.filter(pk=price_list_id).update(is_processing=False)


def _build_product_data_from_item(item):
    from .ingestion import ProductData

    sizes = tuple(item.sizes_and_qty.keys())
    qty = tuple(item.sizes_and_qty.values())
    return ProductData(
        product_code=item.product_code,
        description=item.description,
        category=item.category,
        sizes=sizes,
        qty=qty,
        brand=item.brand,
        rrp=item.rrp,
        price=item.sell_price,
        currency=item.currency,
        weight_kg=item.weight_kg,
        image_url=item.image_url or None,
    )


def _load_activation_context(categories, channels):
    from .ingestion import get_exchange_rates
    from .models import Category, ProductType

    product_type_map = {
        pt.name: pt for pt in ProductType.objects.filter(name__in=categories)
    }
    category_map = {
        cat.name: cat for cat in Category.objects.filter(name__in=categories)
    }
    from ..attribute.models import Attribute

    required_attributes = [
        "RRP",
        "Product Code",
        "Size",
        "Minimum Order Quantity",
        "Brand",
    ]
    attribute_map = {
        attr.name: attr
        for attr in Attribute.objects.filter(name__in=required_attributes)
    }
    exchange_rates = get_exchange_rates()
    return product_type_map, category_map, attribute_map, list(channels), exchange_rates


def _activate_item(
    item,
    warehouse,
    product_type_map,
    category_map,
    attribute_map,
    channels,
    exchange_rates,
    newly_created: dict | None = None,
):
    from django.db.models import F

    from ..warehouse.models import Stock
    from .ingestion import (
        assign_product_attributes,
        assign_variant_attributes,
        create_product,
        create_product_channel_listing,
        create_variant,
        create_variant_channel_listing,
    )
    from .models import ProductVariant, ProductVariantChannelListing

    if item.product_id is None:
        product_data = _build_product_data_from_item(item)

        # Guard against within-run duplicates that slipped past processing
        cache_key = (item.product_code, item.brand)
        if newly_created is not None and cache_key in newly_created:
            product = newly_created[cache_key]
            product_type = product_type_map.get(item.category)
            item.product_id = product.pk
        else:
            product_type = product_type_map.get(item.category)
            category = category_map.get(item.category)
            if product_type is None or category is None:
                logger.warning(
                    "Cannot create product for item %s: missing product_type or category '%s'",
                    item.product_code,
                    item.category,
                )
                return False

            product = create_product(product_data, product_type, category)
            for channel in channels:
                create_product_channel_listing(product, channel)
            assign_product_attributes(product, product_data, attribute_map, moq_value=1)
            item.product_id = product.pk
            if newly_created is not None:
                newly_created[cache_key] = product

        for size, qty in item.sizes_and_qty.items():
            variant = create_variant(product, size, weight_kg=product_data.weight_kg)
            assign_variant_attributes(variant, size, attribute_map, product_type)
            for channel in channels:
                create_variant_channel_listing(
                    variant, channel, product_data, exchange_rates
                )
            Stock.objects.create(
                product_variant=variant, warehouse=warehouse, quantity=qty
            )
    else:
        from .ingestion import convert_price

        product_data = _build_product_data_from_item(item)
        product = Product.objects.get(pk=item.product_id)

        # Ensure ProductChannelListing exists and is published for each channel
        for channel in channels:
            if not ProductChannelListing.objects.filter(
                product=product, channel=channel
            ).exists():
                try:
                    with transaction.atomic():
                        create_product_channel_listing(product, channel)
                except IntegrityError:
                    pass

        ProductChannelListing.objects.filter(
            product_id=item.product_id, is_published=False
        ).update(is_published=True, available_for_purchase_at=timezone.now())
        for size, qty in item.sizes_and_qty.items():
            variant, created = ProductVariant.objects.get_or_create(
                product_id=item.product_id,
                name=size,
                defaults={"sku": f"pl-{item.product_id}-{size}"},
            )
            # Atomic increment — avoids read-modify-write race under concurrent tasks
            updated = Stock.objects.filter(
                product_variant=variant, warehouse=warehouse
            ).update(quantity=F("quantity") + qty)
            if not updated:
                Stock.objects.create(
                    product_variant=variant, warehouse=warehouse, quantity=qty
                )
            for channel in channels:
                existing_listing = ProductVariantChannelListing.objects.filter(
                    variant=variant, channel=channel
                ).first()
                if existing_listing is None:
                    try:
                        with transaction.atomic():
                            create_variant_channel_listing(
                                variant, channel, product_data, exchange_rates
                            )
                    except IntegrityError:
                        pass
                elif existing_listing.discounted_price_amount is None:
                    price = convert_price(
                        product_data.price,
                        product_data.currency,
                        channel.currency_code,
                        exchange_rates,
                    )
                    ProductVariantChannelListing.objects.filter(
                        pk=existing_listing.pk
                    ).update(discounted_price_amount=price)
    return True


@app.task
@allow_writer()
def activate_price_list_task(price_list_id: int):
    from .ingestion import MissingDatabaseSetup, get_products_by_code_and_brand

    try:
        with transaction.atomic():
            price_list = (
                PriceList.objects.select_for_update()
                .select_related("warehouse")
                .get(pk=price_list_id)
            )

            if price_list.status == PriceListStatus.ACTIVE:
                return

            if not price_list.processing_completed_at:
                raise ValueError(
                    f"PriceList {price_list_id} has not completed processing"
                )
            if price_list.warehouse.is_owned:
                raise ValueError(
                    f"Warehouse {price_list.warehouse_id} is owned; cannot activate price list"
                )

            # Auto-replace: if another list is active for this warehouse, delegate to replace.
            existing_active_id = (
                PriceList.objects.filter(
                    status=PriceListStatus.ACTIVE, warehouse=price_list.warehouse
                )
                .exclude(pk=price_list_id)
                .values_list("pk", flat=True)
                .first()
            )
            if existing_active_id is not None:
                replace_price_list_task.delay(existing_active_id, price_list_id)
                return

            items = list(price_list.items.filter(is_valid=True))

            unresolved = [i for i in items if i.product_id is None]
            if unresolved:
                try:
                    codes = [i.product_code for i in unresolved]
                    product_map = get_products_by_code_and_brand(codes)
                    for item in unresolved:
                        p = product_map.get((item.product_code, item.brand))
                        if p:
                            item.product_id = p.pk
                except MissingDatabaseSetup:
                    pass

            categories = {i.category for i in items if i.category}
            product_type_map, category_map, attribute_map, channels, exchange_rates = (
                _load_activation_context(categories, price_list.channels.all())
            )

            newly_created: dict = {}
            updated_items = []
            for item in items:
                old_product_id = item.product_id
                if not _activate_item(
                    item,
                    price_list.warehouse,
                    product_type_map,
                    category_map,
                    attribute_map,
                    channels,
                    exchange_rates,
                    newly_created=newly_created,
                ):
                    logger.warning(
                        "Skipped PriceListItem %s (product_code=%s) during activation of "
                        "PriceList %s: missing category or product type",
                        item.pk,
                        item.product_code,
                        price_list_id,
                    )
                    continue
                if item.product_id != old_product_id:
                    updated_items.append(item)

            if updated_items:
                PriceListItem.objects.bulk_update(updated_items, ["product_id"])

            activated_product_ids = [
                i.product_id for i in items if i.product_id is not None
            ]
            Product.objects.filter(id__in=activated_product_ids).update(
                search_index_dirty=True
            )

            PriceList.objects.filter(pk=price_list_id).update(
                status=PriceListStatus.ACTIVE,
                activated_at=timezone.now(),
                deactivated_at=None,
            )

        update_products_search_vector_task.delay()
    finally:
        PriceList.objects.filter(pk=price_list_id).update(is_processing=False)


def _count_draft_unconfirmed_orders(warehouse, product_ids=None):
    """Return count of distinct draft/unconfirmed orders with allocations at warehouse.

    If product_ids is given, only considers allocations for those products.
    """
    from ..order import OrderStatus
    from ..warehouse.models import Allocation

    qs = Allocation.objects.filter(
        stock__warehouse=warehouse,
        order_line__order__status__in=[OrderStatus.DRAFT, OrderStatus.UNCONFIRMED],
        quantity_allocated__gt=0,
    )
    if product_ids is not None:
        qs = qs.filter(stock__product_variant__product_id__in=product_ids)
    return qs.values("order_line__order_id").distinct().count()


def _hide_zero_stock_products(product_ids: list) -> None:
    """Set is_published=False on channel listings for products with no stock anywhere.

    Must be called after stock has already been zeroed. Only hides products whose
    total quantity across ALL warehouses is now 0 — products with stock elsewhere
    remain live.
    """
    from django.db.models import Sum
    from django.db.models.functions import Coalesce

    zero_stock_ids = list(
        Product.objects.filter(id__in=product_ids)
        .annotate(total_qty=Coalesce(Sum("variants__stocks__quantity"), 0))
        .filter(total_qty=0)
        .values_list("id", flat=True)
    )
    if zero_stock_ids:
        ProductChannelListing.objects.filter(product_id__in=zero_stock_ids).update(
            is_published=False, available_for_purchase_at=None
        )


def _deallocate_draft_unconfirmed(warehouse, product_ids, size_names=None):
    """Delete Allocation rows for draft/unconfirmed orders and reduce Stock.quantity_allocated.

    Must be called inside a transaction.atomic() block.
    """
    from django.db.models import F, Value
    from django.db.models.functions import Greatest

    from ..order import OrderStatus
    from ..warehouse.models import Allocation, Stock

    qs = (
        Allocation.objects.select_for_update(of=("self", "stock"))
        .filter(
            stock__warehouse=warehouse,
            stock__product_variant__product_id__in=product_ids,
            order_line__order__status__in=[OrderStatus.DRAFT, OrderStatus.UNCONFIRMED],
            quantity_allocated__gt=0,
        )
        .select_related("stock")
    )
    if size_names is not None:
        qs = qs.filter(stock__product_variant__name__in=size_names)

    allocations = list(qs)
    if not allocations:
        return

    deltas: dict[int, int] = {}
    for alloc in allocations:
        deltas[alloc.stock_id] = (
            deltas.get(alloc.stock_id, 0) + alloc.quantity_allocated
        )

    for stock_id, delta in deltas.items():
        Stock.objects.filter(pk=stock_id).update(
            quantity_allocated=Greatest(Value(0), F("quantity_allocated") - delta)
        )

    Allocation.objects.filter(pk__in=[a.pk for a in allocations]).delete()


@app.task
@allow_writer()
def deactivate_price_list_task(price_list_id: int):
    from django.db.models import F, Value
    from django.db.models.functions import Greatest

    from ..warehouse.models import Stock

    try:
        with transaction.atomic():
            price_list = (
                PriceList.objects.select_for_update()
                .select_related("warehouse")
                .get(pk=price_list_id)
            )

            if price_list.status == PriceListStatus.INACTIVE:
                return

            product_ids = list(
                price_list.items.filter(is_valid=True, product_id__isnull=False)
                .values_list("product_id", flat=True)
                .distinct()
            )

            _deallocate_draft_unconfirmed(price_list.warehouse, product_ids)

            # Set quantity = quantity_allocated so available stock (quantity - allocated) = 0,
            # preventing new allocations without zeroing out existing ones.
            Stock.objects.filter(
                product_variant__product_id__in=product_ids,
                warehouse=price_list.warehouse,
            ).update(quantity=Greatest(Value(0), F("quantity_allocated")))

            _hide_zero_stock_products(product_ids)

            Product.objects.filter(id__in=product_ids).update(search_index_dirty=True)

            PriceList.objects.filter(pk=price_list_id).update(
                status=PriceListStatus.INACTIVE,
                deactivated_at=timezone.now(),
            )

        update_products_search_vector_task.delay()
    finally:
        PriceList.objects.filter(pk=price_list_id).update(is_processing=False)


@app.task
@allow_writer()
def replace_price_list_task(old_id: int, new_id: int):
    from django.db.models import F, Value
    from django.db.models.functions import Greatest

    from ..warehouse.models import Stock
    from .ingestion import (
        MissingDatabaseSetup,
        create_variant,
        create_variant_channel_listing,
        get_products_by_code_and_brand,
    )

    try:
        with transaction.atomic():
            # Lock both rows in consistent pk order to prevent deadlock
            pls = {
                pl.pk: pl
                for pl in (
                    PriceList.objects.select_for_update()
                    .select_related("warehouse")
                    .filter(pk__in=[old_id, new_id])
                    .order_by("pk")
                )
            }
            if old_id not in pls or new_id not in pls:
                logger.warning(
                    "replace_price_list_task: PriceList(s) not found (old=%s, new=%s); aborting",
                    old_id,
                    new_id,
                )
                return

            old_pl = pls[old_id]
            new_pl = pls[new_id]

            if old_pl.warehouse_id != new_pl.warehouse_id:
                raise ValueError(
                    f"Cannot replace: price lists belong to different warehouses "
                    f"({old_pl.warehouse_id} vs {new_pl.warehouse_id})"
                )
            if not new_pl.processing_completed_at:
                raise ValueError(f"New PriceList {new_id} has not completed processing")

            if new_pl.status == PriceListStatus.ACTIVE:
                return

            if (
                old_pl.status == PriceListStatus.INACTIVE
                and old_pl.replaced_by_id is not None
            ):
                # A concurrent replace already deactivated old_pl and set replaced_by_id.
                # Follow the chain so new_pl replaces whatever is now active.
                if old_pl.replaced_by_id != new_id:
                    replace_price_list_task.delay(old_pl.replaced_by_id, new_id)
                return

            warehouse = old_pl.warehouse

            old_items = list(old_pl.items.filter(is_valid=True))
            new_items = list(new_pl.items.filter(is_valid=True))

            unresolved_new = [i for i in new_items if i.product_id is None]
            if unresolved_new:
                try:
                    codes = [i.product_code for i in unresolved_new]
                    product_map = get_products_by_code_and_brand(codes)
                    resolved_new = []
                    for item in unresolved_new:
                        p = product_map.get((item.product_code, item.brand))
                        if p:
                            item.product_id = p.pk
                            resolved_new.append(item)
                    if resolved_new:
                        PriceListItem.objects.bulk_update(resolved_new, ["product_id"])
                except MissingDatabaseSetup:
                    pass

            old_product_ids = {
                i.product_id for i in old_items if i.product_id is not None
            }
            new_product_ids = {
                i.product_id for i in new_items if i.product_id is not None
            }

            old_only = old_product_ids - new_product_ids
            both = old_product_ids & new_product_ids
            new_only = new_product_ids - old_product_ids

            new_item_by_product = {
                i.product_id: i for i in new_items if i.product_id is not None
            }
            old_item_by_product = {
                i.product_id: i for i in old_items if i.product_id is not None
            }

            categories = {i.category for i in new_items if i.category}
            product_type_map, category_map, attribute_map, channels, exchange_rates = (
                _load_activation_context(categories, new_pl.channels.all())
            )

            if old_only:
                _deallocate_draft_unconfirmed(warehouse, list(old_only))
                Stock.objects.filter(
                    product_variant__product_id__in=old_only,
                    warehouse=warehouse,
                ).update(quantity=Greatest(Value(0), F("quantity_allocated")))
                _hide_zero_stock_products(list(old_only))

            # Prefetch products and existing variants for all `both` products to avoid N+1
            both_products = (
                {p.pk: p for p in Product.objects.filter(pk__in=both)} if both else {}
            )
            existing_variant_names: set[tuple[int, str]] = (
                set(
                    ProductVariant.objects.filter(product_id__in=both).values_list(
                        "product_id", "name"
                    )
                )
                if both
                else set()
            )

            for product_id in both:
                new_item = new_item_by_product[product_id]
                old_item = old_item_by_product[product_id]
                new_sizes = set(new_item.sizes_and_qty.keys())
                old_sizes = set(old_item.sizes_and_qty.keys())

                removed_sizes = old_sizes - new_sizes
                if removed_sizes:
                    _deallocate_draft_unconfirmed(
                        warehouse, [product_id], size_names=removed_sizes
                    )
                    Stock.objects.filter(
                        product_variant__product_id=product_id,
                        product_variant__name__in=removed_sizes,
                        warehouse=warehouse,
                    ).update(quantity=Greatest(Value(0), F("quantity_allocated")))

                product_data = _build_product_data_from_item(new_item)
                product = both_products.get(product_id)
                if product is None:
                    logger.warning(
                        "replace_price_list_task: Product %s not found; skipping",
                        product_id,
                    )
                    continue

                for size, qty in new_item.sizes_and_qty.items():
                    if (product_id, size) in existing_variant_names:
                        Stock.objects.filter(
                            product_variant__product_id=product_id,
                            product_variant__name=size,
                            warehouse=warehouse,
                        ).update(quantity=Greatest(F("quantity_allocated"), Value(qty)))
                    else:
                        variant = create_variant(
                            product, size, weight_kg=product_data.weight_kg
                        )
                        for channel in channels:
                            try:
                                with transaction.atomic():
                                    create_variant_channel_listing(
                                        variant, channel, product_data, exchange_rates
                                    )
                            except IntegrityError:
                                pass
                        Stock.objects.create(
                            product_variant=variant, warehouse=warehouse, quantity=qty
                        )

            newly_created: dict = {}
            updated_new_items = []
            for product_id in new_only:
                new_item = new_item_by_product[product_id]
                old_product_id = new_item.product_id
                if not _activate_item(
                    new_item,
                    warehouse,
                    product_type_map,
                    category_map,
                    attribute_map,
                    channels,
                    exchange_rates,
                    newly_created=newly_created,
                ):
                    logger.warning(
                        "Skipped PriceListItem %s (product_code=%s) during replace of "
                        "PriceList %s: missing category or product type",
                        new_item.pk,
                        new_item.product_code,
                        new_id,
                    )
                    continue
                if new_item.product_id != old_product_id:
                    updated_new_items.append(new_item)

            if updated_new_items:
                PriceListItem.objects.bulk_update(updated_new_items, ["product_id"])

            all_affected_ids = list(old_product_ids | new_product_ids)
            Product.objects.filter(id__in=all_affected_ids).update(
                search_index_dirty=True
            )

            now = timezone.now()
            PriceList.objects.filter(pk=old_id).update(
                status=PriceListStatus.INACTIVE,
                deactivated_at=now,
                replaced_by_id=new_id,
            )
            PriceList.objects.filter(pk=new_id).update(
                status=PriceListStatus.ACTIVE,
                activated_at=now,
                deactivated_at=None,
            )

        update_products_search_vector_task.delay()
    finally:
        PriceList.objects.filter(pk__in=[old_id, new_id]).update(is_processing=False)
