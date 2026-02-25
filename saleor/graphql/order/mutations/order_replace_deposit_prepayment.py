from decimal import Decimal as DecimalType

import graphene
from django.core.exceptions import ValidationError

from ....order.error_codes import OrderErrorCode
from ....payment import CustomPaymentChoices, TransactionKind
from ....permission.enums import OrderPermissions
from ...core import ResolveInfo
from ...core.context import SyncWebhookControlContext
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.mutations import BaseMutation
from ...core.scalars import Decimal
from ...core.types import OrderError
from ...plugins.dataloaders import get_plugin_manager_promise
from ..types import Order


class OrderReplaceDepositPrepayment(BaseMutation):
    order = graphene.Field(Order, description="Order with updated deposit prepayment.")

    class Arguments:
        id = graphene.ID(required=True, description="ID of the order.")
        xero_deposit_prepayment_id = graphene.String(
            required=True,
            description="New Xero prepayment ID for this order's deposit.",
        )
        percentage = Decimal(
            required=False,
            description="Updated deposit percentage (0-100). If omitted, existing value is kept.",
        )
        xero_bank_account_code = graphene.String(
            required=False,
            description="Updated Xero bank account code. If omitted, existing value is kept.",
        )
        xero_bank_account_sort_code = graphene.String(
            required=False,
            description="Updated bank sort code. If omitted, existing value is kept.",
        )
        xero_bank_account_number = graphene.String(
            required=False,
            description="Updated bank account number. If omitted, existing value is kept.",
        )

    class Meta:
        description = (
            "Replace the Xero deposit prepayment tracked by Saleor for an order. "
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
        xero_deposit_prepayment_id,
        percentage=None,
        xero_bank_account_code=None,
        xero_bank_account_sort_code=None,
        xero_bank_account_number=None,
    ):
        order = cls.get_node_or_error(info, id, only_type=Order)
        cls.check_channel_permissions(info, [order.channel_id])

        if not order.deposit_required:
            raise ValidationError(
                "Cannot replace deposit prepayment: this order does not require a deposit.",
                code=OrderErrorCode.INVALID.value,
            )

        if order.deposit_paid_at:
            raise ValidationError(
                "Cannot replace deposit prepayment: the deposit has already been paid and recorded.",
                code=OrderErrorCode.INVALID.value,
            )

        if percentage is not None and not (
            DecimalType(0) <= percentage <= DecimalType(100)
        ):
            raise ValidationError(
                {
                    "percentage": ValidationError(
                        "Deposit percentage must be between 0 and 100.",
                        code=OrderErrorCode.INVALID.value,
                    )
                }
            )

        update_fields = ["xero_deposit_prepayment_id"]
        order.xero_deposit_prepayment_id = xero_deposit_prepayment_id

        if percentage is not None:
            order.deposit_percentage = percentage
            update_fields.append("deposit_percentage")

        if xero_bank_account_code is not None:
            order.xero_bank_account_code = xero_bank_account_code
            update_fields.append("xero_bank_account_code")
        if xero_bank_account_sort_code is not None:
            order.xero_bank_account_sort_code = xero_bank_account_sort_code
            update_fields.append("xero_bank_account_sort_code")
        if xero_bank_account_number is not None:
            order.xero_bank_account_number = xero_bank_account_number
            update_fields.append("xero_bank_account_number")

        order.save(update_fields=update_fields)

        manager = get_plugin_manager_promise(info.context).get()
        response = manager.xero_check_prepayment_status(xero_deposit_prepayment_id)
        if response and response.get("isPaid"):
            from ....order.utils import record_external_payment

            already_recorded = order.payments.filter(
                psp_reference=xero_deposit_prepayment_id
            ).exists()
            if not already_recorded:
                amount = DecimalType(str(response["amountPaid"]))
                record_external_payment(
                    order=order,
                    amount=amount,
                    gateway=CustomPaymentChoices.XERO,
                    psp_reference=xero_deposit_prepayment_id,
                    transaction_kind=TransactionKind.CAPTURE,
                    metadata={
                        "source": "replace_deposit_prepayment",
                        "datePaid": response.get("datePaid", ""),
                    },
                    manager=manager,
                )
            order.refresh_from_db()

        return OrderReplaceDepositPrepayment(order=SyncWebhookControlContext(order))
