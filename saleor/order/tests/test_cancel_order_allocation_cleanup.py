from decimal import Decimal
from unittest.mock import patch

from ...plugins.manager import get_plugins_manager
from ...warehouse.models import Allocation, Stock
from ..actions import cancel_order


@patch("saleor.order.actions.send_order_canceled_confirmation")
def test_cancel_order_deletes_allocations_completely(
    send_order_canceled_confirmation_mock,
    order_with_lines,
    nonowned_warehouse,
    django_capture_on_commit_callbacks,
):
    """Test that canceling an order completely deletes allocation records.

    Regression test: deallocate_stock_for_orders() was setting quantity_allocated=0
    but not deleting the Allocation records, causing orphaned records in the database.
    """
    # Given: Order with allocations at a supplier warehouse
    order = order_with_lines
    variant = order.lines.first().variant

    supplier_stock = Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=100,
        quantity_allocated=0,
    )

    allocation = Allocation.objects.create(
        order_line=order.lines.first(),
        stock=supplier_stock,
        quantity_allocated=10,
    )
    supplier_stock.quantity_allocated = 10
    supplier_stock.save(update_fields=["quantity_allocated"])

    allocation_id = allocation.id
    assert Allocation.objects.filter(id=allocation_id).exists()

    # When: Cancel the order
    manager = get_plugins_manager(allow_replica=False)
    with django_capture_on_commit_callbacks(execute=True):
        cancel_order(order, None, None, manager)

    # Then: Allocation record should be DELETED, not just set to 0
    assert not Allocation.objects.filter(id=allocation_id).exists(), (
        "Allocation should be deleted when order is canceled, not just set to quantity_allocated=0"
    )

    # Stock quantity_allocated should be reduced
    supplier_stock.refresh_from_db()
    assert supplier_stock.quantity_allocated == 0


@patch("saleor.order.actions.send_order_canceled_confirmation")
def test_cancel_order_allows_subsequent_po_confirmation(
    send_order_canceled_confirmation_mock,
    order_with_lines,
    nonowned_warehouse,
    owned_warehouse,
    django_capture_on_commit_callbacks,
):
    """Test that after canceling an order, we can confirm a PO without AllocationInvariantViolation.

    This reproduces the bug: confirming a PO fails when there are orphaned allocations
    from canceled orders at the supplier warehouse.
    """
    from ...inventory import PurchaseOrderItemStatus
    from ...inventory.models import PurchaseOrder, PurchaseOrderItem
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...shipping import IncoTerm, ShipmentType
    from ...shipping.models import Shipment

    # Given: Order with allocations at supplier warehouse
    order = order_with_lines
    variant = order.lines.first().variant

    supplier_stock = Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=100,
        quantity_allocated=0,
    )

    Allocation.objects.create(
        order_line=order.lines.first(),
        stock=supplier_stock,
        quantity_allocated=10,
    )
    supplier_stock.quantity_allocated = 10
    supplier_stock.save(update_fields=["quantity_allocated"])

    # Cancel the order
    manager = get_plugins_manager(allow_replica=False)
    with django_capture_on_commit_callbacks(execute=True):
        cancel_order(order, None, None, manager)

    # Create a Purchase Order to move stock from supplier to owned warehouse
    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        shipment_type=ShipmentType.INBOUND,
        tracking_url="TEST-SHIPMENT",
        shipping_cost_amount=Decimal("100.00"),
        currency="USD",
        carrier="TEST-CARRIER",
        inco_term=IncoTerm.DDP,
    )

    po = PurchaseOrder.objects.create(
        source_warehouse=nonowned_warehouse,
        destination_warehouse=owned_warehouse,
    )

    supplier_stock.refresh_from_db()
    assert supplier_stock.quantity == 100
    assert supplier_stock.quantity_allocated == 0

    poi = PurchaseOrderItem.objects.create(
        order=po,
        product_variant=variant,
        quantity_ordered=50,
        total_price_amount=500.00,
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # When: Confirm the PO - should NOT raise AllocationInvariantViolation
    confirm_purchase_order_item(poi)

    # Then: POI should be confirmed successfully
    poi.refresh_from_db()
    assert poi.status == PurchaseOrderItemStatus.CONFIRMED

    # Verify stock moved correctly
    supplier_stock.refresh_from_db()
    assert supplier_stock.quantity == 50

    owned_stock = Stock.objects.get(
        warehouse=owned_warehouse,
        product_variant=variant,
    )
    assert owned_stock.quantity == 50
