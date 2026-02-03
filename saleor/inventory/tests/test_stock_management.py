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
        quantity_received=0,
        quantity_allocated=0,
        unit_price_amount=10.00,
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
    from ...warehouse.models import AllocationSource

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
        quantity_received=0,
        quantity_allocated=0,
        unit_price_amount=10.00,
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
    assert destination.quantity == 7  # 10 - 3 allocated
    assert destination.quantity_allocated == 3

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
        quantity_ordered=10,  # More than available!
        quantity_received=0,
        quantity_allocated=0,
        unit_price_amount=10.00,
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
        quantity_received=0,
        quantity_allocated=0,
        unit_price_amount=10.00,
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.CONFIRMED,  # Already confirmed!
        confirmed_at=timezone.now(),
    )

    # when / then - should fail
    with pytest.raises(InvalidPurchaseOrderItemStatus):
        confirm_purchase_order_item(poi)


def test_confirm_poi_enforces_invariant():
    """After confirming POI, Stock.quantity equals sum of unallocated POI capacity.

    Invariant: Stock.quantity == sum(POI.quantity_ordered - POI.quantity_allocated)
    for all POIs at that warehouse/variant.
    """
    # TODO: Implement test that confirms the invariant holds
    # across multiple POI confirmations
