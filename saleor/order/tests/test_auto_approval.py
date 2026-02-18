from decimal import Decimal

import pytest
from django.utils import timezone

from ...payment import ChargeStatus, CustomPaymentChoices
from ...payment.models import Payment
from ...shipping.models import Shipment
from .. import FulfillmentStatus, PickStatus
from ..actions import (
    assign_shipment_to_fulfillment,
    auto_create_pick_for_fulfillment,
    complete_pick,
    start_pick,
    update_pick_item,
)


def _create_xero_payment(order, amount):
    return Payment.objects.create(
        order=order,
        gateway=CustomPaymentChoices.XERO,
        psp_reference=f"TEST-{amount}",
        total=amount,
        captured_amount=amount,
        charge_status=ChargeStatus.FULLY_CHARGED,
        currency=order.currency,
        is_active=True,
    )


@pytest.fixture
def shipment_for_fulfillment(warehouse):
    from ...shipping import ShipmentType

    return Shipment.objects.create(
        source=warehouse.address,
        destination=warehouse.address,
        tracking_url="AUTO-APPROVE-TEST",
        shipping_cost_amount=Decimal("50.00"),
        currency="USD",
        inco_term="DDP",
        carrier="TEST-CARRIER",
        departed_at=timezone.now(),
        shipment_type=ShipmentType.OUTBOUND,
    )


def test_auto_approve_when_pick_completed_last(
    full_fulfillment_awaiting_approval,
    shipment_for_fulfillment,
    staff_user,
):
    fulfillment = full_fulfillment_awaiting_approval
    _create_xero_payment(fulfillment.order, Decimal("99999.00"))

    fulfillment.shipment = shipment_for_fulfillment
    fulfillment.save(update_fields=["shipment"])

    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)
    start_pick(pick, user=staff_user)
    for pick_item in pick.items.all():
        update_pick_item(
            pick_item, quantity_picked=pick_item.quantity_to_pick, user=staff_user
        )

    assert fulfillment.status == FulfillmentStatus.WAITING_FOR_APPROVAL

    complete_pick(pick, user=staff_user, auto_approve=True)

    fulfillment.refresh_from_db()
    assert fulfillment.status == FulfillmentStatus.FULFILLED


def test_auto_approve_when_shipment_assigned_last(
    full_fulfillment_awaiting_approval,
    shipment_for_fulfillment,
    staff_user,
):
    fulfillment = full_fulfillment_awaiting_approval
    _create_xero_payment(fulfillment.order, Decimal("99999.00"))

    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)
    start_pick(pick, user=staff_user)
    for pick_item in pick.items.all():
        update_pick_item(
            pick_item, quantity_picked=pick_item.quantity_to_pick, user=staff_user
        )
    complete_pick(pick, user=staff_user)

    fulfillment.refresh_from_db()
    assert fulfillment.status == FulfillmentStatus.WAITING_FOR_APPROVAL

    assign_shipment_to_fulfillment(
        fulfillment, shipment_for_fulfillment, staff_user, auto_approve=True
    )

    fulfillment.refresh_from_db()
    assert fulfillment.status == FulfillmentStatus.FULFILLED


def test_no_auto_approve_when_pick_not_completed(
    full_fulfillment_awaiting_approval,
    shipment_for_fulfillment,
    staff_user,
):
    fulfillment = full_fulfillment_awaiting_approval

    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)
    start_pick(pick, user=staff_user)

    assign_shipment_to_fulfillment(
        fulfillment, shipment_for_fulfillment, staff_user, auto_approve=True
    )

    fulfillment.refresh_from_db()
    assert fulfillment.status == FulfillmentStatus.WAITING_FOR_APPROVAL
    assert pick.status == PickStatus.IN_PROGRESS


def test_no_auto_approve_when_no_shipment(
    full_fulfillment_awaiting_approval,
    staff_user,
):
    fulfillment = full_fulfillment_awaiting_approval

    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)
    start_pick(pick, user=staff_user)
    for pick_item in pick.items.all():
        update_pick_item(
            pick_item, quantity_picked=pick_item.quantity_to_pick, user=staff_user
        )
    complete_pick(pick, user=staff_user, auto_approve=True)

    fulfillment.refresh_from_db()
    assert fulfillment.status == FulfillmentStatus.WAITING_FOR_APPROVAL
    assert fulfillment.shipment is None


