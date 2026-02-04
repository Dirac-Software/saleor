import graphene
from django.core.exceptions import ValidationError

from ....inventory.error_codes import ReceiptErrorCode
from ....inventory.stock_management import start_receipt
from ....permission.enums import ProductPermissions
from ...core import ResolveInfo
from ...core.doc_category import DOC_CATEGORY_PRODUCTS
from ...core.mutations import ModelMutation
from ...core.types import ReceiptError
from ...core.utils import from_global_id_or_error
from ...shipping.types import Shipment
from ..types import Receipt


class ReceiptStart(ModelMutation):
    """Start a new receipt for an inbound shipment."""

    class Arguments:
        shipment_id = graphene.ID(
            required=True,
            description="ID of the shipment to receive.",
        )

    class Meta:
        description = "Start receiving goods from an inbound shipment."
        model = Receipt
        object_type = Receipt
        permissions = (ProductPermissions.MANAGE_PRODUCTS,)
        error_type_class = ReceiptError
        error_type_field = "receipt_errors"
        doc_category = DOC_CATEGORY_PRODUCTS

    @classmethod
    def perform_mutation(cls, root, info: ResolveInfo, /, **data):
        shipment_id = data["shipment_id"]

        # Get shipment
        _, shipment_pk = from_global_id_or_error(
            shipment_id, "Shipment"
        )

        from ....shipping.models import Shipment as ShipmentModel

        try:
            shipment = ShipmentModel.objects.get(pk=shipment_pk)
        except ShipmentModel.DoesNotExist:
            raise ValidationError(
                {
                    "shipment_id": ValidationError(
                        "Shipment not found.",
                        code=ReceiptErrorCode.NOT_FOUND.value,
                    )
                }
            )

        # Start receipt
        try:
            receipt = start_receipt(shipment, user=info.context.user)
        except ValueError as e:
            raise ValidationError(
                {
                    "shipment_id": ValidationError(
                        str(e),
                        code=ReceiptErrorCode.INVALID.value,
                    )
                }
            )

        return ReceiptStart(receipt=receipt)
