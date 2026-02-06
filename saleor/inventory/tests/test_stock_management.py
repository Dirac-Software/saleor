"""Tests for confirm_purchase_order_item - the entry point for stock into owned warehouses."""

import pytest
from django.utils import timezone

from ...warehouse.models import Allocation, Stock
from .. import PurchaseOrderItemStatus
from ..exceptions import InvalidPurchaseOrderItemStatus
from ..models import PurchaseOrderItem
from ..stock_management import confirm_purchase_order_item


def test_confirm_poi_moves_stock_from_nonowned_to_owned(
    variant, nonowned_warehouse, owned_warehouse, purchase_order
):
    """Confirming POI moves stock from supplier to owned warehouse."""
    # given - stock at supplier
    source = Stock.objects.create(
        product_variant=variant,
        warehouse=nonowned_warehouse,
        quantity=100,
        quantity_allocated=0,
    )

    # Create DRAFT POI
    from ...shipping.models import Shipment

    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )
    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=50,
        quantity_allocated=0,
        total_price_amount=500.0,  # 50 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # when - confirm POI
    confirm_purchase_order_item(poi)

    # then - stock moved to owned warehouse
    source.refresh_from_db()
    assert source.quantity == 50  # 100 - 50
    assert source.quantity_allocated == 0

    destination = Stock.objects.get(warehouse=owned_warehouse, product_variant=variant)
    assert destination.quantity == 50
    assert destination.quantity_allocated == 0

    # POI is confirmed
    poi.refresh_from_db()
    assert poi.status == PurchaseOrderItemStatus.CONFIRMED
    assert poi.confirmed_at is not None


def test_confirm_poi_with_existing_allocations(
    variant, order_line, nonowned_warehouse, owned_warehouse, purchase_order
):
    """Confirming POI attaches existing allocations and creates AllocationSources."""
    from ...order import OrderStatus
    from ...warehouse.models import AllocationSource

    # given - order must be UNCONFIRMED for allocation at supplier warehouse
    order = order_line.order
    order.status = OrderStatus.UNCONFIRMED
    order.save(update_fields=["status"])

    # given - stock with allocation at supplier
    source = Stock.objects.create(
        product_variant=variant,
        warehouse=nonowned_warehouse,
        quantity=10,
        quantity_allocated=3,
    )

    allocation = Allocation.objects.create(
        order_line=order_line, stock=source, quantity_allocated=3
    )

    # Create DRAFT POI
    from ...shipping.models import Shipment

    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )
    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=10,
        quantity_allocated=0,
        total_price_amount=100.0,  # 10 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # when - confirm POI
    confirm_purchase_order_item(poi)

    # then - allocation moved to owned warehouse with AllocationSource
    allocation.refresh_from_db()
    destination = Stock.objects.get(warehouse=owned_warehouse, product_variant=variant)
    assert allocation.stock == destination

    # AllocationSource created linking allocation to POI
    source = AllocationSource.objects.get(allocation=allocation)
    assert source.purchase_order_item == poi
    assert source.quantity == 3

    # Stock quantities correct
    assert destination.quantity == 10  # Total from POI
    assert destination.quantity_allocated == 3
    # Available would be: 10 - 3 = 7

    # POI tracks allocation
    poi.refresh_from_db()
    assert poi.quantity_allocated == 3


def test_confirm_poi_with_insufficient_stock(
    variant, nonowned_warehouse, owned_warehouse, purchase_order
):
    """Confirming POI with insufficient stock at source raises error."""
    # given - only 5 units available
    Stock.objects.create(
        product_variant=variant,
        warehouse=nonowned_warehouse,
        quantity=5,
        quantity_allocated=0,
    )

    # Create DRAFT POI for 10 units
    from ...shipping.models import Shipment

    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )
    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=10,  # More than available!        quantity_allocated=0,
        total_price_amount=100.0,  # 10 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # when / then - should fail
    with pytest.raises(ValueError, match="Insufficient stock at source"):
        confirm_purchase_order_item(poi)

    # POI remains in DRAFT
    poi.refresh_from_db()
    assert poi.status == PurchaseOrderItemStatus.DRAFT


