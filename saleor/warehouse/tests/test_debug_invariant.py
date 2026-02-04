"""Debug test to understand the invariant violation."""

from ...inventory import PurchaseOrderItemStatus
from ...inventory.models import PurchaseOrderItem
from ...inventory.stock_management import confirm_purchase_order_item
from ...order.fetch import OrderLineInfo
from ...plugins.manager import get_plugins_manager
from ...shipping.models import Shipment
from ..management import allocate_stocks
from ..models import Allocation, AllocationSource, Stock


def test_debug_poi_confirmation_with_existing_allocation(
    owned_warehouse,
    nonowned_warehouse,
    purchase_order,
    order_line,
    channel_USD,
):
    """Debug what happens when we confirm POI with pre-existing allocations."""
    variant = order_line.variant

    # Step 1: Create stock at supplier
    supplier_stock = Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=200,
        quantity_allocated=0,
    )

    # Step 2: Customer orders (allocate from supplier)
    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=50)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    supplier_stock.refresh_from_db()

    allocation = Allocation.objects.get(order_line=order_line)

    # Step 3: Create POI
    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )

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
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # Step 4: Confirm POI (this should move allocation)
    confirm_purchase_order_item(poi)

    # Step 5: Check final state
    poi.refresh_from_db()
    supplier_stock.refresh_from_db()

    owned_stock = Stock.objects.filter(
        warehouse=owned_warehouse, product_variant=variant
    ).first()

    # Check allocation
    allocation.refresh_from_db()

    # Check AllocationSources
    AllocationSource.objects.filter(allocation=allocation)

    # Calculate expected owned stock quantity
    if owned_stock:
        expected = poi.quantity_ordered - poi.quantity_allocated
        assert owned_stock.quantity == expected
