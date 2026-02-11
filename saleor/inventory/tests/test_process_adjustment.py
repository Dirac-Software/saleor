"""Tests for process_adjustment - handling inventory discrepancies."""

import pytest

from ...order import OrderStatus
from ...warehouse.models import Allocation, AllocationSource, Stock
from .. import PurchaseOrderItemStatus
from ..exceptions import (
    AdjustmentAffectsFulfilledOrders,
    AdjustmentAffectsPaidOrders,
    AdjustmentAlreadyProcessed,
)
from ..models import PurchaseOrderItemAdjustment
from ..stock_management import confirm_purchase_order_item, process_adjustment


@pytest.fixture
def confirmed_poi_with_stock(
    purchase_order_item, owned_warehouse, nonowned_warehouse, variant
):
    """POI that's been confirmed - has stock in owned warehouse."""
    # Delete any existing stock to start fresh
    Stock.objects.filter(
        product_variant=variant,
        warehouse__in=[nonowned_warehouse, owned_warehouse],
    ).delete()

    # Setup source stock fresh
    Stock.objects.create(
        product_variant=variant,
        warehouse=nonowned_warehouse,
        quantity=100,
        quantity_allocated=0,
    )

    # Confirm POI to move stock
    purchase_order_item.status = PurchaseOrderItemStatus.DRAFT
    purchase_order_item.quantity_ordered = 50
    purchase_order_item.save()

    confirm_purchase_order_item(purchase_order_item)

    purchase_order_item.refresh_from_db()
    return purchase_order_item


def test_positive_adjustment_increases_stock(confirmed_poi_with_stock, owned_warehouse):
    """Positive adjustment (found extra units) increases available stock."""
    # given
    poi = confirmed_poi_with_stock
    stock = Stock.objects.get(
        warehouse=owned_warehouse, product_variant=poi.product_variant
    )

    initial_quantity = stock.quantity
    assert initial_quantity == 50

    adjustment = PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=10,  # Found 10 extra units
        reason="cycle_count_pos",
        affects_payable=False,
    )

    # when
    process_adjustment(adjustment)

    # then
    stock.refresh_from_db()
    assert stock.quantity == 60  # 50 + 10
    assert adjustment.processed_at is not None


def test_negative_adjustment_with_single_allocation(
    confirmed_poi_with_stock, owned_warehouse, order_line, staff_user
):
    """Negative adjustment with one allocation correctly deallocates."""
    # given
    poi = confirmed_poi_with_stock
    stock = Stock.objects.get(
        warehouse=owned_warehouse, product_variant=poi.product_variant
    )

    # Create allocation (UNCONFIRMED order, NOT paid)
    order = order_line.order
    order.status = OrderStatus.UNCONFIRMED
    order.total_charged_amount = 0  # Ensure not paid
    order.total_gross_amount = 2000
    order.total_net_amount = 2000
    order.save()

    order_line.quantity = 20
    order_line.save()

    allocation = Allocation.objects.create(
        order_line=order_line, stock=stock, quantity_allocated=20
    )

    # Create AllocationSource
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=poi, quantity=20
    )

    stock.quantity_allocated = 20
    stock.save()

    poi.quantity_allocated = 20  # Track allocation on POI
    poi.save()

    # Loss of 10 units
    adjustment = PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-10,
        reason="delivery_short",
        affects_payable=True,
    )

    # when
    process_adjustment(adjustment, user=staff_user)

    # then - stock decreased by 10
    stock.refresh_from_db()
    assert stock.quantity == 40  # 50 - 10
    assert stock.quantity_allocated == 10  # 20 - 10 deallocated

    # allocation reduced by 10
    allocation.refresh_from_db()
    assert allocation.quantity_allocated == 10

    # POI quantity_allocated restored by 10
    poi.refresh_from_db()
    assert poi.quantity_allocated == 10  # Was 20, deallocated 10


