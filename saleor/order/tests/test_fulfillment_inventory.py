import pytest

from ...warehouse.models import Allocation, AllocationSource, Stock
from ..models import Fulfillment, FulfillmentLine


@pytest.fixture
def fulfillment_with_owned_warehouse(order_with_lines, owned_warehouse):
    order = order_with_lines
    fulfillment = Fulfillment.objects.create(order=order)

    for line in order.lines.all():
        stock, _ = Stock.objects.get_or_create(
            warehouse=owned_warehouse,
            product_variant=line.variant,
            defaults={"quantity": 100},
        )

        allocation, _ = Allocation.objects.get_or_create(
            order_line=line,
            stock=stock,
            defaults={"quantity_allocated": line.quantity},
        )

        FulfillmentLine.objects.create(
            fulfillment=fulfillment,
            order_line=line,
            quantity=line.quantity,
            stock=stock,
        )

    return fulfillment


@pytest.mark.django_db
def test_fulfillment_has_inventory_received_true(
    fulfillment_with_owned_warehouse, purchase_order_item
):
    from django.utils import timezone

    fulfillment = fulfillment_with_owned_warehouse

    purchase_order_item.shipment.arrived_at = timezone.now()
    purchase_order_item.shipment.save()

    for line in fulfillment.lines.all():
        order_line = line.order_line
        allocations = order_line.allocations.filter(stock__warehouse__is_owned=True)

        for allocation in allocations:
            AllocationSource.objects.create(
                allocation=allocation,
                purchase_order_item=purchase_order_item,
                quantity=allocation.quantity_allocated,
            )

    assert fulfillment.has_inventory_received is True


@pytest.mark.django_db
def test_fulfillment_has_inventory_received_false_no_sources(
    fulfillment_with_owned_warehouse,
):
    fulfillment = fulfillment_with_owned_warehouse

    for line in fulfillment.lines.all():
        order_line = line.order_line
        assert order_line.allocations.filter(stock__warehouse__is_owned=True).exists()

    assert fulfillment.has_inventory_received is False


@pytest.mark.django_db
def test_fulfillment_has_inventory_received_false_partial_sources(
    fulfillment_with_owned_warehouse, purchase_order_item
):
    fulfillment = fulfillment_with_owned_warehouse

    for line in fulfillment.lines.all():
        order_line = line.order_line
        allocations = order_line.allocations.filter(stock__warehouse__is_owned=True)

        for allocation in allocations:
            AllocationSource.objects.create(
                allocation=allocation,
                purchase_order_item=purchase_order_item,
                quantity=allocation.quantity_allocated - 1,
            )

    assert fulfillment.has_inventory_received is False


@pytest.mark.django_db
def test_fulfillment_has_inventory_received_no_owned_allocations(fulfillment):
    assert fulfillment.has_inventory_received is False


@pytest.mark.django_db
def test_fulfillment_has_inventory_received_false_shipment_not_arrived(
    fulfillment_with_owned_warehouse, purchase_order_item
):
    fulfillment = fulfillment_with_owned_warehouse

    purchase_order_item.shipment.arrived_at = None
    purchase_order_item.shipment.save()

    for line in fulfillment.lines.all():
        order_line = line.order_line
        allocations = order_line.allocations.filter(stock__warehouse__is_owned=True)

        for allocation in allocations:
            AllocationSource.objects.create(
                allocation=allocation,
                purchase_order_item=purchase_order_item,
                quantity=allocation.quantity_allocated,
            )

    assert fulfillment.has_inventory_received is False
