import graphene
from django.core.exceptions import ValidationError
from django.db import transaction

from ....permission.enums import OrderPermissions
from ...core import ResolveInfo
from ...core.doc_category import DOC_CATEGORY_SHIPPING
from ...core.mutations import BaseMutation
from ...core.scalars import PositiveDecimal
from ...core.types import BaseInputObjectType, NonNullList, ShippingError
from ...core.utils import from_global_id_or_error
from ..types import Shipment


class OutboundShipmentCreateInput(BaseInputObjectType):
    fulfillment_ids = NonNullList(
        graphene.ID,
        required=True,
        description="Fulfillments to include in this shipment.",
    )
    carrier = graphene.String(
        required=True,
        description="Carrier name (e.g., DHL, FedEx).",
    )
    tracking_url = graphene.String(
        required=True,
        description="Tracking URL or number from carrier.",
    )
    shipping_cost = PositiveDecimal(description="Shipping cost including VAT.")
    currency = graphene.String(
        description="Currency code (default: USD).",
        default_value="USD",
    )

    class Meta:
        doc_category = DOC_CATEGORY_SHIPPING


class OutboundShipmentCreate(BaseMutation):
    shipment = graphene.Field(Shipment, description="Created outbound shipment.")

    class Arguments:
        input = OutboundShipmentCreateInput(
            required=True,
            description="Fields required to create an outbound shipment.",
        )

    class Meta:
        description = "Creates a new outbound shipment for order fulfillments."
        permissions = (OrderPermissions.MANAGE_ORDERS,)
        error_type_class = ShippingError
        error_type_field = "shipping_errors"
        doc_category = DOC_CATEGORY_SHIPPING

    @classmethod
    @transaction.atomic
    def perform_mutation(cls, _root, info: ResolveInfo, /, **data):
        from ....order.models import Fulfillment
        from ....shipping import ShipmentType
        from ....shipping.models import Shipment as ShipmentModel

        input_data = data["input"]

        # Load fulfillments
        fulfillment_pks = []
        for fulfillment_id in input_data["fulfillment_ids"]:
            _, fulfillment_pk = from_global_id_or_error(fulfillment_id, "Fulfillment")
            fulfillment_pks.append(fulfillment_pk)

        fulfillments = list(
            Fulfillment.objects.filter(pk__in=fulfillment_pks)
            .select_related("order")
            .select_for_update()
        )

        if len(fulfillments) != len(fulfillment_pks):
            raise ValidationError("Some fulfillments not found.")

        # Validate fulfillments can be linked to a shipment
        cls._validate_fulfillments(fulfillments)

        # Get source and destination from first fulfillment
        # All fulfillments should have compatible addresses (validated above)
        first_fulfillment = fulfillments[0]
        source_address = cls._get_source_address(first_fulfillment)
        destination_address = first_fulfillment.order.shipping_address

        if not destination_address:
            raise ValidationError(
                "Order must have a shipping address to create outbound shipment."
            )

        # Create shipment
        shipment = ShipmentModel.objects.create(
            source=source_address,
            destination=destination_address,
            shipment_type=ShipmentType.OUTBOUND,
            carrier=input_data.get("carrier"),
            tracking_url=input_data.get("tracking_url"),
            shipping_cost_amount=input_data.get("shipping_cost"),
            currency=input_data.get("currency", "USD"),
        )

        # Link fulfillments to shipment
        for fulfillment in fulfillments:
            fulfillment.shipment = shipment
            fulfillment.tracking_url = input_data.get("tracking_url", "")
            fulfillment.save(update_fields=["shipment", "tracking_url"])

        return OutboundShipmentCreate(shipment=shipment)

    @classmethod
    def _validate_fulfillments(cls, fulfillments):
        """Validate that fulfillments can be added to a shipment together."""
        if not fulfillments:
            raise ValidationError("At least one fulfillment is required.")

        # Check all fulfillments are WAITING_FOR_APPROVAL
        from ....order import FulfillmentStatus

        for fulfillment in fulfillments:
            if fulfillment.status != FulfillmentStatus.WAITING_FOR_APPROVAL:
                raise ValidationError(
                    f"Fulfillment {fulfillment.composed_id} must be in "
                    f"WAITING_FOR_APPROVAL status, got {fulfillment.status}."
                )

            if fulfillment.shipment_id:
                raise ValidationError(
                    f"Fulfillment {fulfillment.composed_id} is already "
                    f"linked to a shipment."
                )

        # Check all fulfillments have same destination
        destinations = set()
        for fulfillment in fulfillments:
            if fulfillment.order.shipping_address:
                dest_key = (
                    fulfillment.order.shipping_address.street_address_1,
                    fulfillment.order.shipping_address.city,
                    fulfillment.order.shipping_address.postal_code,
                )
                destinations.add(dest_key)

        if len(destinations) > 1:
            raise ValidationError(
                "All fulfillments must have the same destination address."
            )

    @classmethod
    def _get_source_address(cls, fulfillment):
        """Get source address (warehouse address) for fulfillment."""
        # Get warehouse from fulfillment lines
        fulfillment_line = fulfillment.lines.select_related(
            "stock__warehouse__address"
        ).first()

        if not fulfillment_line or not fulfillment_line.stock:
            raise ValidationError(
                f"Fulfillment {fulfillment.composed_id} has no stock information."
            )

        warehouse = fulfillment_line.stock.warehouse
        if not warehouse.address:
            raise ValidationError(
                f"Warehouse {warehouse.name} does not have an address."
            )

        return warehouse.address
