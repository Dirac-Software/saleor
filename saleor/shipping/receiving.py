"""Shipment lifecycle management for inbound goods."""

from django.db import transaction
from django.utils import timezone

from ..inventory import PurchaseOrderItemStatus
from .models import Shipment

"""
We make a PO
We arrange shipping
We create a shipment
Goods arrive at warehouse
We record the shipping invoice

What ordering should we impose on these events?

I expect there will be some check in shipment session model, where we increment
the POI quantity_received. Once we finish a session then we can click 'done' and mark
the shipment as received.

In order to make this work a shipment which tracks some POIs tht are arriving must
exist first. We probably want to record the tracking number of a shipment when we make
it so the warehouse guys can plan ahead. We need an invoice to do accounting so this can
be nullable.

We use a constraint that a shipment must

"""


@transaction.atomic
def create_shipment(
    source_address,
    destination_address,
    purchase_order_items,
    carrier=None,
    tracking_number=None,
    shipping_cost=None,
    shipping_cost_vat=None,
    currency="GBP",
):
    """
    Create a new shipment for inbound goods from supplier.

    Args:
        source_address: Address where shipment originates (supplier)
        destination_address: Address where shipment is going (owned warehouse)
        purchase_order_items: List of PurchaseOrderItem instances to include
        carrier: Optional carrier name
        tracking_number: Optional tracking number
        shipping_cost: Estimated shipping cost (Decimal)
        shipping_cost_vat: Estimated VAT on shipping (Decimal)
        currency: Currency for costs (default GBP)

    Returns:
        Shipment instance

    Raises:
        ValueError: If POIs are not CONFIRMED or already assigned to a shipment

    Links POIs to shipment for tracking physical movement.
    Costs start as estimates until invoice is added.
    """
    # Validate all POIs are CONFIRMED
    for poi in purchase_order_items:
        if poi.status != PurchaseOrderItemStatus.CONFIRMED:
            raise ValueError(
                f"POI {poi.id} must be CONFIRMED to create shipment, "
                f"current status: {poi.status}"
            )
        if poi.shipment is not None:
            raise ValueError(
                f"POI {poi.id} is already assigned to shipment {poi.shipment.id}"
            )

    # Create the shipment
    shipment = Shipment.objects.create(
        source=source_address,
        destination=destination_address,
        carrier=carrier,
        tracking_number=tracking_number,
        shipping_cost_amount=shipping_cost or 0,
        shipping_cost_vat_amount=shipping_cost_vat or 0,
        currency=currency,
    )

    # Link POIs to shipment
    for poi in purchase_order_items:
        poi.shipment = shipment
        poi.save(update_fields=["shipment"])

    return shipment


@transaction.atomic
def add_invoice_for_shipment(shipment, invoice, shipping_cost, shipping_cost_vat):
    """
    Attach shipping invoice and finalize costs.

    Args:
        shipment: Shipment instance
        invoice: Invoice instance for shipping costs
        shipping_cost: Actual shipping cost from invoice (Decimal)
        shipping_cost_vat: Actual VAT from invoice (Decimal)

    Returns:
        Updated Shipment instance

    Updates shipment with final costs from invoice.
    Called when shipping invoice is received and processed.
    """
    shipment.shipping_invoice = invoice
    shipment.shipping_cost_amount = shipping_cost
    shipment.shipping_cost_vat_amount = shipping_cost_vat
    shipment.save(
        update_fields=["shipping_invoice", "shipping_cost_amount", "shipping_cost_vat_amount"]
    )

    return shipment


@transaction.atomic
def receive_shipment(shipment, items_received, finalize=False):
    """
    Process receipt of shipment goods.

    Args:
        shipment: Shipment instance being received
        items_received: Dict mapping POI IDs to actual quantities received
                       Example: {poi_id_1: 50, poi_id_2: 100}
        finalize: If True, finalize all POIs and handle shortages

    Returns:
        Updated Shipment instance

    Can be called multiple times as goods arrive incrementally.
    When finalize=True:
    - Sets shipment.arrived_at to current time
    - Finalizes all POIs (handles shortages, moves to RECEIVED status)
    """
    raise NotImplementedError("receive_shipment not yet implemented")
