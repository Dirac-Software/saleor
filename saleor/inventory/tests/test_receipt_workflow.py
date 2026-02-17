"""Tests for receipt workflow (start_receipt, receive_item, complete_receipt, etc)."""

import pytest
from django.utils import timezone

from ...order import FulfillmentStatus, OrderStatus
from ...order.models import Fulfillment
from ...warehouse.models import Allocation, Stock
from .. import ReceiptStatus
from ..exceptions import (
    ReceiptLineNotInProgress,
    ReceiptNotInProgress,
)
from ..models import PurchaseOrderItem, Receipt, ReceiptLine
from ..stock_management import (
    complete_receipt,
    confirm_purchase_order_item,
    delete_receipt,
    delete_receipt_line,
    receive_item,
    start_receipt,
)

# Tests for start_receipt function


def test_creates_receipt_successfully(shipment, staff_user):
    # given: a shipment without a receipt
    # when: starting a receipt
    receipt = start_receipt(shipment, user=staff_user)

    # then: receipt is created with correct status
    assert receipt.shipment == shipment
    assert receipt.status == ReceiptStatus.IN_PROGRESS
    assert receipt.created_by == staff_user
    assert receipt.created_at is not None
    assert receipt.completed_at is None


def test_resumes_existing_in_progress_receipt(shipment, staff_user):
    # given: a shipment with an in-progress receipt
    existing_receipt = start_receipt(shipment, user=staff_user)

    # when: starting another receipt for same shipment
    receipt = start_receipt(shipment, user=staff_user)

    # then: returns the existing receipt
    assert receipt.id == existing_receipt.id
    assert receipt.status == ReceiptStatus.IN_PROGRESS


def test_error_when_shipment_already_received(shipment, staff_user):
    # given: a shipment that has already been received
    shipment.arrived_at = timezone.now()
    shipment.save()

    # when/then: starting a receipt raises error
    with pytest.raises(ValueError, match="already marked as received"):
        start_receipt(shipment, user=staff_user)


def test_error_when_shipment_has_completed_receipt(
    shipment, staff_user, receipt_factory
):
    # given: a shipment with a completed receipt
    receipt_factory(shipment=shipment, status=ReceiptStatus.COMPLETED)

    # when/then: starting a new receipt raises error
    with pytest.raises(ValueError, match="already has a receipt"):
        start_receipt(shipment, user=staff_user)


# Tests for receive_item function


def test_receives_item_successfully(receipt, purchase_order_item, variant, staff_user):
    # given: an in-progress receipt and a POI with 0 received
    assert purchase_order_item.quantity_received == 0

    # when: receiving an item
    line = receive_item(receipt, variant, quantity=50, user=staff_user, notes="Test")

    # then: ReceiptLine created and POI updated
    assert line.receipt == receipt
    assert line.purchase_order_item == purchase_order_item
    assert line.quantity_received == 50
    assert line.received_by == staff_user
    assert line.notes == "Test"

    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_received == 50


def test_multiple_scans_increment_quantity(
    receipt, purchase_order_item, variant, staff_user
):
    # given: a POI that we scan multiple times
    receive_item(receipt, variant, quantity=30, user=staff_user)

    # when: scanning the same item again
    receive_item(receipt, variant, quantity=20, user=staff_user)

    # then: quantity_received is cumulative
    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_received == 50

    # and: two separate receipt lines exist
    assert receipt.lines.count() == 2


def test_error_when_receipt_not_in_progress(receipt, variant, staff_user):
    # given: a completed receipt
    receipt.status = ReceiptStatus.COMPLETED
    receipt.save()

    # when/then: trying to receive items raises error
    with pytest.raises(ReceiptNotInProgress):
        receive_item(receipt, variant, quantity=10, user=staff_user)


def test_error_when_variant_not_in_shipment(
    receipt, product_variant_factory, staff_user
):
    # given: a variant that is not part of this shipment
    other_variant = product_variant_factory()

    # when/then: trying to receive it raises error
    with pytest.raises(ValueError, match="not found in shipment"):
        receive_item(receipt, other_variant, quantity=10, user=staff_user)


