"""Tests for unit price calculations on PurchaseOrderItem."""

from decimal import Decimal

import pytest

from .. import PurchaseOrderItemAdjustmentReason
from ..models import PurchaseOrderItemAdjustment


@pytest.fixture
def purchase_order_item_with_price(purchase_order_item):
    """POI with known pricing: 10 units @ $100 total ($10/unit)."""
    purchase_order_item.quantity_ordered = 10
    purchase_order_item.total_price_amount = Decimal("100.00")
    purchase_order_item.currency = "USD"
    purchase_order_item.save()
    return purchase_order_item


def test_unit_price_amount_base_case(purchase_order_item_with_price):
    """Unit price equals invoice unit price with no adjustments."""
    # given: POI with no adjustments
    poi = purchase_order_item_with_price

    # when: getting unit price
    unit_price = poi.unit_price_amount

    # then: equals original invoice unit price
    assert unit_price == Decimal("10.00")


def test_unit_price_amount_with_payable_adjustment(
    purchase_order_item_with_price, staff_user
):
    """Payable adjustments keep unit price constant (both cost and quantity adjust)."""
    # given: POI with delivery short (supplier credits us)
    poi = purchase_order_item_with_price

    PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-2,  # Short 2 units
        reason=PurchaseOrderItemAdjustmentReason.DELIVERY_SHORT,
        affects_payable=True,
        processed_at=staff_user.date_joined,  # Mark as processed
        created_by=staff_user,
    )

    # when: getting unit price
    unit_price = poi.unit_price_amount

    # then: unit price remains constant
    # Cost: $100 - (2 × $10) = $80
    # Quantity: 10 - 2 = 8
    # Unit price: $80 / 8 = $10
    assert unit_price == Decimal("10.00")


def test_unit_price_amount_with_non_payable_adjustment(
    purchase_order_item_with_price, staff_user
):
    """Non-payable adjustments increase unit price (only quantity decreases)."""
    # given: POI with shrinkage (we eat the loss)
    poi = purchase_order_item_with_price

    PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-2,  # Lost 2 units
        reason=PurchaseOrderItemAdjustmentReason.SHRINKAGE,
        affects_payable=False,  # We eat the loss
        processed_at=staff_user.date_joined,
        created_by=staff_user,
    )

    # when: getting unit price
    unit_price = poi.unit_price_amount

    # then: unit price increases
    # Cost: $100 (no change, we still paid $100)
    # Quantity: 10 - 2 = 8
    # Unit price: $100 / 8 = $12.50
    assert unit_price == Decimal("12.50")


def test_unit_price_amount_with_multiple_payable_adjustments(
    purchase_order_item_with_price, staff_user
):
    """Multiple payable adjustments compound correctly."""
    # given: POI with multiple delivery shorts
    poi = purchase_order_item_with_price

    PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-2,
        reason=PurchaseOrderItemAdjustmentReason.DELIVERY_SHORT,
        affects_payable=True,
        processed_at=staff_user.date_joined,
        created_by=staff_user,
    )

    PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-1,
        reason=PurchaseOrderItemAdjustmentReason.DELIVERY_SHORT,
        affects_payable=True,
        processed_at=staff_user.date_joined,
        created_by=staff_user,
    )

    # when: getting unit price
    unit_price = poi.unit_price_amount

    # then: unit price remains constant
    # Cost: $100 - (3 × $10) = $70
    # Quantity: 10 - 3 = 7
    # Unit price: $70 / 7 = $10
    assert unit_price == Decimal("10.00")


def test_unit_price_amount_with_multiple_non_payable_adjustments(
    purchase_order_item_with_price, staff_user
):
    """Multiple non-payable adjustments compound correctly."""
    # given: POI with multiple losses
    poi = purchase_order_item_with_price

    PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-2,
        reason=PurchaseOrderItemAdjustmentReason.SHRINKAGE,
        affects_payable=False,
        processed_at=staff_user.date_joined,
        created_by=staff_user,
    )

    PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-1,
        reason=PurchaseOrderItemAdjustmentReason.DAMAGE,
        affects_payable=False,
        processed_at=staff_user.date_joined,
        created_by=staff_user,
    )

    # when: getting unit price
    unit_price = poi.unit_price_amount

    # then: unit price increases significantly
    # Cost: $100 (no change)
    # Quantity: 10 - 3 = 7
    # Unit price: $100 / 7 ≈ $14.29
    assert unit_price == Decimal("100.00") / Decimal(7)


def test_unit_price_amount_with_mixed_adjustments(
    purchase_order_item_with_price, staff_user
):
    """Mix of payable and non-payable adjustments."""
    # given: POI with both types of adjustments
    poi = purchase_order_item_with_price

    # Delivery short (affects payable)
    PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-2,
        reason=PurchaseOrderItemAdjustmentReason.DELIVERY_SHORT,
        affects_payable=True,
        processed_at=staff_user.date_joined,
        created_by=staff_user,
    )

    # Shrinkage (doesn't affect payable)
    PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-1,
        reason=PurchaseOrderItemAdjustmentReason.SHRINKAGE,
        affects_payable=False,
        processed_at=staff_user.date_joined,
        created_by=staff_user,
    )

    # when: getting unit price
    unit_price = poi.unit_price_amount

    # then: correct calculation
    # Cost: $100 - (2 × $10) = $80 (only payable adjustment)
    # Quantity: 10 - 2 - 1 = 7 (both adjustments)
    # Unit price: $80 / 7 ≈ $11.43
    expected = Decimal("80.00") / Decimal(7)
    assert abs(unit_price - expected) < Decimal("0.01")


