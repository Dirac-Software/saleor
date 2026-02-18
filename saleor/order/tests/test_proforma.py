from decimal import Decimal
from unittest.mock import Mock

from ..proforma import (
    calculate_deposit_allocation,
    calculate_fulfillment_total,
)


def test_calculate_deposit_allocation_no_deposit():
    order = Mock()
    order.deposit_required = False
    order.total_deposit_paid = Decimal(0)

    result = calculate_deposit_allocation(order, Decimal(100))

    assert result == Decimal(0)


def test_calculate_deposit_allocation_zero_order_total():
    order = Mock()
    order.deposit_required = True
    order.total_deposit_paid = Decimal(50)
    order.total_gross_amount = Decimal(0)

    result = calculate_deposit_allocation(order, Decimal(100))

    assert result == Decimal(0)


def test_calculate_deposit_allocation_simple():
    order = Mock()
    order.deposit_required = True
    order.total_deposit_paid = Decimal(100)
    order.total_gross_amount = Decimal(200)
    order.fulfillments.all.return_value = []

    fulfillment_total = Decimal(50)
    result = calculate_deposit_allocation(order, fulfillment_total)

    assert result == Decimal(25)


def test_calculate_deposit_allocation_fifo_with_already_allocated():
    order = Mock()
    order.deposit_required = True
    order.total_deposit_paid = Decimal(100)
    order.total_gross_amount = Decimal(300)

    existing_fulfillment = Mock()
    existing_fulfillment.deposit_allocated_amount = Decimal(40)
    order.fulfillments.all.return_value = [existing_fulfillment]

    fulfillment_total = Decimal(100)
    result = calculate_deposit_allocation(order, fulfillment_total)

    remaining_deposit = Decimal(100) - Decimal(40)
    proportional_share = Decimal(100) * (Decimal(100) / Decimal(300))
    expected = min(remaining_deposit, proportional_share)

    assert result == expected


def test_calculate_deposit_allocation_exceeds_remaining():
    order = Mock()
    order.deposit_required = True
    order.total_deposit_paid = Decimal(100)
    order.total_gross_amount = Decimal(200)

    existing_fulfillment = Mock()
    existing_fulfillment.deposit_allocated_amount = Decimal(90)
    order.fulfillments.all.return_value = [existing_fulfillment]

    fulfillment_total = Decimal(100)
    result = calculate_deposit_allocation(order, fulfillment_total)

    assert result == Decimal(10)


def test_calculate_fulfillment_total():
    line1 = Mock()
    line1.order_line.unit_price_gross_amount = Decimal(10)
    line1.quantity = 2

    line2 = Mock()
    line2.order_line.unit_price_gross_amount = Decimal(25)
    line2.quantity = 3

    fulfillment = Mock()
    fulfillment.lines.all.return_value = [line1, line2]

    result = calculate_fulfillment_total(fulfillment)

    assert result == Decimal(95)
