import graphene
from django.core.exceptions import ValidationError

from ....order.error_codes import OrderErrorCode
from ....payment import CustomPaymentChoices, TransactionKind
from ....permission.enums import OrderPermissions
from ...core import ResolveInfo
from ...core.context import SyncWebhookControlContext
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.mutations import BaseMutation
from ...core.types import OrderError
from ...plugins.dataloaders import get_plugin_manager_promise
from ..types import Fulfillment, Order


class FulfillmentReplacePrepayment(BaseMutation):
    fulfillment = graphene.Field(
        Fulfillment, description="Fulfillment with updated proforma prepayment."
    )
    order = graphene.Field(Order, description="Order for the updated fulfillment.")

    class Arguments:
        id = graphene.ID(required=True, description="ID of the fulfillment.")
        xero_proforma_prepayment_id = graphene.String(
            required=True,
            description="New Xero prepayment ID for this fulfillment's proforma.",
        )

    class Meta:
        description = (
            "Replace the Xero proforma prepayment tracked by Saleor for a fulfillment. "
            "Use when the original prepayment was voided in Xero and a new one created manually. "
            "Staff must void the old prepayment in Xero before calling this mutation."
        )
        doc_category = DOC_CATEGORY_ORDERS
        permissions = (OrderPermissions.MANAGE_ORDERS,)
        error_type_class = OrderError
        error_type_field = "order_errors"

    @classmethod
    def perform_mutation(  # type: ignore[override]
        cls,
        _root,
        info: ResolveInfo,
        /,
        *,
        id,
        xero_proforma_prepayment_id,
    ):
        fulfillment = cls.get_node_or_error(info, id, only_type=Fulfillment)
        order = fulfillment.order
        cls.check_channel_permissions(info, [order.channel_id])

        if not fulfillment.xero_proforma_prepayment_id:
            raise ValidationError(
                "Cannot replace proforma prepayment: this fulfillment has no proforma prepayment ID set.",
                code=OrderErrorCode.INVALID.value,
            )

        already_paid = order.payments.filter(
            psp_reference=fulfillment.xero_proforma_prepayment_id
        ).exists()
        if already_paid:
            raise ValidationError(
                "Cannot replace proforma prepayment: the proforma has already been paid and recorded.",
                code=OrderErrorCode.INVALID.value,
            )

        fulfillment.xero_proforma_prepayment_id = xero_proforma_prepayment_id
        fulfillment.save(update_fields=["xero_proforma_prepayment_id"])

        manager = get_plugin_manager_promise(info.context).get()
        response = manager.xero_check_prepayment_status(xero_proforma_prepayment_id)
        if response and response.get("isPaid"):
            from ....order.utils import record_external_payment

            already_recorded = order.payments.filter(
                psp_reference=xero_proforma_prepayment_id
            ).exists()
            if not already_recorded:
                from decimal import Decimal as DecimalType

                amount = DecimalType(str(response["amountPaid"]))
                record_external_payment(
                    order=order,
                    amount=amount,
                    gateway=CustomPaymentChoices.XERO,
                    psp_reference=xero_proforma_prepayment_id,
                    transaction_kind=TransactionKind.CAPTURE,
                    metadata={
                        "source": "replace_proforma_prepayment",
                        "datePaid": response.get("datePaid", ""),
                    },
                    manager=manager,
                )
            fulfillment.refresh_from_db()

        return FulfillmentReplacePrepayment(
            fulfillment=SyncWebhookControlContext(node=fulfillment),
            order=SyncWebhookControlContext(order),
        )
