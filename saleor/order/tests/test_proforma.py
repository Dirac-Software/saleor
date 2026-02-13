from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from ..proforma import (
    calculate_deposit_allocation,
    calculate_fulfillment_total,
    generate_proforma_invoice,
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


@pytest.mark.django_db
def test_generate_proforma_invoice_creates_invoice(
    order_with_lines, warehouse, address
):
    from decimal import Decimal

    from saleor.inventory import PurchaseOrderItemStatus
    from saleor.inventory.models import (
        PurchaseOrder,
        PurchaseOrderItem,
        Receipt,
        ReceiptLine,
    )
    from saleor.invoice import InvoiceType
    from saleor.order.models import Fulfillment, FulfillmentLine
    from saleor.payment import ChargeStatus, CustomPaymentChoices
    from saleor.payment.models import Payment
    from saleor.shipping import ShipmentType
    from saleor.shipping.models import Shipment
    from saleor.warehouse.models import Allocation, AllocationSource, Warehouse

    order = order_with_lines
    line = order.lines.first()
    variant = line.variant

    order.deposit_required = True
    order.deposit_percentage = Decimal("30.00")
    order.total_gross_amount = Decimal("200.00")
    order.save()

    Payment.objects.create(
        order=order,
        gateway=CustomPaymentChoices.XERO,
        psp_reference="XERO-PMT-PROFORMA-001",
        total=Decimal("60.00"),
        captured_amount=Decimal("60.00"),
        charge_status=ChargeStatus.FULLY_CHARGED,
        currency=order.currency,
        is_active=True,
        metadata={"is_deposit": True},
    )

    supplier_warehouse = Warehouse.objects.create(
        address=address.get_copy(),
        name="Supplier Warehouse Proforma",
        slug="supplier-warehouse-proforma",
        email="supplier-proforma@example.com",
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
        order_line=line, stock=stock, quantity_allocated=5
    )
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=poi, quantity=5
    )

    fulfillment = Fulfillment.objects.create(order=order, status="FULFILLED")
    FulfillmentLine.objects.create(order_line=line, fulfillment=fulfillment, quantity=5)

    manager = Mock()
    manager.fulfillment_proforma_invoice_generated.return_value = None

    with patch("saleor.order.proforma.call_event") as mock_call_event:
        invoice = generate_proforma_invoice(fulfillment, manager)

        assert invoice is not None
        assert invoice.type == InvoiceType.PROFORMA
        assert invoice.order == order
        assert invoice.fulfillment == fulfillment

        fulfillment.refresh_from_db()
        assert fulfillment.deposit_allocated_amount > Decimal(0)

        mock_call_event.assert_called_once_with(
            manager.fulfillment_proforma_invoice_generated, fulfillment
        )


@pytest.mark.django_db
def test_generate_proforma_invoice_no_deposit(order_with_lines):
    from saleor.order.models import Fulfillment, FulfillmentLine

    order = order_with_lines
    line = order.lines.first()

    order.deposit_required = False
    order.save()

    fulfillment = Fulfillment.objects.create(order=order, status="FULFILLED")
    FulfillmentLine.objects.create(order_line=line, fulfillment=fulfillment, quantity=2)

    manager = Mock()

    invoice = generate_proforma_invoice(fulfillment, manager)

    assert invoice is not None
    fulfillment.refresh_from_db()
    assert fulfillment.deposit_allocated_amount == Decimal(0)
