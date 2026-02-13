import graphene
from django.core.exceptions import ValidationError
from django.utils.timezone import now

from ....order.error_codes import OrderErrorCode
from ....payment import ChargeStatus, CustomPaymentChoices
from ....payment.models import Payment
from ....permission.enums import OrderPermissions
from ...core import ResolveInfo
from ...core.context import SyncWebhookControlContext
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.mutations import BaseMutation
from ...core.scalars import DateTime
from ...core.types import OrderError
from ...payment.types import Payment as PaymentType
from ..types import Order


class OrderAddXeroPayment(BaseMutation):
    payment = graphene.Field(PaymentType, description="Created Xero payment.")
    order = graphene.Field(Order, description="Order with added payment.")

    class Arguments:
        order_id = graphene.ID(required=True, description="ID of the order.")
        xero_payment_id = graphene.String(
            required=True, description="Xero payment ID (Invoice.PaymentID)."
        )
        amount = graphene.Argument(
            "saleor.graphql.core.scalars.Decimal",
            required=True,
            description="Amount of this payment.",
        )
        is_deposit = graphene.Boolean(
            required=False,
            default_value=False,
            description="Whether this is a deposit payment.",
        )
        paid_at = DateTime(
            required=False,
            description="Timestamp when payment was made. Defaults to now.",
        )

    class Meta:
        description = "Add a Xero payment to an order. Replaces manual 'mark as paid' functionality."
        doc_category = DOC_CATEGORY_ORDERS
        permissions = (OrderPermissions.MANAGE_ORDERS,)
        error_type_class = OrderError
        error_type_field = "order_errors"

    @classmethod
    def clean_input(cls, order, xero_payment_id, amount, is_deposit):
        from decimal import Decimal

        if not xero_payment_id or not xero_payment_id.strip():
            raise ValidationError(
                {
                    "xero_payment_id": ValidationError(
                        "Xero payment ID cannot be empty.",
                        code=OrderErrorCode.REQUIRED.value,
                    )
                }
            )

        if amount <= Decimal(0):
            raise ValidationError(
                {
                    "amount": ValidationError(
                        "Amount must be greater than zero.",
                        code=OrderErrorCode.INVALID.value,
                    )
                }
            )

        if Payment.objects.filter(
            order=order, psp_reference=xero_payment_id.strip()
        ).exists():
            raise ValidationError(
                {
                    "xero_payment_id": ValidationError(
                        "A payment with this Xero payment ID already exists for this order.",
                        code=OrderErrorCode.UNIQUE.value,
                    )
                }
            )

        if is_deposit and not order.deposit_required:
            raise ValidationError(
                {
                    "is_deposit": ValidationError(
                        "Cannot add deposit payment to order that does not require deposit.",
                        code=OrderErrorCode.INVALID.value,
                    )
                }
            )

    @classmethod
    def perform_mutation(
        cls,
        _root,
        info: ResolveInfo,
        /,
        *,
        order_id,
        xero_payment_id,
        amount,
        is_deposit=False,
        paid_at=None,
    ):
        order = cls.get_node_or_error(info, order_id, only_type=Order)
        cls.check_channel_permissions(info, [order.channel_id])
        cls.clean_input(order, xero_payment_id, amount, is_deposit)

        metadata = {"is_deposit": is_deposit}
        if paid_at:
            metadata["paid_at"] = paid_at.isoformat()

        payment = Payment.objects.create(
            order=order,
            gateway=CustomPaymentChoices.XERO,
            psp_reference=xero_payment_id.strip(),
            total=amount,
            captured_amount=amount,
            charge_status=ChargeStatus.FULLY_CHARGED,
            currency=order.currency,
            is_active=True,
            billing_email=order.user_email,
            metadata=metadata,
        )

        if is_deposit and order.deposit_threshold_met and not order.deposit_paid_at:
            order.deposit_paid_at = paid_at or now()
            order.save(update_fields=["deposit_paid_at"])

        return OrderAddXeroPayment(
            payment=payment, order=SyncWebhookControlContext(order)
        )