def test_negative_adjustment_with_multiple_allocations_distributes_loss(
    confirmed_poi_with_stock, owned_warehouse, channel_USD, variant, staff_user
):
    """CRITICAL TEST: Negative adjustment with MULTIPLE allocations from same POI.

    Should distribute the loss across allocations, not apply it to each one.
    This is the bug scenario that was fixed.
    """
    # given
    from ...order.models import Order, OrderLine

    poi = confirmed_poi_with_stock
    stock = Stock.objects.get(
        warehouse=owned_warehouse, product_variant=poi.product_variant
    )

    # Create TWO orders, each with allocation from the same POI
    order1 = Order.objects.create(
        channel=channel_USD,
        billing_address=owned_warehouse.address,
        shipping_address=owned_warehouse.address,
        status=OrderStatus.UNCONFIRMED,
        lines_count=1,
        total_charged_amount=0,  # NOT paid
        total_gross_amount=2000,
        total_net_amount=2000,
        currency="USD",
    )
    order_line1 = OrderLine.objects.create(
        order=order1,
        variant=variant,
        quantity=20,
        unit_price_gross_amount=100,
        unit_price_net_amount=100,
        total_price_gross_amount=2000,
        total_price_net_amount=2000,
        currency="USD",
        is_shipping_required=True,
        is_gift_card=False,
    )

    order2 = Order.objects.create(
        channel=channel_USD,
        billing_address=owned_warehouse.address,
        shipping_address=owned_warehouse.address,
        status=OrderStatus.UNCONFIRMED,
        lines_count=1,
        total_charged_amount=0,  # NOT paid
        total_gross_amount=1500,
        total_net_amount=1500,
        currency="USD",
    )
    order_line2 = OrderLine.objects.create(
        order=order2,
        variant=variant,
        quantity=15,
        unit_price_gross_amount=100,
        unit_price_net_amount=100,
        total_price_gross_amount=1500,
        total_price_net_amount=1500,
        currency="USD",
        is_shipping_required=True,
        is_gift_card=False,
    )

    # Both allocations from the SAME POI
    allocation1 = Allocation.objects.create(
        order_line=order_line1, stock=stock, quantity_allocated=20
    )
    AllocationSource.objects.create(
        allocation=allocation1, purchase_order_item=poi, quantity=20
    )

    allocation2 = Allocation.objects.create(
        order_line=order_line2, stock=stock, quantity_allocated=15
    )
    AllocationSource.objects.create(
        allocation=allocation2, purchase_order_item=poi, quantity=15
    )

    stock.quantity_allocated = 35  # 20 + 15
    stock.save()

    poi.quantity_allocated = 35
    poi.save()

    # Loss of 10 units (should be distributed across allocations)
    adjustment = PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-10,
        reason="delivery_short",
        affects_payable=True,
    )

    # when
    process_adjustment(adjustment, user=staff_user)

    # then - stock decreased by exactly 10 (not 20!)
    stock.refresh_from_db()
    assert stock.quantity == 40  # 50 - 10
    assert stock.quantity_allocated == 25  # 35 - 10

    # First allocation gets full 10 deallocated (LIFO - processes newest first)
    allocation1.refresh_from_db()
    assert allocation1.quantity_allocated == 10  # 20 - 10

    # Second allocation unchanged (loss already absorbed by first)
    allocation2.refresh_from_db()
    assert allocation2.quantity_allocated == 15  # Unchanged

    # POI correctly tracks deallocation
    poi.refresh_from_db()
    assert poi.quantity_allocated == 25  # 35 - 10


def test_negative_adjustment_larger_than_first_allocation_spans_multiple(
    confirmed_poi_with_stock, owned_warehouse, channel_USD, variant, staff_user
):
    """Loss larger than first allocation should span multiple allocations."""
    # given
    from ...order.models import Order, OrderLine

    poi = confirmed_poi_with_stock
    stock = Stock.objects.get(
        warehouse=owned_warehouse, product_variant=poi.product_variant
    )

    # Create two allocations: one with 5 units, one with 10 units
    order1 = Order.objects.create(
        channel=channel_USD,
        billing_address=owned_warehouse.address,
        shipping_address=owned_warehouse.address,
        status=OrderStatus.UNCONFIRMED,
        lines_count=1,
        total_charged_amount=0,
        total_gross_amount=500,
        total_net_amount=500,
        currency="USD",
    )
    order_line1 = OrderLine.objects.create(
        order=order1,
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

    order2 = Order.objects.create(
        channel=channel_USD,
        billing_address=owned_warehouse.address,
        shipping_address=owned_warehouse.address,
        status=OrderStatus.UNCONFIRMED,
        lines_count=1,
        total_charged_amount=0,
        total_gross_amount=1000,
        total_net_amount=1000,
        currency="USD",
    )
    order_line2 = OrderLine.objects.create(
        order=order2,
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

    allocation1 = Allocation.objects.create(
        order_line=order_line1, stock=stock, quantity_allocated=5
    )
    AllocationSource.objects.create(
        allocation=allocation1, purchase_order_item=poi, quantity=5
    )

    allocation2 = Allocation.objects.create(
        order_line=order_line2, stock=stock, quantity_allocated=10
    )
    AllocationSource.objects.create(
        allocation=allocation2, purchase_order_item=poi, quantity=10
    )

    stock.quantity_allocated = 15
    stock.save()

    poi.quantity_allocated = 15
    poi.save()

    # Loss of 8 units (bigger than first allocation's 5)
    adjustment = PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-8,
        reason="shrinkage_damage",
        affects_payable=False,
    )

    # when
    process_adjustment(adjustment, user=staff_user)

    # then
    stock.refresh_from_db()
    assert stock.quantity == 42  # 50 - 8
    assert stock.quantity_allocated == 7  # 15 - 8

    # First allocation fully deallocated (5 of the 8) and deleted
    assert not Allocation.objects.filter(id=allocation1.id).exists()

    # Second allocation partially deallocated (3 of the 8)
    allocation2.refresh_from_db()
    assert allocation2.quantity_allocated == 7  # 10 - 3


def test_adjustment_already_processed_raises_error(confirmed_poi_with_stock):
    """Cannot process the same adjustment twice."""
    # given
    poi = confirmed_poi_with_stock
    adjustment = PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=5,
        reason="cycle_count_pos",
        affects_payable=False,
    )

    process_adjustment(adjustment)

    # when/then - trying to process again should fail
    with pytest.raises(AdjustmentAlreadyProcessed):
        process_adjustment(adjustment)


