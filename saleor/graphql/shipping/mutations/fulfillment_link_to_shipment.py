import graphene
from django.core.exceptions import ValidationError
from django.db import transaction

from ....permission.enums import OrderPermissions
from ...core import ResolveInfo
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.mutations import BaseMutation
from ...core.types import OrderError
from ...core.utils import from_global_id_or_error
from ...order.types import Fulfillment


class FulfillmentLinkToShipment(BaseMutation):
    fulfillment = graphene.Field(
        Fulfillment,
        description="Updated fulfillment linked to shipment.",
    )

    class Arguments:
        fulfillment_id = graphene.ID(
            required=True,
            description="ID of the fulfillment to link.",
        )
        shipment_id = graphene.ID(
            required=True,
            description="ID of the shipment to link to.",
        )

    class Meta:
        description = "Links a fulfillment to an existing shipment."
        permissions = (OrderPermissions.MANAGE_ORDERS,)
        error_type_class = OrderError
        error_type_field = "order_errors"
        doc_category = DOC_CATEGORY_ORDERS

    @classmethod
    @transaction.atomic
    def perform_mutation(cls, _root, info: ResolveInfo, /, **data):
        from ....order.models import Fulfillment as FulfillmentModel
        from ....shipping.models import Shipment

        # Get fulfillment
        _, fulfillment_pk = from_global_id_or_error(
            data["fulfillment_id"], "Fulfillment"
        )
        try:
            fulfillment = (
                FulfillmentModel.objects.select_for_update()
                .select_related("order")
                .prefetch_related("lines__stock__warehouse__address")
                .get(pk=fulfillment_pk)
            )
        except FulfillmentModel.DoesNotExist:
            raise ValidationError("Fulfillment not found.") from None

        # Get shipment
        _, shipment_pk = from_global_id_or_error(data["shipment_id"], "Shipment")
        try:
            shipment = (
                Shipment.objects.select_for_update()
                .select_related("source", "destination")
                .get(pk=shipment_pk)
            )
        except Shipment.DoesNotExist:
            raise ValidationError("Shipment not found.") from None

        # Validate the link
        cls._validate_link(fulfillment, shipment)

        # Link fulfillment to shipment using the action that handles auto-approval
        from ....order.actions import assign_shipment_to_fulfillment

        if shipment.tracking_url:
            fulfillment.tracking_url = shipment.tracking_url
            fulfillment.save(update_fields=["tracking_url"])

        assign_shipment_to_fulfillment(
            fulfillment, shipment, user=info.context.user, auto_approve=True
        )

        return FulfillmentLinkToShipment(fulfillment=fulfillment)

    @classmethod
    def _validate_link(cls, fulfillment, shipment):
        """Validate that fulfillment can be linked to shipment."""
        from ....order import FulfillmentStatus
        from ....shipping import ShipmentType

        if shipment.shipment_type != ShipmentType.OUTBOUND:
            raise ValidationError(
                f"Cannot link fulfillment to {shipment.shipment_type} shipment. "
                "Only outbound shipments can have fulfillments linked."
            )

        # Check fulfillment status
        if fulfillment.status != FulfillmentStatus.WAITING_FOR_APPROVAL:
            raise ValidationError(
                f"Fulfillment must be in WAITING_FOR_APPROVAL status, "
                f"got {fulfillment.status}."
            )

        # Check fulfillment not already linked
        if fulfillment.shipment_id:
            raise ValidationError(
                "Fulfillment is already linked to a shipment. "
                "Unlink it first before linking to a different shipment."
            )

        # Check shipment hasn't departed
        if shipment.departed_at:
            raise ValidationError(
                f"Shipment has already departed at {shipment.departed_at}. "
                "Cannot add fulfillments to departed shipments."
            )

        # Check destination addresses match
        if not fulfillment.order.shipping_address:
            raise ValidationError(
                "Order must have a shipping address to link to a shipment."
            )

        fulfillment_dest = fulfillment.order.shipping_address
        shipment_dest = shipment.destination

        # Compare key address fields
        if not cls._addresses_match(fulfillment_dest, shipment_dest):
            raise ValidationError(
                "Fulfillment destination does not match shipment destination. "
                f"Fulfillment goes to {fulfillment_dest.city}, "
                f"but shipment goes to {shipment_dest.city}."
            )

        # Check source addresses are compatible (warehouse → shipment source)
        fulfillment_line = fulfillment.lines.select_related(
            "stock__warehouse__address"
        ).first()

        if fulfillment_line and fulfillment_line.stock:
            warehouse = fulfillment_line.stock.warehouse
            if warehouse.address and shipment.source:
                if not cls._addresses_match(warehouse.address, shipment.source):
                    raise ValidationError(
                        f"Fulfillment source warehouse ({warehouse.name}) does not "
                        f"match shipment source location."
                    )

    @staticmethod
    def _addresses_match(addr1, addr2):
        """Check if two addresses are the same (fuzzy match on key fields)."""
        if not addr1 or not addr2:
            return False

        # Compare key fields
        return (
            addr1.street_address_1 == addr2.street_address_1
            and addr1.city == addr2.city
            and addr1.postal_code == addr2.postal_code
            and addr1.country == addr2.country
        )
