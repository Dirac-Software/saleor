"""Tests for FulfillmentSource tracking in owned warehouses.

FulfillmentSource records track which PurchaseOrderItem batches fulfilled which
customer orders. This provides audit trail for:
- COGS (Cost of Goods Sold) calculation per order
- Returns processing (which batch to return to)
- Supplier quality tracking
"""

import pytest

from ...order.actions import create_fulfillments
from ...order.fetch import OrderLineInfo
from ...plugins.manager import get_plugins_manager
from ..management import allocate_stocks
from ..models import AllocationSource, Stock

COUNTRY_CODE = "US"


@pytest.fixture
def allocated_order_line(order_line, owned_warehouse, purchase_order_item, channel_USD):
    """Order line with allocation from owned warehouse."""
    variant = order_line.variant
    stock, _ = Stock.objects.get_or_create(
        warehouse=owned_warehouse,
        product_variant=variant,
        defaults={"quantity": 100},
    )
    stock.quantity = 100
    stock.save(update_fields=["quantity"])

    # Set order_line quantity to 50
    order_line.quantity = 50
    order_line.save(update_fields=["quantity"])

    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=50)],
        COUNTRY_CODE,
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    order_line.refresh_from_db()
    return order_line, stock, purchase_order_item


def test_fulfill_creates_fulfillment_source_from_allocation_source(
    allocated_order_line, site_settings
):
    """When fulfilling an order, FulfillmentSource is created from AllocationSource."""
    order_line, stock, purchase_order_item = allocated_order_line
    warehouse = stock.warehouse
    order = order_line.order

    allocation = order_line.allocations.first()
    assert allocation.allocation_sources.count() == 1
    allocation_source = allocation.allocation_sources.first()

    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_allocated == 50
    assert purchase_order_item.quantity_fulfilled == 0

    create_fulfillments(
        user=None,
        app=None,
        order=order,
        fulfillment_lines_for_warehouses={
            warehouse.pk: [{"order_line": order_line, "quantity": 50}]
        },
        manager=get_plugins_manager(allow_replica=False),
        site_settings=site_settings,
        notify_customer=False,
    )

    order_line.refresh_from_db()
    fulfillment_line = order_line.fulfillment_lines.first()
    assert fulfillment_line is not None
    assert fulfillment_line.quantity == 50

    assert fulfillment_line.fulfillment_sources.count() == 1
    fulfillment_source = fulfillment_line.fulfillment_sources.first()
    assert fulfillment_source.purchase_order_item == purchase_order_item
    assert fulfillment_source.quantity == 50

    assert AllocationSource.objects.filter(id=allocation_source.id).count() == 0

    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_allocated == 0
    assert purchase_order_item.quantity_fulfilled == 50


def test_partial_fulfill_creates_partial_fulfillment_source(
    allocated_order_line, site_settings
):
    """Partial fulfillment creates FulfillmentSource for fulfilled portion only."""
    order_line, stock, purchase_order_item = allocated_order_line
    warehouse = stock.warehouse
    order = order_line.order

    allocation = order_line.allocations.first()
    assert allocation.allocation_sources.count() == 1

    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_allocated == 50

    create_fulfillments(
        user=None,
        app=None,
        order=order,
        fulfillment_lines_for_warehouses={
            warehouse.pk: [{"order_line": order_line, "quantity": 20}]
        },
        manager=get_plugins_manager(allow_replica=False),
        site_settings=site_settings,
        notify_customer=False,
    )

    order_line.refresh_from_db()
    fulfillment_line = order_line.fulfillment_lines.first()
    assert fulfillment_line.quantity == 20

    assert fulfillment_line.fulfillment_sources.count() == 1
    fulfillment_source = fulfillment_line.fulfillment_sources.first()
    assert fulfillment_source.quantity == 20

    allocation.refresh_from_db()
    assert allocation.quantity_allocated == 30
    assert allocation.allocation_sources.count() == 1
    remaining_source = allocation.allocation_sources.first()
    assert remaining_source.quantity == 30

    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_allocated == 30
    assert purchase_order_item.quantity_fulfilled == 20


