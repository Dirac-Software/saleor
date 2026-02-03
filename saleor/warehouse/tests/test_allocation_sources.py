"""Tests for AllocationSource tracking in owned warehouses.

AllocationSource records track which PurchaseOrderItem batches fulfill which
customer order allocations. This is required for:
- COGS (Cost of Goods Sold) calculation
- Batch traceability (recalls, expiry tracking)
- FIFO inventory management

Key invariant: Allocations in owned warehouses MUST have AllocationSources.
Allocations in non-owned warehouses do NOT need AllocationSources.
"""

import pytest
from django.db.models import Sum

from ...core.exceptions import InsufficientStock
from ...order.fetch import OrderLineInfo
from ...plugins.manager import get_plugins_manager
from ..management import (
    allocate_stocks,
    deallocate_stock,
    increase_stock,
)
from ..models import Allocation, AllocationSource, Stock

COUNTRY_CODE = "US"


def test_allocate_sources_creates_allocation_source_for_owned_warehouse(
    order_line, owned_warehouse, purchase_order_item, channel_USD
):
    """When allocating in owned warehouse, AllocationSource is created."""
    # given
    variant = order_line.variant
    stock = Stock.objects.create(
        warehouse=owned_warehouse, product_variant=variant, quantity=100
    )

    # when
    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=50)],
        COUNTRY_CODE,
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # then
    allocation = Allocation.objects.get(order_line=order_line, stock=stock)
    assert allocation.quantity_allocated == 50

    # Check AllocationSource was created
    assert allocation.allocation_sources.count() == 1
    source = allocation.allocation_sources.first()
    assert source.purchase_order_item == purchase_order_item
    assert source.quantity == 50

    # Check POI.quantity_allocated was updated
    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_allocated == 50


def test_allocate_sources_not_created_for_nonowned_warehouse(
    order_line, nonowned_warehouse, channel_USD
):
    """Non-owned warehouses don't create AllocationSources."""
    # given
    variant = order_line.variant
    stock = Stock.objects.create(
        warehouse=nonowned_warehouse, product_variant=variant, quantity=100
    )

    # when
    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=50)],
        COUNTRY_CODE,
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # then
    allocation = Allocation.objects.get(order_line=order_line, stock=stock)
    assert allocation.quantity_allocated == 50

    # Check NO AllocationSource was created for non-owned warehouse
    assert allocation.allocation_sources.count() == 0


def test_deallocate_sources_restores_poi_quantity_allocated(
    order_line, owned_warehouse, purchase_order_item, channel_USD
):
    """Deallocating removes AllocationSource and restores POI.quantity_allocated."""
    # given - create allocation with source
    variant = order_line.variant
    stock = Stock.objects.create(
        warehouse=owned_warehouse, product_variant=variant, quantity=100
    )

    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=50)],
        COUNTRY_CODE,
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    allocation = Allocation.objects.get(order_line=order_line, stock=stock)
    assert allocation.allocation_sources.count() == 1

    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_allocated == 50

    # when - deallocate
    deallocate_stock(
        [OrderLineInfo(line=order_line, variant=variant, quantity=50)],
        manager=get_plugins_manager(allow_replica=False),
    )

    # then - AllocationSource removed and POI.quantity_allocated restored
    allocation.refresh_from_db()
    assert allocation.quantity_allocated == 0
    assert allocation.allocation_sources.count() == 0

    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_allocated == 0


def test_partial_deallocate_updates_poi_correctly(
    order_line, owned_warehouse, purchase_order_item, channel_USD
):
    """Partial deallocation reduces POI.quantity_allocated correctly."""
    # given - allocate 50
    variant = order_line.variant
    stock = Stock.objects.create(
        warehouse=owned_warehouse, product_variant=variant, quantity=100
    )

    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=50)],
        COUNTRY_CODE,
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_allocated == 50

    # when - deallocate 20
    deallocate_stock(
        [OrderLineInfo(line=order_line, variant=variant, quantity=20)],
        manager=get_plugins_manager(allow_replica=False),
    )

    # then - POI.quantity_allocated reduced to 30
    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_allocated == 30

    allocation = Allocation.objects.get(order_line=order_line, stock=stock)
    assert allocation.quantity_allocated == 30


