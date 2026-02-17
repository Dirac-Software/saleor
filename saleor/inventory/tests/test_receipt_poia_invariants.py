"""Tests validating that receipt POIAs never affect existing fulfillments.

Theory: Any POIA generated on receipt cannot require fulfillment amendments,
because fulfillments can only be created AFTER inventory is received.

The temporal ordering guarantee:
  Inventory received (POIA possible) → Orders edited → Fulfillments created → Proformas sent

Exception: POIAs created AFTER receipt (shrinkage, cycle counts) can affect fulfillments.
"""

import pytest
from decimal import Decimal

from ...inventory.stock_management import complete_receipt
from ...order import OrderStatus
from ...order.models import Fulfillment
from ...warehouse.management import allocate_sources
from ...warehouse.models import Allocation, Stock


def assert_receipt_poia_invariant(poia):
    """Assert that a receipt-time POIA doesn't affect fulfillments.

    Can be called in any test after receipt completion.
    """
    affected_sources = poia.purchase_order_item.allocation_sources.all()
    affected_orders = {
        source.allocation.order_line.order
        for source in affected_sources
    }

    fulfillments = Fulfillment.objects.filter(order__in=affected_orders)

    assert not fulfillments.exists(), (
        f"INVARIANT VIOLATION: Receipt POIA {poia.id} affects "
        f"{fulfillments.count()} existing fulfillments"
    )


def setup_order_with_poi_allocation(order_line, poi, stock, order_status, fully_paid):
    """Set up an order with allocation sourced from the given POI."""
    order = order_line.order
    order.status = order_status
    order.total_gross_amount = Decimal("1000.00")
    order.total_net_amount = Decimal("1000.00")
    order.total_charged_amount = Decimal("1000.00") if fully_paid else Decimal("0.00")
    order.save()

    order_line.quantity = 10
    order_line.variant = poi.product_variant
    order_line.save()

    allocation = Allocation.objects.create(
        order_line=order_line,
        stock=stock,
        quantity_allocated=10,
    )
    allocate_sources(allocation)
    return order


@pytest.mark.parametrize("order_status,fully_paid,should_auto_process", [
    # UNCONFIRMED orders
    (OrderStatus.UNCONFIRMED, False, True),   # Unpaid: auto-deallocate
    (OrderStatus.UNCONFIRMED, True,  False),  # Fully paid: pending (no refund workflow)
    # UNFULFILLED orders (confirmed, no fulfillments yet)
    (OrderStatus.UNFULFILLED, False, False),  # Locked: pending (needs manual edit)
    (OrderStatus.UNFULFILLED, True,  False),  # Locked + paid: pending
])
@pytest.mark.django_db
def test_receipt_poia_never_affects_fulfillments(
    order_status,
    fully_paid,
    should_auto_process,
    purchase_order_item,
    order_line,
    owned_warehouse,
    staff_user,
):
    """
    Exhaustive test: Receipt POIAs never affect fulfillments regardless of order state.

    In all cases:
    - No fulfillments exist when POIA is processed (the core invariant)
    - POIA is either auto-processed or marked pending (never corrupts state)
    """
    poi = purchase_order_item
    stock = Stock.objects.get(
        warehouse=owned_warehouse,
        product_variant=poi.product_variant,
    )

    order = setup_order_with_poi_allocation(
        order_line, poi, stock, order_status, fully_paid
    )

    # Precondition: the invariant we're testing
    assert order.fulfillments.count() == 0

    # Start receipt and record shortage
    from ..stock_management import receive_item, start_receipt
    receipt = start_receipt(poi.shipment, user=staff_user)
    receive_item(
        receipt=receipt,
        product_variant=poi.product_variant,
        quantity=90,  # Ordered 100, shortage of 10
        user=staff_user,
    )
    result = complete_receipt(receipt, user=staff_user)

    # A POIA was created for the discrepancy
    all_poias = result["adjustments_created"] + result["adjustments_pending"]
    assert len(all_poias) > 0

    # INVARIANT: no fulfillments exist, regardless of order state
    order.refresh_from_db()
    assert order.fulfillments.count() == 0, (
        f"Invariant violated: fulfillments exist after receipt POIA "
        f"(status={order_status}, fully_paid={fully_paid})"
    )

    # Processing outcome matches expectations
    if should_auto_process:
        assert len(result["adjustments_created"]) > 0, (
            f"Expected auto-process for status={order_status}, fully_paid={fully_paid}"
        )
        poia = result["adjustments_created"][0]
        assert poia.processed_at is not None

        # Deallocation happened - allocation reduced or fully deleted
        allocation = order_line.allocations.first()
        assert allocation is None or allocation.quantity_allocated < 10
    else:
        assert len(result["adjustments_pending"]) > 0, (
            f"Expected pending for status={order_status}, fully_paid={fully_paid}"
        )
        poia = result["adjustments_pending"][0]
        assert poia.processed_at is None

        # No deallocation - allocation unchanged
        allocation = order_line.allocations.first()
        assert allocation.quantity_allocated == 10

    # Invariant helper also passes
    for poia in all_poias:
        assert_receipt_poia_invariant(poia)
