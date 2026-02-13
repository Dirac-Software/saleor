import graphene
from django.core.exceptions import ValidationError
from django.utils import timezone

from ....order import models
from ....order.error_codes import OrderErrorCode
from ....permission.enums import OrderPermissions
from ...core import ResolveInfo
from ...core.context import SyncWebhookControlContext
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.mutations import BaseMutation
from ...core.scalars import DateTime
from ...core.types import OrderError
from ..types import Fulfillment


class FulfillmentMarkProformaInvoicePaid(BaseMutation):
    fulfillment = graphene.Field(Fulfillment, description="Updated fulfillment.")

    class Arguments:
        id = graphene.ID(
            required=True,
            description="ID of the fulfillment to mark proforma invoice as paid.",
        )
        paid_at = DateTime(
            required=False,
            description="Timestamp when proforma invoice was paid. Defaults to now.",
        )

    class Meta:
        description = "Mark a fulfillment's proforma invoice as paid."
        doc_category = DOC_CATEGORY_ORDERS
        permissions = (OrderPermissions.MANAGE_ORDERS,)
        error_type_class = OrderError
        error_type_field = "order_errors"

    @classmethod
    def perform_mutation(cls, _root, info: ResolveInfo, /, **data):
        fulfillment = cls.get_node_or_error(
            info,
            data["id"],
            only_type=Fulfillment,
            qs=models.Fulfillment.objects.select_related("order"),
        )

        if not hasattr(fulfillment, "proforma_invoice"):
            raise ValidationError(
                {
                    "id": ValidationError(
                        "This fulfillment does not have a proforma invoice.",
                        code=OrderErrorCode.INVALID.value,
                    )
                }
            )

        if fulfillment.proforma_invoice_paid:
            raise ValidationError(
                {
                    "id": ValidationError(
                        "Proforma invoice is already marked as paid.",
                        code=OrderErrorCode.INVALID.value,
                    )
                }
            )

        paid_at = data.get("paid_at") or timezone.now()

        fulfillment.proforma_invoice_paid = True
        fulfillment.proforma_invoice_paid_at = paid_at
        fulfillment.save(
            update_fields=["proforma_invoice_paid", "proforma_invoice_paid_at"]
        )

        from ....order.actions import try_auto_approve_fulfillment
        from ...utils import get_user_or_app_from_context

        requestor = get_user_or_app_from_context(info.context)
        user = requestor if hasattr(requestor, "is_authenticated") else None
        try_auto_approve_fulfillment(fulfillment, user=user)

        fulfillment.refresh_from_db()

        return FulfillmentMarkProformaInvoicePaid(
            fulfillment=SyncWebhookControlContext(node=fulfillment)
        )
