from decimal import Decimal
from unittest.mock import Mock

from ..proforma import (
    calculate_deposit_allocation,
    calculate_fulfillment_total,
    calculate_proportional_shipping,
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


def test_calculate_proportional_shipping_full_fulfillment():
    # Single fulfillment covers all lines: gets the full shipping amount.
    result = calculate_proportional_shipping(
        shipping_amount=Decimal("12.20"),
        fulfillment_lines_total=Decimal("112.92"),
        order_lines_total=Decimal("112.92"),
    )
    assert result == Decimal("12.20")


def test_calculate_proportional_shipping_partial_fulfillment():
    # 2 of 3 identical products: fulfillment gets 2/3 of shipping.
    result = calculate_proportional_shipping(
        shipping_amount=Decimal("12.00"),
        fulfillment_lines_total=Decimal("75.28"),
        order_lines_total=Decimal("112.92"),
    )
    # 12.00 * (75.28 / 112.92) ≈ 7.9928...
    assert result == Decimal("12.00") * Decimal("75.28") / Decimal("112.92")


def test_calculate_proportional_shipping_zero_order_lines():
    result = calculate_proportional_shipping(
        shipping_amount=Decimal("12.20"),
        fulfillment_lines_total=Decimal(0),
        order_lines_total=Decimal(0),
    )
    assert result == Decimal(0)


def test_calculate_proportional_shipping_two_partial_fulfillments_sum_to_full():
    # Proportional shares must sum to the full shipping amount.
    shipping = Decimal("12.00")
    order_lines_total = Decimal("112.92")
    fulfillment1_lines = Decimal("75.28")  # 2/3 of order
    fulfillment2_lines = Decimal("37.64")  # 1/3 of order

    share1 = calculate_proportional_shipping(
        shipping, fulfillment1_lines, order_lines_total
    )
    share2 = calculate_proportional_shipping(
        shipping, fulfillment2_lines, order_lines_total
    )

    # Sum should equal full shipping (within floating-point tolerance)
    assert share1 + share2 == shipping


def test_deposit_allocation_with_proportional_shipping_two_partial_fulfillments():
    # End-to-end: verify deposit is fully and correctly allocated across two partial
    # fulfillments when shipping is split proportionally.
    #
    # Order: 3 × £37.64 products = £112.92 lines, £12.20 shipping = £125.12 total
    # Deposit: 50% = £62.56
    order = Mock()
    order.deposit_required = True
    order.total_deposit_paid = Decimal("62.56")
    order.total_gross_amount = Decimal("125.12")

    shipping = Decimal("12.20")
    order_lines_total = Decimal("112.92")

    # --- Fulfillment 1: 2 products ---
    order.fulfillments.all.return_value = []
    f1_lines = Decimal("75.28")
    prop_shipping_1 = calculate_proportional_shipping(
        shipping, f1_lines, order_lines_total
    )
    deposit_1 = calculate_deposit_allocation(order, f1_lines + prop_shipping_1)

    f1 = Mock()
    f1.deposit_allocated_amount = deposit_1
    order.fulfillments.all.return_value = [f1]

    # --- Fulfillment 2: 1 product ---
    f2_lines = Decimal("37.64")
    prop_shipping_2 = calculate_proportional_shipping(
        shipping, f2_lines, order_lines_total
    )
    deposit_2 = calculate_deposit_allocation(order, f2_lines + prop_shipping_2)

    # Full deposit is consumed
    assert deposit_1 + deposit_2 == Decimal("62.56")

    # Proforma amounts sum to order_total - deposit
    proforma_1 = f1_lines + prop_shipping_1.quantize(Decimal("0.01")) - deposit_1
    proforma_2 = f2_lines + prop_shipping_2.quantize(Decimal("0.01")) - deposit_2
    expected_total_proforma = Decimal("125.12") - Decimal("62.56")
    assert (proforma_1 + proforma_2).quantize(
        Decimal("0.01")
    ) == expected_total_proforma


def test_deposit_allocation_with_proportional_shipping_three_partial_fulfillments():
    # Three equal-value fulfillments: each should get 1/3 of shipping and 1/3 of deposit.
    order = Mock()
    order.deposit_required = True
    order.total_deposit_paid = Decimal("30.00")
    order.total_gross_amount = Decimal("120.00")  # 90 lines + 30 shipping

    shipping = Decimal("30.00")
    order_lines_total = Decimal("90.00")  # 3 × £30

    allocated = []
    for _i in range(3):
        order.fulfillments.all.return_value = [
            Mock(deposit_allocated_amount=d) for d in allocated
        ]
        f_lines = Decimal("30.00")
        prop_shipping = calculate_proportional_shipping(
            shipping, f_lines, order_lines_total
        )
        deposit = calculate_deposit_allocation(order, f_lines + prop_shipping)
        allocated.append(deposit)

    assert sum(allocated) == Decimal("30.00")
    # Each fulfillment's proforma = 30 + 10 - 10 = 30
    for deposit in allocated:
        assert deposit == Decimal("10.00")


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
