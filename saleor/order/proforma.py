from decimal import Decimal

from ..core.utils.events import call_event
from ..invoice.models import Invoice, InvoiceEvents


def calculate_deposit_allocation(order, fulfillment_total):
    """Calculate deposit credit for a fulfillment using FIFO allocation.

    Args:
        order: Order instance with deposit information
        fulfillment_total: Total amount of current fulfillment

    Returns:
        Decimal: Amount of deposit to allocate to this fulfillment

    """
    if not order.deposit_required:
        return Decimal(0)

    total_deposit_paid = order.total_deposit_paid
    if not total_deposit_paid:
        return Decimal(0)

    order_total = order.total_gross_amount
    if order_total == 0:
        return Decimal(0)

    already_allocated = sum(
        f.deposit_allocated_amount or Decimal(0) for f in order.fulfillments.all()
    )
    remaining_deposit = max(Decimal(0), total_deposit_paid - already_allocated)

    proportional_share = fulfillment_total * (total_deposit_paid / order_total)
    deposit_credit = min(remaining_deposit, proportional_share)

    return deposit_credit


def calculate_fulfillment_total(fulfillment):
    """Calculate total value of a fulfillment.

    Args:
        fulfillment: Fulfillment instance with lines

    Returns:
        Decimal: Total gross amount of fulfillment

    """
    total = Decimal(0)
    for line in fulfillment.lines.all():
        order_line = line.order_line
        unit_price = order_line.unit_price_gross_amount
        total += unit_price * line.quantity

    return total


def generate_proforma_invoice(fulfillment, manager):
    """Generate proforma invoice for a fulfillment and trigger webhook.

    Args:
        fulfillment: Fulfillment instance
        manager: PluginsManager for webhook triggering

    Returns:
        Invoice: Created proforma invoice

    """
    from ..invoice import InvoiceType

    order = fulfillment.order

    fulfillment_total = calculate_fulfillment_total(fulfillment)
    deposit_credit = calculate_deposit_allocation(order, fulfillment_total)

    fulfillment.deposit_allocated_amount = deposit_credit
    fulfillment.save(update_fields=["deposit_allocated_amount"])

    invoice = Invoice.objects.create(
        order=order,
        fulfillment=fulfillment,
        type=InvoiceType.PROFORMA,
        number=None,
        created=fulfillment.created_at,
    )

    invoice.events.create(
        type=InvoiceEvents.REQUESTED,
        user=None,
        parameters={"invoice_type": InvoiceType.PROFORMA},
    )

    call_event(manager.fulfillment_proforma_invoice_generated, fulfillment)

    return invoice
