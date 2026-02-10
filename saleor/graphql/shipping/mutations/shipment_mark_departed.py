import graphene
from django.core.exceptions import ValidationError
from django.utils import timezone

from ....permission.enums import OrderPermissions
from ....shipping import ShipmentType
from ...core import ResolveInfo
from ...core.doc_category import DOC_CATEGORY_SHIPPING
from ...core.mutations import BaseMutation
from ...core.scalars import DateTime
from ...core.types import BaseInputObjectType, ShippingError
from ..types import Shipment


class ShipmentMarkDepartedInput(BaseInputObjectType):
    departed_at = DateTime(
        description="Timestamp when shipment departed. Defaults to current time if not provided."
    )

    class Meta:
        doc_category = DOC_CATEGORY_SHIPPING


class ShipmentMarkDeparted(BaseMutation):
    shipment = graphene.Field(Shipment, description="Updated shipment.")

    class Arguments:
        id = graphene.ID(
            required=True,
            description="ID of the shipment to mark as departed.",
        )
        input = ShipmentMarkDepartedInput(
            description="Fields to update when marking as departed.",
        )

    class Meta:
        description = "Marks an outbound shipment as departed."
        permissions = (OrderPermissions.MANAGE_ORDERS,)
        error_type_class = ShippingError
        error_type_field = "shipping_errors"
        doc_category = DOC_CATEGORY_SHIPPING

    @classmethod
    def perform_mutation(cls, _root, info: ResolveInfo, /, **data):
        shipment = cls.get_node_or_error(info, data["id"], only_type=Shipment)

        # Validate shipment type
        if shipment.shipment_type != ShipmentType.OUTBOUND:
            raise ValidationError(
                f"Cannot mark {shipment.shipment_type} shipment as departed. "
                "Only outbound shipments can be marked as departed."
            )

        # Check if already departed
        if shipment.departed_at is not None:
            raise ValidationError(
                f"Shipment {shipment.id} is already marked as departed at {shipment.departed_at}."
            )

        # Set departed_at timestamp
        input_data = data.get("input") or {}
        departed_at = input_data.get("departed_at") or timezone.now()
        shipment.departed_at = departed_at
        shipment.save(update_fields=["departed_at"])

        return ShipmentMarkDeparted(shipment=shipment)
