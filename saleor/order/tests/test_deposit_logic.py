from decimal import Decimal

import pytest
from django.utils import timezone

from ...payment import ChargeStatus, CustomPaymentChoices
from ...payment.models import Payment


def create_xero_deposit_payment(order, amount, payment_id=None):
    """Create a Xero deposit payment for testing."""
    if payment_id is None:
        payment_id = f"XERO-{timezone.now().timestamp()}"

    return Payment.objects.create(
        order=order,
        gateway=CustomPaymentChoices.XERO,
        psp_reference=payment_id,
        total=amount,
        captured_amount=amount,
        charge_status=ChargeStatus.FULLY_CHARGED,
        currency=order.currency,
        is_active=True,
    )


@pytest.mark.parametrize(
    ("deposit_payments", "allocated_amounts", "expected_remaining"),
    [
        ([], [], Decimal(0)),
        ([Decimal(100)], [], Decimal(100)),
        ([Decimal(100)], [Decimal(30)], Decimal(70)),
        ([Decimal(100), Decimal(50)], [Decimal(30), Decimal(40)], Decimal(80)),
        ([Decimal(100)], [Decimal(100)], Decimal(0)),
        ([Decimal(50), Decimal(50)], [Decimal(100)], Decimal(0)),
    ],
)
def test_order_get_remaining_deposit(
    order_with_lines, deposit_payments, allocated_amounts, expected_remaining
):
    order = order_with_lines
    order.deposit_required = True
    order.save()

    for amount in deposit_payments:
        create_xero_deposit_payment(order, amount)

    for amount in allocated_amounts:
        fulfillment = order.fulfillments.create(status="WAITING_FOR_APPROVAL")
        fulfillment.deposit_allocated_amount = amount
        fulfillment.save()

    remaining = order.get_remaining_deposit()
    assert remaining == expected_remaining


def test_total_deposit_paid_with_no_payments(order_with_lines):
    order = order_with_lines
    order.deposit_required = True
    order.save()

    assert order.total_deposit_paid == Decimal(0)


def test_total_deposit_paid_with_xero_payments(order_with_lines):
    order = order_with_lines
    order.deposit_required = True
    order.save()

    create_xero_deposit_payment(order, Decimal("100.00"), "PAY-001")
    create_xero_deposit_payment(order, Decimal("150.00"), "PAY-002")

    assert order.total_deposit_paid == Decimal("250.00")


def test_total_deposit_paid_ignores_non_xero_payments(order_with_lines):
    order = order_with_lines
    order.deposit_required = True
    order.save()

    create_xero_deposit_payment(order, Decimal("100.00"), "PAY-001")

    Payment.objects.create(
        order=order,
        gateway="manual",
        total=Decimal("50.00"),
        captured_amount=Decimal("50.00"),
        charge_status=ChargeStatus.FULLY_CHARGED,
        currency=order.currency,
        is_active=True,
    )

    assert order.total_deposit_paid == Decimal("100.00")


def test_total_deposit_paid_ignores_inactive_payments(order_with_lines):
    order = order_with_lines
    order.deposit_required = True
    order.save()

    create_xero_deposit_payment(order, Decimal("100.00"), "PAY-001")

    Payment.objects.create(
        order=order,
        gateway=CustomPaymentChoices.XERO,
        psp_reference="PAY-INACTIVE",
        total=Decimal("50.00"),
        captured_amount=Decimal("50.00"),
        charge_status=ChargeStatus.FULLY_CHARGED,
        currency=order.currency,
        is_active=False,
    )

    assert order.total_deposit_paid == Decimal("100.00")


@pytest.mark.parametrize(
    ("total", "percentage", "payments", "expected_met"),
    [
        (Decimal(1000), Decimal(30), [Decimal(300)], True),
        (Decimal(1000), Decimal(30), [Decimal(299)], False),
        (Decimal(1000), Decimal(30), [Decimal(150), Decimal(150)], True),
        (Decimal(1000), Decimal(30), [Decimal(100), Decimal(100)], False),
        (Decimal(1000), Decimal(50), [Decimal(500)], True),
        (Decimal(1000), Decimal(50), [Decimal("499.99")], False),
    ],
)
def test_deposit_threshold_met(
    order_with_lines, total, percentage, payments, expected_met
):
    order = order_with_lines
    order.deposit_required = True
    order.deposit_percentage = percentage
    order.total_gross_amount = total
    order.save()

    for amount in payments:
        create_xero_deposit_payment(order, amount)

    assert order.deposit_threshold_met == expected_met


def test_deposit_threshold_met_when_not_required(order_with_lines):
    order = order_with_lines
    order.deposit_required = False
    order.save()

    assert order.deposit_threshold_met is True


def test_deposit_threshold_met_when_no_percentage(order_with_lines):
    order = order_with_lines
    order.deposit_required = True
    order.deposit_percentage = None
    order.save()

    create_xero_deposit_payment(order, Decimal("100.00"))

    assert order.deposit_threshold_met is False


@pytest.mark.parametrize(
    (
        "has_pick",
        "pick_status",
        "has_shipment",
        "has_sufficient_payment",
        "fulfillment_status",
        "expected",
    ),
    [
        (True, "completed", True, True, "waiting_for_approval", True),
        (True, "not_started", True, True, "waiting_for_approval", False),
        (True, "completed", False, True, "waiting_for_approval", False),
        (True, "completed", True, False, "waiting_for_approval", True),
        (True, "completed", True, True, "fulfilled", False),
        (False, None, True, True, "waiting_for_approval", False),
    ],
)
def test_fulfillment_can_auto_transition(
    order_with_lines,
    has_pick,
    pick_status,
    has_shipment,
    has_sufficient_payment,
    fulfillment_status,
    expected,
):
    from ..models import FulfillmentLine, Pick

    order = order_with_lines
    order.deposit_required = False
    order.save()

    fulfillment = order.fulfillments.create(status=fulfillment_status)

    if not has_sufficient_payment:
        # Add a line so the fulfillment has a non-zero total, but no payment,
        # causing total_deposit_paid (0) < fulfillment_total (> 0).
        line = order.lines.first()
        FulfillmentLine.objects.create(
            fulfillment=fulfillment, order_line=line, quantity=1
        )

    if has_pick:
        Pick.objects.create(
            fulfillment=fulfillment, status=pick_status, created_by=None
        )
        fulfillment.refresh_from_db()

    if has_shipment:
        fulfillment.shipment_id = 123

    result = fulfillment.can_auto_transition_to_fulfilled()
    assert result == expected