def test_audit_trail_captured(receipt, purchase_order_item, variant, staff_user):
    # given/when: receiving an item
    before = timezone.now()
    line = receive_item(receipt, variant, quantity=10, user=staff_user)
    after = timezone.now()

    # then: audit fields are populated
    assert line.received_by == staff_user
    assert before <= line.received_at <= after


# Tests for complete_receipt function


def test_completes_receipt_with_no_discrepancies(
    receipt, purchase_order_item, staff_user, receipt_line_factory
):
    # given: a receipt where received == ordered
    purchase_order_item.quantity_ordered = 100
    purchase_order_item.save()

    # Create receipt line to simulate receiving 100 items
    receipt_line_factory(
        receipt=receipt,
        purchase_order_item=purchase_order_item,
        quantity_received=100,
        received_by=staff_user,
    )

    # when: completing the receipt
    result = complete_receipt(receipt, user=staff_user)

    # then: no adjustments created
    assert result["discrepancies"] == 0
    assert len(result["adjustments_created"]) == 0
    assert len(result["adjustments_pending"]) == 0

    # and: receipt is completed
    receipt.refresh_from_db()
    assert receipt.status == ReceiptStatus.COMPLETED
    assert receipt.completed_at is not None
    assert receipt.completed_by == staff_user

    # and: POI status updated
    purchase_order_item.refresh_from_db()
    assert purchase_order_item.status == "received"

    # and: shipment marked arrived
    assert receipt.shipment.arrived_at is not None


def test_creates_adjustment_for_delivery_short(
    receipt, purchase_order_item, staff_user, receipt_line_factory
):
    # given: received less than ordered
    purchase_order_item.quantity_ordered = 100
    purchase_order_item.save()

    # Create receipt line to simulate receiving 98 items (shortage of 2)
    receipt_line_factory(
        receipt=receipt,
        purchase_order_item=purchase_order_item,
        quantity_received=98,
        received_by=staff_user,
    )

    # when: completing the receipt
    result = complete_receipt(receipt, user=staff_user)

    # then: adjustment created for shortage
    assert result["discrepancies"] == 1
    assert len(result["adjustments_created"]) == 1

    adjustment = result["adjustments_created"][0]
    assert adjustment.quantity_change == -2
    assert adjustment.reason == "delivery_short"
    assert adjustment.affects_payable is True
    assert adjustment.processed_at is not None  # Auto-processed


def test_creates_adjustment_for_overage(
    receipt, purchase_order_item, staff_user, receipt_line_factory
):
    # given: received more than ordered
    purchase_order_item.quantity_ordered = 100
    purchase_order_item.save()

    # Create receipt line to simulate receiving 105 items (overage of 5)
    receipt_line_factory(
        receipt=receipt,
        purchase_order_item=purchase_order_item,
        quantity_received=105,
        received_by=staff_user,
    )

    # when: completing the receipt
    result = complete_receipt(receipt, user=staff_user)

    # then: adjustment created for overage
    assert result["discrepancies"] == 1
    adjustment = result["adjustments_created"][0]
    assert adjustment.quantity_change == 5
    assert adjustment.reason == "cycle_count_pos"
    assert adjustment.affects_payable is False


def test_handles_adjustment_affecting_confirmed_orders(
    receipt, purchase_order_item, staff_user, mocker, receipt_line_factory
):
    # given: a shortage that would affect confirmed orders
    purchase_order_item.quantity_ordered = 100
    purchase_order_item.save()

    # Create receipt line to simulate receiving 90 items (shortage of 10)
    receipt_line_factory(
        receipt=receipt,
        purchase_order_item=purchase_order_item,
        quantity_received=90,
        received_by=staff_user,
    )

    # and: process_adjustment will raise AdjustmentRequiresManualResolution
    from ...inventory.exceptions import AdjustmentRequiresManualResolution

    _mock_process = mocker.patch(
        "saleor.inventory.stock_management.process_adjustment",
        side_effect=AdjustmentRequiresManualResolution(
            adjustment=mocker.Mock(), order_numbers=[1234]
        ),
    )

    # when: completing the receipt
    result = complete_receipt(receipt, user=staff_user)

    # then: adjustment created but NOT processed
    assert result["discrepancies"] == 1
    assert len(result["adjustments_pending"]) == 1
    assert len(result["adjustments_created"]) == 0

    adjustment = result["adjustments_pending"][0]
    assert adjustment.processed_at is None


