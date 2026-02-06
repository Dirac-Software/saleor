"""Shipment lifecycle management for inbound goods."""

from django.db import transaction
from prices import Money

from ..inventory import PurchaseOrderItemStatus
from ..inventory.events import shipment_assigned_event
from .models import Shipment

"""
We make a PO
We arrange shipping
We create a shipment
Goods arrive at warehouse
We record the shipping invoice


A receipt requires a shipment.
A shipment requires a PO.

"""


@transaction.atomic
def create_shipment(
    source_address,
    destination_address,
    purchase_order_items,
    carrier=None,
    tracking_number=None,
    shipping_cost=None,
    currency="GBP",
    user=None,
    app=None,
):
    """Create a new shipment for inbound goods from supplier.

    Args:
        source_address: Address where shipment originates (supplier)
        destination_address: Address where shipment is going (owned warehouse)
        purchase_order_items: List of PurchaseOrderItem instances to include
        carrier: Optional carrier name
        tracking_number: Optional tracking number
        shipping_cost: Estimated shipping cost including VAT (Decimal)
        currency: Currency for costs (default GBP)
        user: Optional user who created the shipment
        app: Optional app that created the shipment

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
    shipment_data = {
        "source": source_address,
        "destination": destination_address,
        "carrier": carrier,
        "tracking_number": tracking_number,
    }

    if shipping_cost is not None:
        shipment_data["shipping_cost"] = Money(shipping_cost, currency)

    shipment = Shipment.objects.create(**shipment_data)  # type: ignore[misc]

    # Link POIs to shipment and log events
    for poi in purchase_order_items:
        poi.shipment = shipment
        poi.save(update_fields=["shipment"])

        # Log shipment assignment for audit trail
        shipment_assigned_event(
            purchase_order_item=poi,
            shipment_id=shipment.id,
            user=user,
            app=app,
        )

    return shipment
