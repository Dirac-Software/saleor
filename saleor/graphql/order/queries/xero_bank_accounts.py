import logging

import graphene

from ....order.error_codes import OrderErrorCode
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
    _root, info: ResolveInfo, channel_slug: str
) -> AvailableXeroBankAccounts:
    manager = get_plugin_manager_promise(info.context).get()

    try:
        accounts = manager.xero_list_bank_accounts(domain=channel_slug)
    except Exception:
        logger.exception(
            "availableXeroBankAccounts: exception for channel=%s", channel_slug
        )
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
