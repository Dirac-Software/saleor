from io import StringIO

import pytest
from django.core.management import call_command

from ...warehouse.models import Allocation
from .. import FulfillmentStatus, OrderStatus
from ..models import Fulfillment


@pytest.fixture
def unfulfilled_order_without_fulfillments(order_with_allocations_single_warehouse):
    """Order in UNFULFILLED status without fulfillments (edge case)."""
    order = order_with_allocations_single_warehouse
    # Order is already UNFULFILLED with allocations but no fulfillments
    assert order.status == OrderStatus.UNFULFILLED
    assert Fulfillment.objects.filter(order=order).count() == 0
    return order


def test_command_creates_missing_fulfillments(
    unfulfilled_order_without_fulfillments,
):
    # given
    order = unfulfilled_order_without_fulfillments
    assert Fulfillment.objects.filter(order=order).count() == 0

    # when
    out = StringIO()
    call_command("create_missing_fulfillments", stdout=out)
    output = out.getvalue()

    # then
    assert "Created 1 fulfillments total" in output
    assert Fulfillment.objects.filter(order=order).count() == 1

    fulfillment = Fulfillment.objects.get(order=order)
    assert fulfillment.status == FulfillmentStatus.WAITING_FOR_APPROVAL


def test_command_dry_run_does_not_create_fulfillments(
    unfulfilled_order_without_fulfillments,
):
    # given
    order = unfulfilled_order_without_fulfillments
    assert Fulfillment.objects.filter(order=order).count() == 0

    # when
    out = StringIO()
    call_command("create_missing_fulfillments", "--dry-run", stdout=out)
    output = out.getvalue()

    # then
    assert "DRY RUN MODE" in output
    assert "Would create 1 fulfillments" in output
    assert Fulfillment.objects.filter(order=order).count() == 0


def test_command_skips_orders_without_allocation_sources(
    order, warehouse, product_variant_list
):
    # given
    order.status = OrderStatus.UNFULFILLED
    order.save()

    warehouse.is_owned = True
    warehouse.save()

    variant = product_variant_list[0]
    line = order.lines.create(
        product_name=variant.product.name,
        variant_name=variant.name,
        product_sku=variant.sku,
        is_shipping_required=True,
        is_gift_card=False,
        quantity=3,
        variant=variant,
        unit_price_net_amount=10,
        unit_price_gross_amount=10,
        total_price_net_amount=30,
        total_price_gross_amount=30,
        undiscounted_unit_price_net_amount=10,
        undiscounted_unit_price_gross_amount=10,
        undiscounted_total_price_net_amount=30,
        undiscounted_total_price_gross_amount=30,
        currency="USD",
        tax_rate=0,
    )

    from ...warehouse.models import Stock

    stock, _ = Stock.objects.get_or_create(
        warehouse=warehouse,
        product_variant=variant,
        defaults={"quantity": 10, "quantity_allocated": 0},
    )

    # Create allocation WITHOUT AllocationSource (shouldn't happen in practice)
    Allocation.objects.create(
        order_line=line,
        stock=stock,
        quantity_allocated=3,
    )

    # when
    out = StringIO()
    call_command("create_missing_fulfillments", stdout=out)
    output = out.getvalue()

    # then
    assert "Skipping order" in output
    assert "not all allocations have sources" in output
    assert Fulfillment.objects.filter(order=order).count() == 0


def test_command_skips_orders_that_already_have_fulfillments(
    unfulfilled_order_without_fulfillments,
):
    # given
    order = unfulfilled_order_without_fulfillments

    # Create a fulfillment manually
    Fulfillment.objects.create(
        order=order,
        status=FulfillmentStatus.WAITING_FOR_APPROVAL,
    )

    # when
    out = StringIO()
    call_command("create_missing_fulfillments", stdout=out)
    output = out.getvalue()

    # then
    assert "No missing fulfillments found" in output
    assert Fulfillment.objects.filter(order=order).count() == 1


def test_command_handles_multiple_orders(
    order_with_allocations_single_warehouse,
    order_with_allocations_multiple_warehouses,
):
    # given
    order1 = order_with_allocations_single_warehouse
    order2 = order_with_allocations_multiple_warehouses

    assert Fulfillment.objects.filter(order=order1).count() == 0
    assert Fulfillment.objects.filter(order=order2).count() == 0

    # when
    out = StringIO()
    call_command("create_missing_fulfillments", stdout=out)
    output = out.getvalue()

    # then
    assert "Found 2 UNFULFILLED orders without fulfillments" in output

    # order1 has 1 warehouse, order2 has 2 warehouses = 3 fulfillments total
    assert "Created 3 fulfillments total" in output

    assert Fulfillment.objects.filter(order=order1).count() == 1
    assert Fulfillment.objects.filter(order=order2).count() == 2


def test_command_handles_no_orders_gracefully():
    # given - no orders in database
    assert Fulfillment.objects.count() == 0

    # when
    out = StringIO()
    call_command("create_missing_fulfillments", stdout=out)
    output = out.getvalue()

    # then
    assert "Found 0 UNFULFILLED orders without fulfillments" in output
    assert "No missing fulfillments found" in output