def test_fulfill_from_multiple_poi_batches(
    order_line,
    owned_warehouse,
    multiple_purchase_order_items,
    channel_USD,
    site_settings,
):
    """Fulfillment creates FulfillmentSource for each POI batch consumed."""
    poi_oldest, poi_middle, poi_newest = multiple_purchase_order_items
    variant = order_line.variant
    order = order_line.order

    stock, _ = Stock.objects.get_or_create(
        warehouse=owned_warehouse,
        product_variant=variant,
        defaults={"quantity": 300},
    )
    stock.quantity = 300
    stock.save(update_fields=["quantity"])

    # Set order_line quantity to 150
    order_line.quantity = 150
    order_line.save(update_fields=["quantity"])

    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=150)],
        COUNTRY_CODE,
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    allocation = order_line.allocations.first()
    assert allocation.allocation_sources.count() == 2

    create_fulfillments(
        user=None,
        app=None,
        order=order,
        fulfillment_lines_for_warehouses={
            owned_warehouse.pk: [{"order_line": order_line, "quantity": 150}]
        },
        manager=get_plugins_manager(allow_replica=False),
        site_settings=site_settings,
        notify_customer=False,
    )

    order_line.refresh_from_db()
    fulfillment_line = order_line.fulfillment_lines.first()
    assert fulfillment_line.quantity == 150

    fulfillment_sources = fulfillment_line.fulfillment_sources.all()
    assert len(fulfillment_sources) == 2

    fs_by_poi = {fs.purchase_order_item_id: fs for fs in fulfillment_sources}
    assert fs_by_poi[poi_oldest.id].quantity == 100
    assert fs_by_poi[poi_middle.id].quantity == 50

    poi_oldest.refresh_from_db()
    poi_middle.refresh_from_db()
    assert poi_oldest.quantity_allocated == 0
    assert poi_oldest.quantity_fulfilled == 100
    assert poi_middle.quantity_allocated == 0
    assert poi_middle.quantity_fulfilled == 50


def test_quantity_fulfilled_updates_correctly(allocated_order_line, site_settings):
    """POI.quantity_fulfilled increments when orders are fulfilled."""
    order_line, stock, purchase_order_item = allocated_order_line
    warehouse = stock.warehouse
    order = order_line.order

    purchase_order_item.refresh_from_db()
    initial_allocated = purchase_order_item.quantity_allocated
    initial_fulfilled = purchase_order_item.quantity_fulfilled

    assert initial_allocated == 50
    assert initial_fulfilled == 0

    create_fulfillments(
        user=None,
        app=None,
        order=order,
        fulfillment_lines_for_warehouses={
            warehouse.pk: [{"order_line": order_line, "quantity": 30}]
        },
        manager=get_plugins_manager(allow_replica=False),
        site_settings=site_settings,
        notify_customer=False,
    )

    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_allocated == 20
    assert purchase_order_item.quantity_fulfilled == 30

    create_fulfillments(
        user=None,
        app=None,
        order=order,
        fulfillment_lines_for_warehouses={
            warehouse.pk: [{"order_line": order_line, "quantity": 20}]
        },
        manager=get_plugins_manager(allow_replica=False),
        site_settings=site_settings,
        notify_customer=False,
    )

    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_allocated == 0
    assert purchase_order_item.quantity_fulfilled == 50


def test_available_quantity_decreases_after_fulfillment(
    allocated_order_line, site_settings
):
    """POI.available_quantity correctly accounts for fulfilled quantity."""
    order_line, stock, purchase_order_item = allocated_order_line
    warehouse = stock.warehouse
    order = order_line.order

    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_ordered == 100
    assert purchase_order_item.quantity_allocated == 50
    assert purchase_order_item.quantity_fulfilled == 0
    assert purchase_order_item.available_quantity == 50

    create_fulfillments(
        user=None,
        app=None,
        order=order,
        fulfillment_lines_for_warehouses={
            warehouse.pk: [{"order_line": order_line, "quantity": 50}]
        },
        manager=get_plugins_manager(allow_replica=False),
        site_settings=site_settings,
        notify_customer=False,
    )

    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_allocated == 0
    assert purchase_order_item.quantity_fulfilled == 50
    assert purchase_order_item.available_quantity == 50