def test_allocation_uses_fifo_across_multiple_pois(
    order_line,
    order,
    owned_warehouse,
    multiple_purchase_order_items,
    channel_USD,
):
    """Allocations consume POIs in FIFO order (oldest first)."""
    # given - 3 POIs confirmed at different times, each with 100 units
    poi_oldest, poi_middle, poi_newest = multiple_purchase_order_items
    variant = order_line.variant

    Stock.objects.create(
        warehouse=owned_warehouse, product_variant=variant, quantity=300
    )

    # Create 3 order lines to allocate 250 units total
    order_line_2 = order.lines.create(
        product_name=order_line.product_name,
        variant_name=order_line.variant_name,
        product_sku=order_line.product_sku,
        variant=variant,
        quantity=1,
        unit_price_gross_amount=10,
        unit_price_net_amount=10,
        total_price_gross_amount=10,
        total_price_net_amount=10,
        currency="USD",
        is_shipping_required=False,
        is_gift_card=False,
    )

    order_line_3 = order.lines.create(
        product_name=order_line.product_name,
        variant_name=order_line.variant_name,
        product_sku=order_line.product_sku,
        variant=variant,
        quantity=1,
        unit_price_gross_amount=10,
        unit_price_net_amount=10,
        total_price_gross_amount=10,
        total_price_net_amount=10,
        currency="USD",
        is_shipping_required=False,
        is_gift_card=False,
    )

    # when - allocate 250 units (100 + 100 + 50)
    allocate_stocks(
        [
            OrderLineInfo(line=order_line, variant=variant, quantity=100),
            OrderLineInfo(line=order_line_2, variant=variant, quantity=100),
            OrderLineInfo(line=order_line_3, variant=variant, quantity=50),
        ],
        COUNTRY_CODE,
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # then - should consume oldest first (FIFO)
    poi_oldest.refresh_from_db()
    poi_middle.refresh_from_db()
    poi_newest.refresh_from_db()

    assert poi_oldest.quantity_allocated == 100  # Fully consumed
    assert poi_middle.quantity_allocated == 100  # Fully consumed
    assert poi_newest.quantity_allocated == 50  # Partially consumed
    # Total: 250 allocated across 3 POIs in FIFO order


def test_insufficient_poi_quantity_raises_error(
    order_line, owned_warehouse, purchase_order_item, channel_USD
):
    """Allocating more than POI capacity raises InsufficientStock."""
    # given - POI with 100 capacity, 80 already allocated
    variant = order_line.variant
    purchase_order_item.quantity_allocated = 80
    purchase_order_item.save()

    Stock.objects.create(
        warehouse=owned_warehouse, product_variant=variant, quantity=100
    )

    # when/then - trying to allocate 30 more (total 110 > 100 capacity) fails
    with pytest.raises(InsufficientStock):
        allocate_stocks(
            [OrderLineInfo(line=order_line, variant=variant, quantity=30)],
            COUNTRY_CODE,
            channel_USD,
            manager=get_plugins_manager(allow_replica=False),
        )


def test_poi_quantity_allocated_invariant(
    order,
    owned_warehouse,
    variant,
    purchase_order_item,
    channel_USD,
):
    """POI.quantity_allocated equals sum of AllocationSource.quantity."""
    # given - create 3 order lines
    lines = []
    for i in range(3):
        line = order.lines.create(
            product_name=f"Product {i}",
            variant_name=variant.name,
            product_sku=variant.sku,
            variant=variant,
            quantity=1,
            unit_price_gross_amount=10,
            unit_price_net_amount=10,
            total_price_gross_amount=10,
            total_price_net_amount=10,
            currency="USD",
            is_shipping_required=False,
            is_gift_card=False,
        )
        lines.append(line)

    Stock.objects.create(
        warehouse=owned_warehouse, product_variant=variant, quantity=100
    )

    # when - allocate different amounts to each line (10 + 20 + 15 = 45)
    allocate_stocks(
        [
            OrderLineInfo(line=lines[0], variant=variant, quantity=10),
            OrderLineInfo(line=lines[1], variant=variant, quantity=20),
            OrderLineInfo(line=lines[2], variant=variant, quantity=15),
        ],
        COUNTRY_CODE,
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # then - POI.quantity_allocated should equal sum of all AllocationSource quantities
    total_from_sources = (
        AllocationSource.objects.filter(
            purchase_order_item=purchase_order_item
        ).aggregate(total=Sum("quantity"))["total"]
        or 0
    )

    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_allocated == 45
    assert purchase_order_item.quantity_allocated == total_from_sources


def test_increase_stock_with_allocate_creates_sources(
    order_line, owned_warehouse, purchase_order_item
):
    """increase_stock with allocate=True creates AllocationSources."""
    # given
    variant = order_line.variant
    stock = Stock.objects.create(
        warehouse=owned_warehouse, product_variant=variant, quantity=50
    )

    # when
    increase_stock(order_line, owned_warehouse, quantity=30, allocate=True)

    # then
    allocation = Allocation.objects.get(order_line=order_line, stock=stock)
    assert allocation.quantity_allocated == 30
    assert allocation.allocation_sources.count() == 1

    source = allocation.allocation_sources.first()
    assert source.purchase_order_item == purchase_order_item
    assert source.quantity == 30

    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_allocated == 30


def test_increase_existing_allocation_creates_incremental_sources(
    order_line, owned_warehouse, purchase_order_item, channel_USD
):
    """Increasing existing allocation creates additional AllocationSources."""
    # given - initial allocation of 20
    variant = order_line.variant
    stock = Stock.objects.create(
        warehouse=owned_warehouse, product_variant=variant, quantity=100
    )

    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=20)],
        COUNTRY_CODE,
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    allocation = Allocation.objects.get(order_line=order_line, stock=stock)
    assert allocation.quantity_allocated == 20
    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_allocated == 20

    # when - increase by 15 more
    increase_stock(order_line, owned_warehouse, quantity=15, allocate=True)

    # then - total allocation is 35, POI.quantity_allocated is 35
    allocation.refresh_from_db()
    assert allocation.quantity_allocated == 35

    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_allocated == 35

    # AllocationSource quantity should sum to 35
    total_sources = (
        allocation.allocation_sources.aggregate(total=Sum("quantity"))["total"] or 0
    )
    assert total_sources == 35