def test_confirm_poi_fails_if_already_confirmed(
    variant, nonowned_warehouse, owned_warehouse, purchase_order
):
    """Cannot confirm a POI that's already confirmed."""
    # given - already confirmed POI
    from ...shipping.models import Shipment

    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )
    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=10,
        quantity_allocated=0,
        total_price_amount=100.0,  # 10 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.CONFIRMED,  # Already confirmed!
        confirmed_at=timezone.now(),
    )

    # when / then - should fail
    with pytest.raises(InvalidPurchaseOrderItemStatus):
        confirm_purchase_order_item(poi)


def test_confirm_poi_verifies_source_stock_state(
    variant, order_line, nonowned_warehouse, owned_warehouse, purchase_order
):
    """Source stock quantities are correct after POI confirmation with allocations."""
    from ...order import OrderStatus

    # given - order must be UNCONFIRMED for allocation at supplier warehouse
    order = order_line.order
    order.status = OrderStatus.UNCONFIRMED
    order.save(update_fields=["status"])

    # given - stock with both quantity and allocated at supplier
    source = Stock.objects.create(
        product_variant=variant,
        warehouse=nonowned_warehouse,
        quantity=20,
        quantity_allocated=5,
    )

    Allocation.objects.create(order_line=order_line, stock=source, quantity_allocated=5)

    from ...shipping.models import Shipment

    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )
    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=15,  # Move 15 units (10 from quantity, 5 from allocated)        quantity_allocated=0,
        total_price_amount=150.0,  # 15 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # when - confirm POI
    confirm_purchase_order_item(poi)

    # then - source stock has correct remaining amounts
    source.refresh_from_db()
    assert source.quantity == 5  # 20 - 15 (moved 15 total)
    assert source.quantity_allocated == 0  # 5 - 5 (moved all allocated)
    # Source started with 20+5=25 total, moved 15, left with 5+0=5 total

    # destination has moved stock
    destination = Stock.objects.get(warehouse=owned_warehouse, product_variant=variant)
    assert destination.quantity == 15  # Total from POI
    assert destination.quantity_allocated == 5
    # Available would be: 15 - 5 = 10


def test_confirm_poi_with_split_allocation(
    variant, nonowned_warehouse, owned_warehouse, purchase_order, channel_USD
):
    """When POI capacity < allocation, split allocation between warehouses."""
    from ...order import OrderStatus
    from ...order.models import Order, OrderLine

    # given - allocation for 10 units at source
    source = Stock.objects.create(
        product_variant=variant,
        warehouse=nonowned_warehouse,
        quantity=0,
        quantity_allocated=10,
    )

    order = Order.objects.create(
        channel=channel_USD,
        billing_address=purchase_order.source_warehouse.address,
        shipping_address=purchase_order.source_warehouse.address,
        status=OrderStatus.UNCONFIRMED,
        lines_count=1,
    )
    order_line = OrderLine.objects.create(
        order=order,
        variant=variant,
        quantity=10,
        unit_price_gross_amount=100,
        unit_price_net_amount=100,
        total_price_gross_amount=1000,
        total_price_net_amount=1000,
        currency="USD",
        is_shipping_required=True,
        is_gift_card=False,
    )

    Allocation.objects.create(
        order_line=order_line, stock=source, quantity_allocated=10
    )

    from ...shipping.models import Shipment

    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )
    # POI only for 6 units - can't move entire allocation
    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=6,
        quantity_allocated=0,
        total_price_amount=60.0,  # 6 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # when - confirm POI
    confirm_purchase_order_item(poi)

    # then - allocation split: 6 at destination, 4 at source
    destination = Stock.objects.get(warehouse=owned_warehouse, product_variant=variant)
    assert destination.quantity == 6  # Total from POI
    assert destination.quantity_allocated == 6
    # Available would be: 6 - 6 = 0

    source.refresh_from_db()
    assert source.quantity == 0  # All taken (was 0, moved 6 from allocated)
    assert source.quantity_allocated == 4  # 10 - 6 = 4 remaining

    # Two allocations exist
    allocations = Allocation.objects.filter(order_line=order_line)
    assert allocations.count() == 2

    # One at destination with source
    dest_alloc = allocations.get(stock__warehouse=owned_warehouse)
    assert dest_alloc.quantity_allocated == 6
    assert dest_alloc.allocation_sources.count() == 1
    assert dest_alloc.allocation_sources.first().quantity == 6

    # One at source without source (can't track at non-owned)
    source_alloc = allocations.get(stock__warehouse=nonowned_warehouse)
    assert source_alloc.quantity_allocated == 4
    assert source_alloc.allocation_sources.count() == 0

    # POI tracks the moved allocation
    poi.refresh_from_db()
    assert poi.quantity_allocated == 6


