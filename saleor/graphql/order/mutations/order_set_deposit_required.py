from decimal import Decimal as DecimalType

import graphene
from django.core.exceptions import ValidationError

from ....order.error_codes import OrderErrorCode
from ....permission.enums import OrderPermissions
from ...core import ResolveInfo
from ...core.context import SyncWebhookControlContext
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.mutations import BaseMutation
from ...core.scalars import Decimal
from ...core.types import OrderError
from ..types import Order


class OrderSetDepositRequired(BaseMutation):
    order = graphene.Field(Order, description="Order with updated deposit settings.")

    class Arguments:
        id = graphene.ID(required=True, description="ID of the order.")
        required = graphene.Boolean(
            required=True, description="Whether deposit is required."
        )
        percentage = Decimal(
            required=False,
            description="Percentage of total required as deposit (0-100).",
        )

    class Meta:
        description = "Set whether an order requires a deposit before fulfillment."
        doc_category = DOC_CATEGORY_ORDERS
        permissions = (OrderPermissions.MANAGE_ORDERS,)
        error_type_class = OrderError
        error_type_field = "order_errors"

    @classmethod
    def clean_input(cls, required, percentage):
        if required and percentage is not None:
            if not (DecimalType(0) <= percentage <= DecimalType(100)):
                raise ValidationError(
                    {
                        "percentage": ValidationError(
                            "Deposit percentage must be between 0 and 100.",
                            code=OrderErrorCode.INVALID.value,
                        )
                    }
                )

    @classmethod
    def perform_mutation(  # type: ignore[override]
        cls, _root, info: ResolveInfo, /, *, id, required, percentage=None
    ):
        order = cls.get_node_or_error(info, id, only_type=Order)
        cls.check_channel_permissions(info, [order.channel_id])
        cls.clean_input(required, percentage)

        order.deposit_required = required
        order.deposit_percentage = percentage if required else None
        order.save(update_fields=["deposit_required", "deposit_percentage"])

        return OrderSetDepositRequired(order=SyncWebhookControlContext(order))
