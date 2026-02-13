from decimal import Decimal

import pytest

from ..stock_utils import (
    get_fulfillable_quantity_for_order_line,
    get_received_quantity_for_order_line,
)


@pytest.mark.parametrize(
    ("allocated_qty", "receipt_qty", "expected_received"),
    [
        (5, 5, 5),
        (5, 10, 10),
        (10, 5, 5),
        (0, 0, 0),
    ],
)
def test_get_received_quantity_for_order_line(
    order_with_lines,
    warehouse,
    address,
    allocated_qty,
    receipt_qty,
    expected_received,
):
    from saleor.inventory import PurchaseOrderItemStatus
    from saleor.inventory.models import (
        PurchaseOrder,
        PurchaseOrderItem,
        Receipt,
        ReceiptLine,
    )
    from saleor.shipping import ShipmentType
    from saleor.shipping.models import Shipment
    from saleor.warehouse.models import Allocation, AllocationSource, Warehouse

    line = order_with_lines.lines.first()
    variant = line.variant

    supplier_warehouse = Warehouse.objects.create(
        address=address.get_copy(),
        name="Supplier Warehouse",
        slug="supplier-warehouse-test",
        email="supplier@example.com",
        is_owned=False,
    )

    po = PurchaseOrder.objects.create(
        source_warehouse=supplier_warehouse,
        destination_warehouse=warehouse,
    )
    poi = PurchaseOrderItem.objects.create(
        order=po,
        product_variant=variant,
        quantity_ordered=max(receipt_qty, allocated_qty),
        total_price_amount=Decimal("1000.00"),
        currency="USD",
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    if receipt_qty > 0:
        shipment = Shipment.objects.create(
            source=supplier_warehouse.address,
            destination=warehouse.address,
            shipment_type=ShipmentType.INBOUND,
            arrived_at="2024-01-01T00:00:00Z",
            shipping_cost_amount=Decimal("100.00"),
            currency="USD",
        )
        poi.shipment = shipment
        poi.save()

        receipt = Receipt.objects.create(shipment=shipment)
        ReceiptLine.objects.create(
            receipt=receipt, purchase_order_item=poi, quantity_received=receipt_qty
        )

    if allocated_qty > 0:
        stock = warehouse.stock_set.filter(product_variant=variant).first()
        allocation = Allocation.objects.create(
            order_line=line, stock=stock, quantity_allocated=allocated_qty
        )
        AllocationSource.objects.create(
            allocation=allocation, purchase_order_item=poi, quantity=allocated_qty
        )

    received = get_received_quantity_for_order_line(line)

    assert received == expected_received


def test_get_received_quantity_no_allocations(order_with_lines):
    line = order_with_lines.lines.first()
    received = get_received_quantity_for_order_line(line)
    assert received == 0


def test_get_fulfillable_quantity_respects_order_quantity(
    order_with_lines, warehouse, address
):
    from saleor.inventory import PurchaseOrderItemStatus
    from saleor.inventory.models import (
        PurchaseOrder,
        PurchaseOrderItem,
        Receipt,
        ReceiptLine,
    )
    from saleor.shipping import ShipmentType
    from saleor.shipping.models import Shipment
    from saleor.warehouse.models import Allocation, AllocationSource, Warehouse

    line = order_with_lines.lines.first()
    variant = line.variant
    ordered_quantity = line.quantity

    supplier_warehouse = Warehouse.objects.create(
        address=address.get_copy(),
        name="Supplier Warehouse 2",
        slug="supplier-warehouse-test-2",
        email="supplier2@example.com",
        is_owned=False,
    )

    po = PurchaseOrder.objects.create(
        source_warehouse=supplier_warehouse,
        destination_warehouse=warehouse,
    )
    poi = PurchaseOrderItem.objects.create(
        order=po,
        product_variant=variant,
        quantity_ordered=100,
        total_price_amount=Decimal("10000.00"),
        currency="USD",
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    shipment = Shipment.objects.create(
        source=supplier_warehouse.address,
        destination=warehouse.address,
        shipment_type=ShipmentType.INBOUND,
        arrived_at="2024-01-01T00:00:00Z",
        shipping_cost_amount=Decimal("100.00"),
        currency="USD",
    )
    poi.shipment = shipment
    poi.save()

    receipt = Receipt.objects.create(shipment=shipment)
    ReceiptLine.objects.create(
        receipt=receipt, purchase_order_item=poi, quantity_received=100
    )

    stock = warehouse.stock_set.filter(product_variant=variant).first()
    allocation = Allocation.objects.create(
        order_line=line, stock=stock, quantity_allocated=ordered_quantity
    )
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=poi, quantity=ordered_quantity
    )

    fulfillable = get_fulfillable_quantity_for_order_line(line)

    assert fulfillable == ordered_quantity


def test_get_fulfillable_quantity_minus_already_fulfilled(
    order_with_lines, warehouse, address
):
    from saleor.inventory import PurchaseOrderItemStatus
    from saleor.inventory.models import (
        PurchaseOrder,
        PurchaseOrderItem,
        Receipt,
        ReceiptLine,
    )
    from saleor.order.models import Fulfillment, FulfillmentLine
    from saleor.shipping import ShipmentType
    from saleor.shipping.models import Shipment
    from saleor.warehouse.models import Allocation, AllocationSource, Warehouse

    line = order_with_lines.lines.first()
    variant = line.variant
    ordered_quantity = line.quantity

    supplier_warehouse = Warehouse.objects.create(
        address=address.get_copy(),
        name="Supplier Warehouse 3",
        slug="supplier-warehouse-test-3",
        email="supplier3@example.com",
        is_owned=False,
    )

    po = PurchaseOrder.objects.create(
        source_warehouse=supplier_warehouse,
        destination_warehouse=warehouse,
    )
    poi = PurchaseOrderItem.objects.create(
        order=po,
        product_variant=variant,
        quantity_ordered=10,
        total_price_amount=Decimal("1000.00"),
        currency="USD",
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    shipment = Shipment.objects.create(
        source=supplier_warehouse.address,
        destination=warehouse.address,
        shipment_type=ShipmentType.INBOUND,
        arrived_at="2024-01-01T00:00:00Z",
        shipping_cost_amount=Decimal("100.00"),
        currency="USD",
    )
    poi.shipment = shipment
    poi.save()

    receipt = Receipt.objects.create(shipment=shipment)
    ReceiptLine.objects.create(
        receipt=receipt, purchase_order_item=poi, quantity_received=10
    )

    stock = warehouse.stock_set.filter(product_variant=variant).first()
    allocation = Allocation.objects.create(
        order_line=line, stock=stock, quantity_allocated=ordered_quantity
    )
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=poi, quantity=ordered_quantity
    )

    fulfillment = Fulfillment.objects.create(order=order_with_lines, status="FULFILLED")
    FulfillmentLine.objects.create(order_line=line, fulfillment=fulfillment, quantity=2)

    fulfillable = get_fulfillable_quantity_for_order_line(line)

    assert fulfillable == min(ordered_quantity, 10) - 2


def test_get_fulfillable_quantity_never_negative(order_with_lines, warehouse, address):
    from saleor.inventory import PurchaseOrderItemStatus
    from saleor.inventory.models import (
        PurchaseOrder,
        PurchaseOrderItem,
        Receipt,
        ReceiptLine,
    )
    from saleor.order.models import Fulfillment, FulfillmentLine
    from saleor.shipping import ShipmentType
    from saleor.shipping.models import Shipment
    from saleor.warehouse.models import Allocation, AllocationSource, Warehouse

    line = order_with_lines.lines.first()
    variant = line.variant
    ordered_quantity = line.quantity

    supplier_warehouse = Warehouse.objects.create(
        address=address.get_copy(),
        name="Supplier Warehouse 4",
        slug="supplier-warehouse-test-4",
        email="supplier4@example.com",
        is_owned=False,
    )

    po = PurchaseOrder.objects.create(
        source_warehouse=supplier_warehouse,
        destination_warehouse=warehouse,
    )
    poi = PurchaseOrderItem.objects.create(
        order=po,
        product_variant=variant,
        quantity_ordered=5,
        total_price_amount=Decimal("500.00"),
        currency="USD",
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    shipment = Shipment.objects.create(
        source=supplier_warehouse.address,
        destination=warehouse.address,
        shipment_type=ShipmentType.INBOUND,
        arrived_at="2024-01-01T00:00:00Z",
        shipping_cost_amount=Decimal("100.00"),
        currency="USD",
    )
    poi.shipment = shipment
    poi.save()

    receipt = Receipt.objects.create(shipment=shipment)
    ReceiptLine.objects.create(
        receipt=receipt, purchase_order_item=poi, quantity_received=5
    )

    stock = warehouse.stock_set.filter(product_variant=variant).first()
    allocation = Allocation.objects.create(
        order_line=line, stock=stock, quantity_allocated=ordered_quantity
    )
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=poi, quantity=ordered_quantity
    )

    fulfillment = Fulfillment.objects.create(order=order_with_lines, status="FULFILLED")
    FulfillmentLine.objects.create(
        order_line=line, fulfillment=fulfillment, quantity=ordered_quantity
    )

    fulfillable = get_fulfillable_quantity_for_order_line(line)

    assert fulfillable == 0