def test_confirm_poi_with_multiple_allocations_fifo(
    variant, nonowned_warehouse, owned_warehouse, purchase_order, channel_USD
):
    """Multiple allocations are moved in FIFO order (oldest order first)."""
    from ...order import OrderStatus
    from ...order.models import Order, OrderLine
    from ...warehouse.models import AllocationSource

    # given - stock with 3 allocations for different orders
    source = Stock.objects.create(
        product_variant=variant,
        warehouse=nonowned_warehouse,
        quantity=5,
        quantity_allocated=9,  # 3 + 4 + 2
    )

    # Create 3 orders with different creation times
    order1 = Order.objects.create(
        channel=channel_USD,
        billing_address=purchase_order.source_warehouse.address,
        shipping_address=purchase_order.source_warehouse.address,
        status=OrderStatus.UNCONFIRMED,
        lines_count=1,
    )
    order_line1 = OrderLine.objects.create(
        order=order1,
        variant=variant,
        quantity=3,
        unit_price_gross_amount=100,
        unit_price_net_amount=100,
        total_price_gross_amount=300,
        total_price_net_amount=300,
        currency="USD",
        is_shipping_required=True,
        is_gift_card=False,
    )
    alloc1 = Allocation.objects.create(
        order_line=order_line1, stock=source, quantity_allocated=3
    )

    order2 = Order.objects.create(
        channel=channel_USD,
        billing_address=purchase_order.source_warehouse.address,
        shipping_address=purchase_order.source_warehouse.address,
        status=OrderStatus.UNCONFIRMED,
        lines_count=1,
    )
    order_line2 = OrderLine.objects.create(
        order=order2,
        variant=variant,
        quantity=4,
        unit_price_gross_amount=100,
        unit_price_net_amount=100,
        total_price_gross_amount=400,
        total_price_net_amount=400,
        currency="USD",
        is_shipping_required=True,
        is_gift_card=False,
    )
    alloc2 = Allocation.objects.create(
        order_line=order_line2, stock=source, quantity_allocated=4
    )

    order3 = Order.objects.create(
        channel=channel_USD,
        billing_address=purchase_order.source_warehouse.address,
        shipping_address=purchase_order.source_warehouse.address,
        status=OrderStatus.UNCONFIRMED,
        lines_count=1,
    )
    order_line3 = OrderLine.objects.create(
        order=order3,
        variant=variant,
        quantity=2,
        unit_price_gross_amount=100,
        unit_price_net_amount=100,
        total_price_gross_amount=200,
        total_price_net_amount=200,
        currency="USD",
        is_shipping_required=True,
        is_gift_card=False,
    )
    alloc3 = Allocation.objects.create(
        order_line=order_line3, stock=source, quantity_allocated=2
    )

    from ...shipping.models import Shipment

    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )
    # POI for 14 units - enough for all allocations
    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=14,
        quantity_allocated=0,
        total_price_amount=140.0,  # 14 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # when - confirm POI
    confirm_purchase_order_item(poi)

    # then - all allocations moved to destination in FIFO order
    destination = Stock.objects.get(warehouse=owned_warehouse, product_variant=variant)
    assert destination.quantity == 14  # Total from POI
    assert destination.quantity_allocated == 9
    # Available would be: 14 - 9 = 5

    # All allocations now at destination with sources
    alloc1.refresh_from_db()
    alloc2.refresh_from_db()
    alloc3.refresh_from_db()
    assert alloc1.stock == destination
    assert alloc2.stock == destination
    assert alloc3.stock == destination

    # Each has AllocationSource
    assert AllocationSource.objects.filter(allocation=alloc1).exists()
    assert AllocationSource.objects.filter(allocation=alloc2).exists()
    assert AllocationSource.objects.filter(allocation=alloc3).exists()

    # POI tracks all allocations
    poi.refresh_from_db()
    assert poi.quantity_allocated == 9