def test_sends_notification_for_pending_adjustments(
    receipt, purchase_order_item, staff_user, mocker, receipt_line_factory
):
    # given: a shortage affecting confirmed orders
    purchase_order_item.quantity_ordered = 100
    purchase_order_item.save()

    # Create receipt line to simulate receiving 90 items (shortage of 10)
    receipt_line_factory(
        receipt=receipt,
        purchase_order_item=purchase_order_item,
        quantity_received=90,
        received_by=staff_user,
    )

    from ...inventory.exceptions import AdjustmentRequiresManualResolution

    mocker.patch(
        "saleor.inventory.stock_management.process_adjustment",
        side_effect=AdjustmentRequiresManualResolution(
            adjustment=mocker.Mock(), order_numbers=[1234]
        ),
    )

    # and: a plugin manager
    mock_manager = mocker.Mock()

    # when: completing the receipt
    complete_receipt(receipt, user=staff_user, manager=mock_manager)

    # then: notification sent
    mock_manager.notify.assert_called_once()
    call_args = mock_manager.notify.call_args
    assert call_args[0][0] == "pending_adjustments"


def test_error_when_completing_receipt_not_in_progress(receipt, staff_user):
    # given: a completed receipt
    receipt.status = ReceiptStatus.COMPLETED
    receipt.save()

    # when/then: trying to complete again raises error
    with pytest.raises(ValueError, match="not in progress"):
        complete_receipt(receipt, user=staff_user)


# Tests for delete_receipt function


def test_deletes_receipt_and_reverts_quantities(
    receipt, purchase_order_item, variant, staff_user
):
    # given: a receipt with received items
    receive_item(receipt, variant, quantity=50, user=staff_user)
    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_received == 50

    # when: deleting the receipt
    delete_receipt(receipt)

    # then: receipt is deleted
    from ...inventory.models import Receipt

    assert not Receipt.objects.filter(id=receipt.id).exists()

    # and: POI quantity reverted
    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_received == 0


def test_error_when_receipt_completed(receipt, staff_user):
    # given: a completed receipt
    receipt.status = ReceiptStatus.COMPLETED
    receipt.save()

    # when/then: deleting raises error
    with pytest.raises(ReceiptNotInProgress):
        delete_receipt(receipt)


# Tests for delete_receipt_line function


def test_deletes_line_and_reverts_quantity(
    receipt, purchase_order_item, variant, staff_user
):
    # given: a receipt line
    line = receive_item(receipt, variant, quantity=50, user=staff_user)
    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_received == 50

    # when: deleting the line
    delete_receipt_line(line)

    # then: line is deleted
    from ...inventory.models import ReceiptLine

    assert not ReceiptLine.objects.filter(id=line.id).exists()

    # and: POI quantity reverted
    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_received == 0


def test_only_reverts_deleted_line_quantity(
    receipt, purchase_order_item, variant, staff_user
):
    # given: multiple lines for same POI
    line1 = receive_item(receipt, variant, quantity=30, user=staff_user)
    line2 = receive_item(receipt, variant, quantity=20, user=staff_user)
    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_received == 50

    # when: deleting only line1
    delete_receipt_line(line1)

    # then: only line1's quantity reverted
    purchase_order_item.refresh_from_db()
    assert purchase_order_item.quantity_received == 20

    # and: line2 still exists
    from ...inventory.models import ReceiptLine

    assert ReceiptLine.objects.filter(id=line2.id).exists()