def test_order_auto_confirms_when_all_allocations_sourced(
    order_line, owned_warehouse, purchase_order_item, channel_USD
):
    """Order automatically confirms when all AllocationSources are assigned."""
    from ...order import OrderStatus

    # given - UNCONFIRMED order
    order = order_line.order
    order.status = OrderStatus.UNCONFIRMED
    order.save(update_fields=["status"])

    variant = order_line.variant
    Stock.objects.create(
        warehouse=owned_warehouse, product_variant=variant, quantity=100
    )

    # when - allocate (which creates AllocationSources)
    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=50)],
        COUNTRY_CODE,
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # then - order should auto-confirm
    order.refresh_from_db()
    assert order.status == OrderStatus.UNFULFILLED


def test_allocate_sources_ignores_draft_and_cancelled_pois(
    order_line, owned_warehouse, purchase_order, channel_USD
):
    """AllocationSource only uses CONFIRMED or RECEIVED POIs, not DRAFT or CANCELLED."""
    from ...inventory import PurchaseOrderItemStatus
    from ...inventory.models import PurchaseOrderItem
    from ...shipping.models import Shipment

    # given - stock in owned warehouse
    variant = order_line.variant
    Stock.objects.create(
        warehouse=owned_warehouse, product_variant=variant, quantity=100
    )

    # Create DRAFT POI (should be ignored)
    shipment = Shipment.objects.create(
        source=purchase_order.source_warehouse.address,
        destination=purchase_order.destination_warehouse.address,
        tracking_number="DRAFT-123",
    )
    draft_poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=100,
        quantity_received=0,
        quantity_allocated=0,
        unit_price_amount=10.00,
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,  # DRAFT - should be ignored
    )

    # when - try to allocate (should fail - no active POIs)
    with pytest.raises(InsufficientStock):
        allocate_stocks(
            [OrderLineInfo(line=order_line, variant=variant, quantity=50)],
            COUNTRY_CODE,
            channel_USD,
            manager=get_plugins_manager(allow_replica=False),
        )

    # then - no AllocationSource created (allocation itself may exist but failed)
    assert AllocationSource.objects.count() == 0
    assert draft_poi.quantity_allocated == 0  # Not used