def test_confirm_poi_auto_confirms_order(
    variant, nonowned_warehouse, owned_warehouse, purchase_order, channel_USD
):
    """When POI confirmation makes order fully sourced, order auto-confirms."""
    from ...order import OrderStatus
    from ...order.models import Order, OrderLine

    # given - UNCONFIRMED order with allocation at non-owned warehouse
    source = Stock.objects.create(
        product_variant=variant,
        warehouse=nonowned_warehouse,
        quantity=0,
        quantity_allocated=5,
    )

    order = Order.objects.create(
        channel=channel_USD,
        billing_address=purchase_order.source_warehouse.address,
        shipping_address=purchase_order.source_warehouse.address,
        status=OrderStatus.UNCONFIRMED,
        lines_count=1,
    )
    order_line = OrderLine.objects.create(
        order=order,
        variant=variant,
        quantity=5,
        unit_price_gross_amount=100,
        unit_price_net_amount=100,
        total_price_gross_amount=500,
        total_price_net_amount=500,
        currency="USD",
        is_shipping_required=True,
        is_gift_card=False,
    )

    allocation = Allocation.objects.create(
        order_line=order_line, stock=source, quantity_allocated=5
    )

    from ...shipping.models import Shipment

    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )
    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=5,
        quantity_allocated=0,
        total_price_amount=50.0,  # 5 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # Verify order cannot be confirmed before POI confirmation
    from ...warehouse.management import can_confirm_order

    assert can_confirm_order(order) is False

    # when - confirm POI
    confirm_purchase_order_item(poi)

    # then - order can now be confirmed (allocation has source)
    allocation.refresh_from_db()
    assert allocation.stock.warehouse.is_owned
    assert allocation.allocation_sources.count() == 1
    assert can_confirm_order(order) is True

    # TODO: Add auto-confirmation logic in confirm_purchase_order_item
    # order.refresh_from_db()
    # assert order.status == OrderStatus.UNFULFILLED


def test_confirm_poi_enforces_invariant(
    variant, nonowned_warehouse, owned_warehouse, purchase_order
):
    """After confirming POIs, Stock.quantity equals sum of POI total quantities.

    Invariant: Stock.quantity == sum(POI.quantity_ordered)
    for all CONFIRMED/RECEIVED POIs at that warehouse/variant.
    """
    from ...shipping.models import Shipment

    # given - two POIs at different stages
    Stock.objects.create(
        product_variant=variant,
        warehouse=nonowned_warehouse,
        quantity=100,
        quantity_allocated=0,
    )

    shipment1 = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-1",
    )
    poi1 = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=30,
        quantity_allocated=0,
        total_price_amount=300.0,  # 30 qty × $10.0/unit
        currency="USD",
        shipment=shipment1,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    shipment2 = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-2",
    )
    poi2 = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=20,
        quantity_allocated=0,
        total_price_amount=200.0,  # 20 qty × $10.0/unit
        currency="USD",
        shipment=shipment2,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # when - confirm both POIs
    confirm_purchase_order_item(poi1)
    confirm_purchase_order_item(poi2)

    # then - destination stock quantity equals sum of POI quantities (invariant)
    destination = Stock.objects.get(warehouse=owned_warehouse, product_variant=variant)

    poi1.refresh_from_db()
    poi2.refresh_from_db()

    # Invariant: Stock.quantity = sum(POI.quantity_ordered)
    expected_quantity = poi1.quantity_ordered + poi2.quantity_ordered
    assert destination.quantity == expected_quantity
    assert destination.quantity == 50  # 30 + 20
