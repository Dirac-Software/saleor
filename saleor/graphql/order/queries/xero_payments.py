import os

import graphene
import requests

from ....order.error_codes import OrderErrorCode
from ....permission.enums import OrderPermissions
from ...core import ResolveInfo
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.scalars import Decimal
from ...core.types import OrderError


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
            .select_related("user")
            .get(id=UUID(order_uuid))
        )
    except Exception:
        return AvailableXeroPayments(
            payments=[],
            errors=[
                OrderError(
                    code=OrderErrorCode.NOT_FOUND.value,
                    message="Order not found.",
                )
            ],
        )

    # Check if order has user with Xero contact ID
    if not order.user:
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
                    message="Customer not linked to Xero. Sync a payment first to link customer.",
                )
            ],
        )

    # Fetch payments from Xero via dirac service
    dirac_url = os.getenv("DIRAC_SERVICE_URL", "http://localhost:8086")

    try:
        response = requests.get(
            f"{dirac_url}/api/contact-payments/{order.user.xero_contact_id}",
            params={"limit": 5},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        xero_payments = data.get("payments", [])
    except requests.exceptions.RequestException as e:
        return AvailableXeroPayments(
            payments=[],
            errors=[
                OrderError(
                    code=OrderErrorCode.INVALID.value,
                    message=f"Failed to fetch payments from Xero: {str(e)}",
                )
            ],
        )

    # Convert to GraphQL types
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
