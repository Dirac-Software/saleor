import logging

import graphene

from ....order.error_codes import OrderErrorCode
from ....permission.enums import OrderPermissions
from ...core import ResolveInfo
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.scalars import Decimal
from ...core.types import OrderError
from ...plugins.dataloaders import get_plugin_manager_promise

logger = logging.getLogger(__name__)


class XeroPaymentSummary(graphene.ObjectType):
    payment_id = graphene.String(
        required=True, description="Xero payment ID (PaymentID)."
    )
    amount = Decimal(required=True, description="Payment amount.")
    date = graphene.String(required=True, description="Payment date from Xero.")
    invoice_number = graphene.String(description="Associated invoice number.")
    status = graphene.String(required=True, description="Payment status in Xero.")


class AvailableXeroPayments(graphene.ObjectType):
    payments = graphene.List(
        graphene.NonNull(XeroPaymentSummary),
        description="List of available Xero payments for this order's customer.",
    )
    errors = graphene.List(
        graphene.NonNull(OrderError),
        description="Errors encountered while fetching payments.",
    )

    class Meta:
        description = "Available Xero payments for an order's customer."
        doc_category = DOC_CATEGORY_ORDERS


def get_xero_contact_payments(contact_id: str, manager) -> list:
    return manager.xero_list_payments(contact_id=contact_id)


def resolve_available_xero_payments(
    _root, info: ResolveInfo, order_id: str
) -> AvailableXeroPayments:
    """Fetch available Xero payments for an order's customer."""
    from uuid import UUID

    from ....order.models import Order as OrderModel
    from ...core.context import get_database_connection_name

    # Check permissions
    if not info.context.user or not info.context.user.has_perm(
        OrderPermissions.MANAGE_ORDERS
    ):
        return AvailableXeroPayments(
            payments=[],
            errors=[
                OrderError(
                    code=OrderErrorCode.REQUIRED.value,
                    message="Insufficient permissions.",
                )
            ],
        )

    # Get order using proper database connection
    database_connection_name = get_database_connection_name(info.context)
    try:
        _, order_uuid = graphene.Node.from_global_id(order_id)
        order = (
            OrderModel.objects.using(database_connection_name)
            .select_related("user", "channel")
            .get(id=UUID(order_uuid))
        )
    except (OrderModel.DoesNotExist, ValueError, TypeError):
        logger.warning("availableXeroPayments: order not found for id=%s", order_id)
        return AvailableXeroPayments(
            payments=[],
            errors=[
                OrderError(
                    code=OrderErrorCode.NOT_FOUND.value,
                    message="Order not found.",
                )
            ],
        )

    if not order.user:
        logger.warning("availableXeroPayments: order %s has no customer", order.pk)
        return AvailableXeroPayments(
            payments=[],
            errors=[
                OrderError(
                    code=OrderErrorCode.INVALID.value,
                    message="Order has no customer.",
                )
            ],
        )

    if not order.user.xero_contact_id:
        return AvailableXeroPayments(
            payments=[],
            errors=[
                OrderError(
                    code=OrderErrorCode.INVALID.value,
                    message=f"Customer {order.user.email} is not linked to Xero.",
                )
            ],
        )

    manager = get_plugin_manager_promise(info.context).get()

    logger.debug(
        "availableXeroPayments: fetching by contact_id=%s for order=%s",
        order.user.xero_contact_id,
        order.pk,
    )

    try:
        xero_payments = get_xero_contact_payments(order.user.xero_contact_id, manager)
    except Exception as e:
        logger.exception(
            "availableXeroPayments: exception calling xero_list_payments for order=%s contact_id=%s",
            order.pk,
            order.user.xero_contact_id,
        )
        return AvailableXeroPayments(
            payments=[],
            errors=[
                OrderError(
                    code=OrderErrorCode.INVALID.value,
                    message=f"Failed to fetch payments from Xero: {str(e)}",
                )
            ],
        )

    logger.debug(
        "availableXeroPayments: xero_list_payments returned %d payment(s) for order=%s",
        len(xero_payments),
        order.pk,
    )

    if xero_payments is None or (
        isinstance(xero_payments, list) and len(xero_payments) == 0
    ):
        logger.warning(
            "availableXeroPayments: no payments returned from Xero for order=%s "
            "contact_id=%s — check that a XERO_LIST_PAYMENTS webhook is registered",
            order.pk,
            order.user.xero_contact_id,
        )
        return AvailableXeroPayments(
            payments=[],
            errors=[
                OrderError(
                    code=OrderErrorCode.NOT_FOUND.value,
                    message=(
                        f"No Xero payments found for {order.user.xero_contact_id}. "
                        "Ensure the XERO_LIST_PAYMENTS webhook is registered in the Xero app."
                    ),
                )
            ],
        )

    payments = [
        XeroPaymentSummary(
            payment_id=p["payment_id"],
            amount=p["amount"],
            date=p["date"],
            invoice_number=p["invoice_number"],
            status=p["status"],
        )
        for p in xero_payments
    ]

    return AvailableXeroPayments(payments=payments, errors=[])
