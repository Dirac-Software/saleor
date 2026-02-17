import os
from decimal import Decimal

import graphene
from django.core.exceptions import ValidationError
from django.utils.timezone import now
from requests import HTTPError

from ....core.http_client import HTTPClient
from ....order.error_codes import OrderErrorCode
from ....order.utils import record_external_payment
from ....payment import CustomPaymentChoices, TransactionKind
from ....payment.exceptions import XeroValidationError
from ....payment.models import Payment
from ....permission.enums import OrderPermissions
from ...core import ResolveInfo
from ...core.context import SyncWebhookControlContext
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.mutations import BaseMutation
from ...core.types import OrderError
from ...payment.types import Payment as PaymentType
from ...plugins.dataloaders import get_plugin_manager_promise
from ..types import Order


class OrderSyncXeroPayment(BaseMutation):
    payment = graphene.Field(PaymentType, description="Created Xero payment.")
    order = graphene.Field(Order, description="Order with synced payment.")

    class Arguments:
        order_id = graphene.ID(required=True, description="ID of the order.")
        xero_payment_id = graphene.String(
            required=True, description="Xero payment ID to validate and sync."
        )
        is_deposit = graphene.Boolean(
            required=False,
            default_value=False,
            description="Whether this is a deposit payment.",
        )

    class Meta:
        description = (
            "Sync a Xero payment to an order by validating with Xero API. "
            "Only callable by external integrations after receiving Xero webhooks. "
            "Payment amount is fetched from Xero, not provided by caller."
        )
        doc_category = DOC_CATEGORY_ORDERS
        permissions = (OrderPermissions.MANAGE_ORDERS,)
        error_type_class = OrderError
        error_type_field = "order_errors"

    @classmethod
    def clean_input(cls, order, xero_payment_id, is_deposit):
        if not xero_payment_id or not xero_payment_id.strip():
            raise ValidationError(
                {
                    "xero_payment_id": ValidationError(
                        "Xero payment ID cannot be empty.",
                        code=OrderErrorCode.REQUIRED.value,
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
    def perform_mutation(  # type: ignore[override]
        cls,
        _root,
        info: ResolveInfo,
        /,
        *,
        order_id,
        xero_payment_id,
        is_deposit=False,
    ):
        order = cls.get_node_or_error(info, order_id, only_type=Order)
        cls.check_channel_permissions(info, [order.channel_id])
        cls.clean_input(order, xero_payment_id, is_deposit)

        # Validate payment via dirac service and fetch amount
        dirac_url = os.getenv("DIRAC_SERVICE_URL", "http://localhost:8086")

        try:
            response = HTTPClient.send_request(
                "POST",
                f"{dirac_url}/api/validate-xero-payment",
                json={"payment_id": xero_payment_id.strip()},
            )
            response.raise_for_status()
            xero_data = response.json()

            if xero_data.get("status") != "AUTHORISED":
                raise XeroValidationError(
                    f"Payment {xero_payment_id} status is {xero_data.get('status')}, expected AUTHORISED"
                )

            xero_data["amount"] = Decimal(str(xero_data["amount"]))

        except HTTPError as e:
            raise ValidationError(
                {
                    "xero_payment_id": ValidationError(
                        f"Failed to validate payment with Xero: {str(e)}",
                        code=OrderErrorCode.INVALID.value,
                    )
                }
            ) from e
        except XeroValidationError as e:
            raise ValidationError(
                {
                    "xero_payment_id": ValidationError(
                        str(e), code=OrderErrorCode.INVALID.value
                    )
                }
            ) from e

        # Validate payment belongs to correct customer
        if order.user:
            if order.user.xero_contact_id:
                # User has Xero contact ID - validate it matches
                if xero_data["contact_id"] != order.user.xero_contact_id:
                    raise ValidationError(
                        {
                            "xero_payment_id": ValidationError(
                                f"Payment belongs to different customer. "
                                f"Expected contact {order.user.xero_contact_id}, "
                                f"got {xero_data['contact_id']}",
                                code=OrderErrorCode.INVALID.value,
                            )
                        }
                    )
            elif xero_data["contact_id"]:
                # Auto-populate Xero contact ID from payment
                order.user.xero_contact_id = xero_data["contact_id"]
                order.user.save(update_fields=["xero_contact_id"])

        # Use amount from Xero, not from user input
        amount = xero_data["amount"]

        metadata = {
            "is_deposit": is_deposit,
            "xero_invoice_id": xero_data["invoice_id"],
            "xero_contact_id": xero_data["contact_id"],
            "xero_date": xero_data["date"],
        }

        manager = get_plugin_manager_promise(info.context).get()

        payment = record_external_payment(
            order=order,
            amount=amount,
            gateway=CustomPaymentChoices.XERO,
            psp_reference=xero_payment_id.strip(),
            transaction_kind=TransactionKind.CAPTURE,
            metadata=metadata,
            user=info.context.user if hasattr(info.context, "user") else None,
            app=info.context.app if hasattr(info.context, "app") else None,
            manager=manager,
        )

        if is_deposit and order.deposit_threshold_met and not order.deposit_paid_at:
            order.deposit_paid_at = now()
            order.save(update_fields=["deposit_paid_at"])

        return OrderSyncXeroPayment(
            payment=payment, order=SyncWebhookControlContext(order)
        )
