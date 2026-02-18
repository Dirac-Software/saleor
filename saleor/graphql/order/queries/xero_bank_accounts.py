import logging

import graphene

from ....order.error_codes import OrderErrorCode
from ....permission.enums import OrderPermissions
from ...core import ResolveInfo
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.types import OrderError
from ...plugins.dataloaders import get_plugin_manager_promise

logger = logging.getLogger(__name__)


class XeroBankAccountSummary(graphene.ObjectType):
    code = graphene.String(required=True, description="Xero bank account code.")
    name = graphene.String(required=True, description="Xero bank account name.")

    class Meta:
        description = "A Xero bank account."
        doc_category = DOC_CATEGORY_ORDERS


class AvailableXeroBankAccounts(graphene.ObjectType):
    bank_accounts = graphene.List(
        graphene.NonNull(XeroBankAccountSummary),
        description="List of available Xero bank accounts.",
    )
    errors = graphene.List(
        graphene.NonNull(OrderError),
        description="Errors encountered while fetching bank accounts.",
    )

    class Meta:
        description = "Available Xero bank accounts for creating prepayments."
        doc_category = DOC_CATEGORY_ORDERS


def resolve_available_xero_bank_accounts(
    _root, info: ResolveInfo, order_id: str
) -> AvailableXeroBankAccounts:
    """Fetch available Xero bank accounts for the tenant linked to this order's channel."""
    from uuid import UUID

    from ....order.models import Order as OrderModel
    from ...core.context import get_database_connection_name

    if not info.context.user or not info.context.user.has_perm(
        OrderPermissions.MANAGE_ORDERS
    ):
        return AvailableXeroBankAccounts(
            bank_accounts=[],
            errors=[
                OrderError(
                    code=OrderErrorCode.REQUIRED.value,
                    message="Insufficient permissions.",
                )
            ],
        )

    database_connection_name = get_database_connection_name(info.context)
    try:
        _, order_uuid = graphene.Node.from_global_id(order_id)
        order = (
            OrderModel.objects.using(database_connection_name)
            .select_related("channel")
            .get(id=UUID(order_uuid))
        )
    except (OrderModel.DoesNotExist, ValueError, TypeError):
        return AvailableXeroBankAccounts(
            bank_accounts=[],
            errors=[
                OrderError(
                    code=OrderErrorCode.NOT_FOUND.value,
                    message="Order not found.",
                )
            ],
        )

    manager = get_plugin_manager_promise(info.context).get()

    try:
        accounts = manager.xero_list_bank_accounts(domain=order.channel.slug)
    except Exception:
        logger.exception("availableXeroBankAccounts: exception for order=%s", order.pk)
        return AvailableXeroBankAccounts(
            bank_accounts=[],
            errors=[
                OrderError(
                    code=OrderErrorCode.INVALID.value,
                    message="Failed to fetch bank accounts from Xero.",
                )
            ],
        )

    return AvailableXeroBankAccounts(
        bank_accounts=[
            XeroBankAccountSummary(code=a["code"], name=a["name"]) for a in accounts
        ],
        errors=[],
    )
