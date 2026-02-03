import pytest
from django.utils import timezone

from ... import PurchaseOrderItemStatus
from ...models import PurchaseOrder, PurchaseOrderItem


@pytest.fixture
def purchase_order(nonowned_warehouse, owned_warehouse):
    """PurchaseOrder moving stock from supplier to owned warehouse."""
    return PurchaseOrder.objects.create(
        source_warehouse=nonowned_warehouse,
        destination_warehouse=owned_warehouse,
    )


@pytest.fixture
def purchase_order_item(purchase_order, variant):
    """Create confirmed POI with quantity available for allocation."""
    # Need to create a shipment for the FK
    from ....shipping.models import Shipment

    shipment = Shipment.objects.create(
        source=purchase_order.source_warehouse.address,
        destination=purchase_order.destination_warehouse.address,
        tracking_number="TEST-123",
    )

    return PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=100,
        quantity_received=0,
        quantity_allocated=0,
        unit_price_amount=10.00,
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.CONFIRMED,
        confirmed_at=timezone.now(),
    )


@pytest.fixture
def multiple_purchase_order_items(purchase_order, variant):
    """Three POIs confirmed at different times for FIFO testing."""
    from datetime import timedelta

    from ....shipping.models import Shipment

    shipment = Shipment.objects.create(
        source=purchase_order.source_warehouse.address,
        destination=purchase_order.destination_warehouse.address,
        tracking_number="TEST-FIFO",
    )

    now = timezone.now()
    pois = []

    for _i, days_ago in enumerate([2, 1, 0]):
        poi = PurchaseOrderItem.objects.create(
            order=purchase_order,
            product_variant=variant,
            quantity_ordered=100,
            quantity_received=0,
            quantity_allocated=0,
            unit_price_amount=10.00,
            currency="USD",
            shipment=shipment,
            country_of_origin="US",
            status=PurchaseOrderItemStatus.CONFIRMED,
            confirmed_at=now - timedelta(days=days_ago),
        )
        pois.append(poi)

    return pois  # Returns [oldest, middle, newest]