def test_partial_fulfill_spanning_multiple_poi_batches(
    order_line,
    owned_warehouse,
    multiple_purchase_order_items,
    channel_USD,
    site_settings,
):
    """Partial fulfillment spanning multiple POI batches correctly converts sources.

    Tests the critical case where:
    - Allocation spans 2 POI batches (100 + 50 = 150 allocated)
    - Fulfill only 120 units (partial)
    - Should fully consume first batch and partially consume second batch
    - Should create FulfillmentSource for both batches with correct quantities
    - Should leave remaining AllocationSource for unfulfilled portion
    """
    poi_oldest, poi_middle, poi_newest = multiple_purchase_order_items
    variant = order_line.variant
    order = order_line.order

    stock, _ = Stock.objects.get_or_create(
        warehouse=owned_warehouse,
        product_variant=variant,
        defaults={"quantity": 300},
    )
    stock.quantity = 300
    stock.save(update_fields=["quantity"])

    # Set order_line quantity to 150
    order_line.quantity = 150
    order_line.save(update_fields=["quantity"])

    # Allocate 150 (will span poi_oldest:100 + poi_middle:50)
    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=150)],
        COUNTRY_CODE,
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    allocation = order_line.allocations.first()
    assert allocation.allocation_sources.count() == 2
    allocation_sources_before = {
        src.purchase_order_item_id: src.quantity
        for src in allocation.allocation_sources.all()
    }
    assert allocation_sources_before[poi_oldest.id] == 100
    assert allocation_sources_before[poi_middle.id] == 50

    # Fulfill only 120 (should consume: poi_oldest:100 fully + poi_middle:20 partially)
    # Note: LIFO order means we deallocate from poi_middle first, then poi_oldest
    # So it should consume: poi_middle:50 + poi_oldest:70
    create_fulfillments(
        user=None,
        app=None,
        order=order,
        fulfillment_lines_for_warehouses={
            owned_warehouse.pk: [{"order_line": order_line, "quantity": 120}]
        },
        manager=get_plugins_manager(allow_replica=False),
        site_settings=site_settings,
        notify_customer=False,
    )

    order_line.refresh_from_db()
    fulfillment_line = order_line.fulfillment_lines.first()
    assert fulfillment_line.quantity == 120

    # Check FulfillmentSources created (LIFO: middle fully, oldest partially)
    fulfillment_sources = fulfillment_line.fulfillment_sources.all()
    assert len(fulfillment_sources) == 2

    fs_by_poi = {fs.purchase_order_item_id: fs for fs in fulfillment_sources}
    assert fs_by_poi[poi_middle.id].quantity == 50  # Fully consumed
    assert fs_by_poi[poi_oldest.id].quantity == 70  # Partially consumed

    # Check remaining AllocationSource
    allocation.refresh_from_db()
    assert allocation.quantity_allocated == 30
    assert allocation.allocation_sources.count() == 1  # Only oldest remains
    remaining_source = allocation.allocation_sources.first()
    assert remaining_source.purchase_order_item_id == poi_oldest.id
    assert remaining_source.quantity == 30  # 100 - 70 = 30

    # Check POI states
    poi_oldest.refresh_from_db()
    poi_middle.refresh_from_db()

    # poi_middle: fully consumed
    assert poi_middle.quantity_allocated == 0
    assert poi_middle.quantity_fulfilled == 50

    # poi_oldest: partially consumed
    assert poi_oldest.quantity_allocated == 30
    assert poi_oldest.quantity_fulfilled == 70

    # Check Stock state
    stock.refresh_from_db()
    assert stock.quantity == 300 - 120  # 180 remaining
    assert stock.quantity_allocated == 30  # 30 still allocated
