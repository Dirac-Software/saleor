import pytest
from decimal import Decimal
from django.utils import timezone
from prices import Money

from ... import PurchaseOrderItemStatus
from ...models import PurchaseOrder, PurchaseOrderItem


@pytest.fixture
def shipment(nonowned_warehouse, owned_warehouse):
    """Create a shipment for testing receipts."""
    from ....shipping.models import Shipment

    return Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-SHIPMENT",
        shipping_cost=Money(Decimal("100.00"), "USD"),
        carrier="TEST-CARRIER",
        arrived_at=timezone.now(),
        departed_at=timezone.now(),
    )


@pytest.fixture
def purchase_order(nonowned_warehouse, owned_warehouse):
    """PurchaseOrder moving stock from supplier to owned warehouse."""
    return PurchaseOrder.objects.create(
        source_warehouse=nonowned_warehouse,
        destination_warehouse=owned_warehouse,
    )


@pytest.fixture
def purchase_order_item(purchase_order, variant, shipment):
    """Create confirmed POI with quantity available for allocation."""
    from ....warehouse.models import Stock
    from ...stock_management import confirm_purchase_order_item

    # Ensure stock exists at source warehouse (supplier)
    Stock.objects.get_or_create(
        warehouse=purchase_order.source_warehouse,
        product_variant=variant,
        defaults={"quantity": 1000, "quantity_allocated": 0},
    )

    # Create POI in DRAFT status
    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=100,
        total_price_amount=1000.00,  # 100 qty × $10/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # Properly confirm through business logic (creates Stock at destination)
    confirm_purchase_order_item(poi)

    return poi


@pytest.fixture
def multiple_purchase_order_items(purchase_order, variant):
    """Three POIs confirmed at different times for FIFO testing."""
    from datetime import timedelta

    from ....shipping.models import Shipment
    from ....warehouse.models import Stock
    from ...stock_management import confirm_purchase_order_item

    # Ensure sufficient stock exists at source warehouse
    Stock.objects.get_or_create(
        warehouse=purchase_order.source_warehouse,
        product_variant=variant,
        defaults={"quantity": 3000, "quantity_allocated": 0},
    )

    shipment = Shipment.objects.create(
        source=purchase_order.source_warehouse.address,
        destination=purchase_order.destination_warehouse.address,
        tracking_number="TEST-FIFO",
        shipping_cost=Money(Decimal("100.00"), "USD"),
        carrier="TEST-CARRIER",
        arrived_at=timezone.now(),
        departed_at=timezone.now(),
    )

    now = timezone.now()
    pois = []

    for _i, days_ago in enumerate([2, 1, 0]):
        poi = PurchaseOrderItem.objects.create(
            order=purchase_order,
            product_variant=variant,
            quantity_ordered=100,
            total_price_amount=1000.00,  # 100 qty × $10/unit
            currency="USD",
            shipment=shipment,
            country_of_origin="US",
            status=PurchaseOrderItemStatus.DRAFT,
        )
        # Properly confirm through business logic
        confirm_purchase_order_item(poi)
        # Manually set confirmed_at for FIFO testing
        poi.confirmed_at = now - timedelta(days=days_ago)
        poi.save(update_fields=["confirmed_at"])
        pois.append(poi)

    return pois  # Returns [oldest, middle, newest]