def test_no_auto_approve_when_pick_does_not_exist(
    full_fulfillment_awaiting_approval,
    shipment_for_fulfillment,
    staff_user,
):
    fulfillment = full_fulfillment_awaiting_approval

    assign_shipment_to_fulfillment(
        fulfillment, shipment_for_fulfillment, staff_user, auto_approve=True
    )

    fulfillment.refresh_from_db()
    assert fulfillment.status == FulfillmentStatus.WAITING_FOR_APPROVAL


def test_no_auto_approve_when_already_fulfilled(
    full_fulfillment_awaiting_approval,
    shipment_for_fulfillment,
    staff_user,
):
    fulfillment = full_fulfillment_awaiting_approval
    fulfillment.status = FulfillmentStatus.FULFILLED
    fulfillment.save(update_fields=["status"])

    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)
    start_pick(pick, user=staff_user)
    for pick_item in pick.items.all():
        update_pick_item(
            pick_item, quantity_picked=pick_item.quantity_to_pick, user=staff_user
        )
    complete_pick(pick, user=staff_user, auto_approve=True)

    fulfillment.refresh_from_db()
    assert fulfillment.status == FulfillmentStatus.FULFILLED


def test_no_auto_approve_when_payments_insufficient(
    full_fulfillment_awaiting_approval,
    shipment_for_fulfillment,
    staff_user,
):
    fulfillment = full_fulfillment_awaiting_approval
    # No Xero payment created — total_deposit_paid=0 < fulfillment total > 0

    fulfillment.shipment = shipment_for_fulfillment
    fulfillment.save(update_fields=["shipment"])

    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)
    start_pick(pick, user=staff_user)
    for pick_item in pick.items.all():
        update_pick_item(
            pick_item, quantity_picked=pick_item.quantity_to_pick, user=staff_user
        )
    complete_pick(pick, user=staff_user, auto_approve=True)

    fulfillment.refresh_from_db()
    assert fulfillment.status == FulfillmentStatus.WAITING_FOR_APPROVAL


def test_no_auto_approve_when_deposit_not_allocated(
    full_fulfillment_awaiting_approval,
    shipment_for_fulfillment,
    staff_user,
):
    fulfillment = full_fulfillment_awaiting_approval
    order = fulfillment.order

    order.deposit_required = True
    order.save(update_fields=["deposit_required"])

    fulfillment.deposit_allocated_amount = Decimal(0)
    fulfillment.save(update_fields=["deposit_allocated_amount"])

    fulfillment.shipment = shipment_for_fulfillment
    fulfillment.save(update_fields=["shipment"])

    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)
    start_pick(pick, user=staff_user)
    for pick_item in pick.items.all():
        update_pick_item(
            pick_item, quantity_picked=pick_item.quantity_to_pick, user=staff_user
        )
    complete_pick(pick, user=staff_user, auto_approve=True)

    fulfillment.refresh_from_db()
    assert fulfillment.status == FulfillmentStatus.WAITING_FOR_APPROVAL


def test_auto_approve_when_all_conditions_met_with_deposit(
    full_fulfillment_awaiting_approval,
    shipment_for_fulfillment,
    staff_user,
):
    fulfillment = full_fulfillment_awaiting_approval
    order = fulfillment.order

    order.deposit_required = True
    order.save(update_fields=["deposit_required"])

    _create_xero_payment(order, Decimal("99999.00"))

    fulfillment.deposit_allocated_amount = Decimal("100.00")
    fulfillment.save(update_fields=["deposit_allocated_amount"])

    fulfillment.shipment = shipment_for_fulfillment
    fulfillment.save(update_fields=["shipment"])

    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)
    start_pick(pick, user=staff_user)
    for pick_item in pick.items.all():
        update_pick_item(
            pick_item, quantity_picked=pick_item.quantity_to_pick, user=staff_user
        )
    complete_pick(pick, user=staff_user, auto_approve=True)

    fulfillment.refresh_from_db()
    assert fulfillment.status == FulfillmentStatus.FULFILLED