def test_unit_price_amount_ignores_unprocessed_adjustments(
    purchase_order_item_with_price, staff_user
):
    """Unprocessed adjustments don't affect unit price."""
    # given: POI with unprocessed adjustment
    poi = purchase_order_item_with_price

    PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-2,
        reason=PurchaseOrderItemAdjustmentReason.SHRINKAGE,
        affects_payable=False,
        processed_at=None,  # Not processed yet
        created_by=staff_user,
    )

    # when: getting unit price
    unit_price = poi.unit_price_amount

    # then: adjustment ignored, original price returned
    assert unit_price == Decimal("10.00")


def test_unit_price_amount_with_positive_adjustment(
    purchase_order_item_with_price, staff_user
):
    """Positive adjustments (overages) work correctly."""
    # given: POI with overage
    poi = purchase_order_item_with_price

    PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=5,  # Received 5 extra
        reason=PurchaseOrderItemAdjustmentReason.CYCLE_COUNT_POS,
        affects_payable=False,  # Free bonus units
        processed_at=staff_user.date_joined,
        created_by=staff_user,
    )

    # when: getting unit price
    unit_price = poi.unit_price_amount

    # then: unit price decreases
    # Cost: $100 (no change)
    # Quantity: 10 + 5 = 15
    # Unit price: $100 / 15 ≈ $6.67
    expected = Decimal("100.00") / Decimal(15)
    assert abs(unit_price - expected) < Decimal("0.01")


def test_unit_price_amount_zero_quantity_ordered(purchase_order_item):
    """Handles edge case of zero quantity ordered."""
    # given: POI with zero quantity
    purchase_order_item.quantity_ordered = 0
    purchase_order_item.total_price_amount = Decimal("100.00")
    purchase_order_item.save()

    # when: getting unit price
    unit_price = purchase_order_item.unit_price_amount

    # then: returns zero (not division by zero error)
    assert unit_price == 0


def test_unit_price_amount_all_units_lost(purchase_order_item_with_price, staff_user):
    """Handles edge case where all units are lost."""
    # given: POI where all units are lost
    poi = purchase_order_item_with_price

    PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-10,  # All 10 units lost
        reason=PurchaseOrderItemAdjustmentReason.SHRINKAGE,
        affects_payable=False,
        processed_at=staff_user.date_joined,
        created_by=staff_user,
    )

    # when: getting unit price
    unit_price = poi.unit_price_amount

    # then: returns original unit price (fallback)
    assert unit_price == Decimal("10.00")


def test_financial_impact_uses_original_unit_price(
    purchase_order_item_with_price, staff_user
):
    """Financial impact uses original invoice unit price, not adjusted price."""
    # given: POI with a shrinkage adjustment (increases unit_price_amount)
    poi = purchase_order_item_with_price

    # First adjustment: shrinkage that increases unit price
    first_adj = PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-2,
        reason=PurchaseOrderItemAdjustmentReason.SHRINKAGE,
        affects_payable=False,
        processed_at=staff_user.date_joined,
        created_by=staff_user,
    )

    # Verify unit price has increased
    assert poi.unit_price_amount == Decimal("12.50")

    # when: creating another adjustment
    second_adj = PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-1,
        reason=PurchaseOrderItemAdjustmentReason.DAMAGE,
        affects_payable=False,
        processed_at=staff_user.date_joined,
        created_by=staff_user,
    )

    # then: financial impact uses original $10 unit price, not adjusted $12.50
    assert first_adj.financial_impact == Decimal("-20.00")  # -2 × $10
    assert second_adj.financial_impact == Decimal("-10.00")  # -1 × $10
    # NOT -1 × $12.50 = -$12.50


def test_financial_impact_consistent_regardless_of_order(
    purchase_order_item_with_price, staff_user
):
    """Financial impact is same regardless of adjustment creation order."""
    # given: POI with multiple adjustments created in sequence
    poi = purchase_order_item_with_price

    adj1 = PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-2,
        reason=PurchaseOrderItemAdjustmentReason.SHRINKAGE,
        affects_payable=False,
        processed_at=staff_user.date_joined,
        created_by=staff_user,
    )

    adj2 = PurchaseOrderItemAdjustment.objects.create(
        purchase_order_item=poi,
        quantity_change=-3,
        reason=PurchaseOrderItemAdjustmentReason.DAMAGE,
        affects_payable=False,
        processed_at=staff_user.date_joined,
        created_by=staff_user,
    )

    # when: checking financial impact
    # then: both use original unit price consistently
    assert adj1.financial_impact == Decimal("-20.00")  # -2 × $10
    assert adj2.financial_impact == Decimal("-30.00")  # -3 × $10
    # Total financial impact is consistent: -$50
