import graphene
import pytest

from .....graphql.tests.utils import get_graphql_content

ORDER_QUERY_WAREHOUSE_STOCK = """
    query Order($id: ID!) {
        order(id: $id) {
            id
            lines {
                id
                quantity
                warehouseStock
                canFulfillQuantity
                isReadyToFulfill
            }
            fulfillableLines {
                id
                quantity
            }
        }
    }
"""


def test_order_line_warehouse_stock_fields_exist(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order_id = graphene.Node.to_global_id("Order", order.id)

    response = staff_api_client.post_graphql(
        ORDER_QUERY_WAREHOUSE_STOCK, {"id": order_id}
    )

    content = get_graphql_content(response)
    order_data = content["data"]["order"]

    assert order_data["id"] == order_id
    assert len(order_data["lines"]) > 0

    for line in order_data["lines"]:
        assert "warehouseStock" in line
        assert "canFulfillQuantity" in line
        assert "isReadyToFulfill" in line
        assert line["warehouseStock"] >= 0
        assert line["canFulfillQuantity"] >= 0
        assert isinstance(line["isReadyToFulfill"], bool)

    assert "fulfillableLines" in order_data


def test_order_line_warehouse_stock_with_receipts(
    staff_api_client,
    permission_group_manage_orders,
    order_with_lines,
    warehouse,
    address,
):
    from decimal import Decimal

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

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    line = order.lines.first()
    variant = line.variant

    supplier_warehouse = Warehouse.objects.create(
        address=address.get_copy(),
        name="Supplier Warehouse GraphQL",
        slug="supplier-warehouse-graphql",
        email="supplier-graphql@example.com",
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

    allocation = Allocation.objects.create(
        order_line=line,
        stock=warehouse.stock_set.filter(product_variant=variant).first(),
        quantity_allocated=5,
    )
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=poi, quantity=5
    )

    order_id = graphene.Node.to_global_id("Order", order.id)
    response = staff_api_client.post_graphql(
        ORDER_QUERY_WAREHOUSE_STOCK, {"id": order_id}
    )

    content = get_graphql_content(response)
    order_data = content["data"]["order"]
    line_data = order_data["lines"][0]

    assert line_data["warehouseStock"] == 10
    assert line_data["canFulfillQuantity"] == min(line.quantity, 10)


def test_order_line_warehouse_stock_no_receipts(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order_id = graphene.Node.to_global_id("Order", order.id)

    response = staff_api_client.post_graphql(
        ORDER_QUERY_WAREHOUSE_STOCK, {"id": order_id}
    )

    content = get_graphql_content(response)
    order_data = content["data"]["order"]

    for line in order_data["lines"]:
        assert line["warehouseStock"] == 0
        assert line["canFulfillQuantity"] == 0


def test_order_fulfillable_lines_filters_correctly(
    staff_api_client,
    permission_group_manage_orders,
    order_with_lines,
    warehouse,
    address,
):
    from decimal import Decimal

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

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines

    lines = list(order.lines.all()[:2])
    if len(lines) < 2:
        pytest.skip("Need at least 2 order lines")

    line_with_stock = lines[0]
    variant_with_stock = line_with_stock.variant

    supplier_warehouse = Warehouse.objects.create(
        address=address.get_copy(),
        name="Supplier Warehouse GraphQL 2",
        slug="supplier-warehouse-graphql-2",
        email="supplier-graphql-2@example.com",
        is_owned=False,
    )

    po = PurchaseOrder.objects.create(
        source_warehouse=supplier_warehouse,
        destination_warehouse=warehouse,
    )
    poi = PurchaseOrderItem.objects.create(
        order=po,
        product_variant=variant_with_stock,
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

    allocation = Allocation.objects.create(
        order_line=line_with_stock,
        stock=warehouse.stock_set.filter(product_variant=variant_with_stock).first(),
        quantity_allocated=5,
    )
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=poi, quantity=5
    )

    order_id = graphene.Node.to_global_id("Order", order.id)
    response = staff_api_client.post_graphql(
        ORDER_QUERY_WAREHOUSE_STOCK, {"id": order_id}
    )

    content = get_graphql_content(response)
    order_data = content["data"]["order"]

    assert len(order_data["fulfillableLines"]) == 1
    fulfillable_line_id = graphene.Node.to_global_id("OrderLine", line_with_stock.id)
    assert order_data["fulfillableLines"][0]["id"] == fulfillable_line_id


def test_order_line_can_fulfill_quantity_respects_already_fulfilled(
    staff_api_client,
    permission_group_manage_orders,
    order_with_lines,
    warehouse,
    address,
):
    from decimal import Decimal

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

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    line = order.lines.first()
    variant = line.variant

    supplier_warehouse = Warehouse.objects.create(
        address=address.get_copy(),
        name="Supplier Warehouse GraphQL 3",
        slug="supplier-warehouse-graphql-3",
        email="supplier-graphql-3@example.com",
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

    allocation = Allocation.objects.create(
        order_line=line,
        stock=warehouse.stock_set.filter(product_variant=variant).first(),
        quantity_allocated=line.quantity,
    )
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=poi, quantity=line.quantity
    )

    fulfillment = Fulfillment.objects.create(order=order, status="FULFILLED")
    FulfillmentLine.objects.create(order_line=line, fulfillment=fulfillment, quantity=3)

    order_id = graphene.Node.to_global_id("Order", order.id)
    response = staff_api_client.post_graphql(
        ORDER_QUERY_WAREHOUSE_STOCK, {"id": order_id}
    )

    content = get_graphql_content(response)
    order_data = content["data"]["order"]
    line_data = order_data["lines"][0]

    assert line_data["warehouseStock"] == 10
    assert line_data["canFulfillQuantity"] == min(line.quantity, 10) - 3


def test_order_line_is_ready_to_fulfill_with_stock_and_deposit_met(
    staff_api_client,
    permission_group_manage_orders,
    order_with_lines,
    warehouse,
    address,
):
    from decimal import Decimal

    from saleor.inventory import PurchaseOrderItemStatus
    from saleor.inventory.models import (
        PurchaseOrder,
        PurchaseOrderItem,
        Receipt,
        ReceiptLine,
    )
    from saleor.payment import ChargeStatus, CustomPaymentChoices
    from saleor.payment.models import Payment
    from saleor.shipping import ShipmentType
    from saleor.shipping.models import Shipment
    from saleor.warehouse.models import Allocation, AllocationSource, Warehouse

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    line = order.lines.first()
    variant = line.variant

    order.deposit_required = True
    order.deposit_percentage = Decimal("30")
    order.total_gross_amount = Decimal("1000.00")
    order.save()

    Payment.objects.create(
        order=order,
        gateway=CustomPaymentChoices.XERO,
        psp_reference="XERO-DEPOSIT-1",
        total=Decimal("300.00"),
        captured_amount=Decimal("300.00"),
        charge_status=ChargeStatus.FULLY_CHARGED,
        currency=order.currency,
        is_active=True,
        metadata={"is_deposit": True},
    )

    supplier_warehouse = Warehouse.objects.create(
        address=address.get_copy(),
        name="Supplier Warehouse Ready",
        slug="supplier-warehouse-ready",
        email="supplier-ready@example.com",
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

    allocation = Allocation.objects.create(
        order_line=line,
        stock=warehouse.stock_set.filter(product_variant=variant).first(),
        quantity_allocated=5,
    )
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=poi, quantity=5
    )

    order_id = graphene.Node.to_global_id("Order", order.id)
    response = staff_api_client.post_graphql(
        ORDER_QUERY_WAREHOUSE_STOCK, {"id": order_id}
    )

    content = get_graphql_content(response)
    order_data = content["data"]["order"]
    line_data = order_data["lines"][0]

    assert line_data["canFulfillQuantity"] > 0
    assert line_data["isReadyToFulfill"] is True


def test_order_line_is_ready_to_fulfill_with_stock_but_deposit_not_met(
    staff_api_client,
    permission_group_manage_orders,
    order_with_lines,
    warehouse,
    address,
):
    from decimal import Decimal

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

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    line = order.lines.first()
    variant = line.variant

    order.deposit_required = True
    order.deposit_percentage = Decimal("30")
    order.total_gross_amount = Decimal("1000.00")
    order.save()

    supplier_warehouse = Warehouse.objects.create(
        address=address.get_copy(),
        name="Supplier Warehouse Not Ready",
        slug="supplier-warehouse-not-ready",
        email="supplier-not-ready@example.com",
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

    allocation = Allocation.objects.create(
        order_line=line,
        stock=warehouse.stock_set.filter(product_variant=variant).first(),
        quantity_allocated=5,
    )
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=poi, quantity=5
    )

    order_id = graphene.Node.to_global_id("Order", order.id)
    response = staff_api_client.post_graphql(
        ORDER_QUERY_WAREHOUSE_STOCK, {"id": order_id}
    )

    content = get_graphql_content(response)
    order_data = content["data"]["order"]
    line_data = order_data["lines"][0]

    assert line_data["canFulfillQuantity"] > 0
    assert line_data["isReadyToFulfill"] is False


def test_order_line_is_ready_to_fulfill_without_stock(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    from decimal import Decimal

    from saleor.payment import ChargeStatus, CustomPaymentChoices
    from saleor.payment.models import Payment

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines

    order.deposit_required = True
    order.deposit_percentage = Decimal("30")
    order.total_gross_amount = Decimal("1000.00")
    order.save()

    Payment.objects.create(
        order=order,
        gateway=CustomPaymentChoices.XERO,
        psp_reference="XERO-DEPOSIT-2",
        total=Decimal("300.00"),
        captured_amount=Decimal("300.00"),
        charge_status=ChargeStatus.FULLY_CHARGED,
        currency=order.currency,
        is_active=True,
        metadata={"is_deposit": True},
    )

    order_id = graphene.Node.to_global_id("Order", order.id)
    response = staff_api_client.post_graphql(
        ORDER_QUERY_WAREHOUSE_STOCK, {"id": order_id}
    )

    content = get_graphql_content(response)
    order_data = content["data"]["order"]
    line_data = order_data["lines"][0]

    assert line_data["canFulfillQuantity"] == 0
    assert line_data["isReadyToFulfill"] is False


def test_order_line_is_ready_to_fulfill_without_deposit_requirement(
    staff_api_client,
    permission_group_manage_orders,
    order_with_lines,
    warehouse,
    address,
):
    from decimal import Decimal

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

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    line = order.lines.first()
    variant = line.variant

    order.deposit_required = False
    order.save()

    supplier_warehouse = Warehouse.objects.create(
        address=address.get_copy(),
        name="Supplier Warehouse No Deposit",
        slug="supplier-warehouse-no-deposit",
        email="supplier-no-deposit@example.com",
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

    allocation = Allocation.objects.create(
        order_line=line,
        stock=warehouse.stock_set.filter(product_variant=variant).first(),
        quantity_allocated=5,
    )
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=poi, quantity=5
    )

    order_id = graphene.Node.to_global_id("Order", order.id)
    response = staff_api_client.post_graphql(
        ORDER_QUERY_WAREHOUSE_STOCK, {"id": order_id}
    )

    content = get_graphql_content(response)
    order_data = content["data"]["order"]
    line_data = order_data["lines"][0]

    assert line_data["canFulfillQuantity"] > 0
    assert line_data["isReadyToFulfill"] is True