def test_adjustment_affecting_unfulfilled_orders_raises_error(
    confirmed_poi_with_stock, owned_warehouse, order_line, staff_user
):
    """Cannot auto-process adjustment affecting UNFULFILLED orders."""
    # given
    poi = confirmed_poi_with_stock
    stock = Stock.objects.get(
        warehouse=owned_warehouse, product_variant=poi.product_variant
    )

    # Create allocation for UNFULFILLED order
    order = order_line.order
    order.status = OrderStatus.UNFULFILLED  # Locked status!
    order.save()

    order_line.quantity = 10
    order_line.save()

    allocation = Allocation.objects.create(
        order_line=order_line, stock=stock, quantity_allocated=10
    )
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=poi, quantity=10
    )

    stock.quantity_allocated = 10
    stock.save()

    adjustment = PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-5,
        reason="shrinkage_theft",
        affects_payable=False,
    )

    # when/then
    with pytest.raises(AdjustmentAffectsFulfilledOrders):
        process_adjustment(adjustment, user=staff_user)


def test_adjustment_affecting_paid_orders_raises_error(
    confirmed_poi_with_stock, owned_warehouse, order_line, staff_user
):
    """Cannot auto-process negative adjustment affecting fully paid orders."""
    # given
    poi = confirmed_poi_with_stock
    stock = Stock.objects.get(
        warehouse=owned_warehouse, product_variant=poi.product_variant
    )

    # Create allocation for paid order
    order = order_line.order
    order.status = OrderStatus.UNCONFIRMED
    order.total_charged_amount = order.total.gross.amount  # Fully paid!
    order.save()

    order_line.quantity = 10
    order_line.save()

    allocation = Allocation.objects.create(
        order_line=order_line, stock=stock, quantity_allocated=10
    )
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=poi, quantity=10
    )

    stock.quantity_allocated = 10
    stock.save()

    adjustment = PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-5,
        reason="shrinkage_damage",
        affects_payable=False,
    )

    # when/then
    with pytest.raises(AdjustmentAffectsPaidOrders):
        process_adjustment(adjustment, user=staff_user)


def test_adjustment_transitions_order_back_to_draft(
    confirmed_poi_with_stock, owned_warehouse, order_line, staff_user
):
    """If adjustment removes all sources, order transitions UNCONFIRMED -> DRAFT."""
    # given
    poi = confirmed_poi_with_stock
    stock = Stock.objects.get(
        warehouse=owned_warehouse, product_variant=poi.product_variant
    )

    order = order_line.order
    order.status = OrderStatus.UNCONFIRMED
    order.total_charged_amount = 0  # Ensure not paid
    order.total_gross_amount = 1000
    order.total_net_amount = 1000
    order.save()

    order_line.quantity = 10
    order_line.save()

    allocation = Allocation.objects.create(
        order_line=order_line, stock=stock, quantity_allocated=10
    )
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=poi, quantity=10
    )

    stock.quantity_allocated = 10
    stock.save()

    poi.quantity_allocated = 10
    poi.save()

    # Loss of all 10 units
    adjustment = PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-10,
        reason="delivery_short",
        affects_payable=True,
    )

    # when
    process_adjustment(adjustment, user=staff_user)

    # then - order back to DRAFT (lost all sources)
    order.refresh_from_db()
    assert order.status == OrderStatus.DRAFT

    # allocation deleted (quantity went to 0)
    assert not Allocation.objects.filter(id=allocation.id).exists()