def test_error_when_receipt_line_completed(
    receipt, purchase_order_item, variant, staff_user
):
    # given: a line from a completed receipt
    line = receive_item(receipt, variant, quantity=50, user=staff_user)
    receipt.status = ReceiptStatus.COMPLETED
    receipt.save()

    # when/then: deleting the line raises error
    with pytest.raises(ReceiptLineNotInProgress):
        delete_receipt_line(line)


# Tests for fulfillment creation via complete_receipt


@pytest.fixture
def order_with_poi_and_receipt(
    order,
    nonowned_warehouse,
    owned_warehouse,
    purchase_order,
    variant,
    shipment,
    staff_user,
):
    """Scenario: UNCONFIRMED order → POI confirmed → in-progress receipt.

    Sets up an order with an allocation at nonowned_warehouse and a POI linked
    to the shipment. POI is NOT yet confirmed - the test controls that step.
    """
    order.status = OrderStatus.UNCONFIRMED
    order.save()

    line = order.lines.create(
        product_name=variant.product.name,
        variant_name=variant.name,
        product_sku=variant.sku,
        is_shipping_required=True,
        is_gift_card=False,
        quantity=5,
        variant=variant,
        unit_price_net_amount=10,
        unit_price_gross_amount=10,
        total_price_net_amount=50,
        total_price_gross_amount=50,
        undiscounted_unit_price_net_amount=10,
        undiscounted_unit_price_gross_amount=10,
        undiscounted_total_price_net_amount=50,
        undiscounted_total_price_gross_amount=50,
        currency="USD",
        tax_rate=0,
    )

    source_stock, _ = Stock.objects.get_or_create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        defaults={"quantity": 100, "quantity_allocated": 0},
    )

    Allocation.objects.create(
        order_line=line,
        stock=source_stock,
        quantity_allocated=5,
    )
    source_stock.quantity_allocated = 5
    source_stock.save()

    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=5,
        total_price_amount=50.0,
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
    )

    receipt = Receipt.objects.create(
        shipment=shipment,
        status=ReceiptStatus.IN_PROGRESS,
        created_by=staff_user,
    )

    return {"order": order, "line": line, "poi": poi, "receipt": receipt}


def test_confirm_poi_does_not_create_fulfillments_for_order(
    order_with_poi_and_receipt, staff_user
):
    # given
    order = order_with_poi_and_receipt["order"]
    poi = order_with_poi_and_receipt["poi"]

    assert Fulfillment.objects.filter(order=order).count() == 0

    # when
    confirm_purchase_order_item(poi, user=staff_user)

    # then
    order.refresh_from_db()
    assert order.status == OrderStatus.UNFULFILLED
    assert Fulfillment.objects.filter(order=order).count() == 0


def test_complete_receipt_creates_fulfillments(
    order_with_poi_and_receipt, staff_user
):
    # given
    order = order_with_poi_and_receipt["order"]
    poi = order_with_poi_and_receipt["poi"]
    receipt = order_with_poi_and_receipt["receipt"]

    confirm_purchase_order_item(poi, user=staff_user)

    order.refresh_from_db()
    assert order.status == OrderStatus.UNFULFILLED
    assert Fulfillment.objects.filter(order=order).count() == 0

    ReceiptLine.objects.create(
        receipt=receipt,
        purchase_order_item=poi,
        quantity_received=5,
    )

    # when
    complete_receipt(receipt, user=staff_user)

    # then
    fulfillments = Fulfillment.objects.filter(order=order)
    assert fulfillments.count() == 1
    assert fulfillments.first().status == FulfillmentStatus.WAITING_FOR_APPROVAL


def test_complete_receipt_with_no_linked_orders_creates_no_fulfillments(
    receipt, purchase_order_item, staff_user, receipt_line_factory
):
    # given: POI has no order allocations (standalone stock receipt)
    purchase_order_item.quantity_ordered = 10
    purchase_order_item.save()

    receipt_line_factory(
        receipt=receipt,
        purchase_order_item=purchase_order_item,
        quantity_received=10,
        received_by=staff_user,
    )

    # when
    complete_receipt(receipt, user=staff_user)

    # then: no fulfillments since no orders are linked to this stock
    assert Fulfillment.objects.count() == 0
