import logging

import graphene

from ....order.error_codes import OrderErrorCode
from ...core import ResolveInfo
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.types import OrderError
from ...plugins.dataloaders import get_plugin_manager_promise

logger = logging.getLogger(__name__)


class XeroTaxCodeSummary(graphene.ObjectType):
    code = graphene.String(required=True, description="Xero tax type code.")
    name = graphene.String(required=True, description="Xero tax code display name.")
    rate = graphene.Float(
        required=True, description="Effective tax rate (e.g. 0.2 for 20%)."
    )

    class Meta:
        description = "A Xero tax code."
        doc_category = DOC_CATEGORY_ORDERS


class AvailableXeroTaxCodes(graphene.ObjectType):
    tax_codes = graphene.List(
        graphene.NonNull(XeroTaxCodeSummary),
        description="List of available Xero tax codes.",
    )
    errors = graphene.List(
        graphene.NonNull(OrderError),
        description="Errors encountered while fetching tax codes.",
    )

    class Meta:
        description = (
            "Available Xero tax codes for configuring tax class country rates."
        )
        doc_category = DOC_CATEGORY_ORDERS


def resolve_available_xero_tax_codes(
    _root, info: ResolveInfo, channel_slug: str
) -> AvailableXeroTaxCodes:
    manager = get_plugin_manager_promise(info.context).get()

    try:
        tax_codes = manager.xero_list_tax_codes(domain=channel_slug)
    except Exception:
        logger.exception(
            "availableXeroTaxCodes: exception for channel=%s", channel_slug
        )
        return AvailableXeroTaxCodes(
            tax_codes=[],
            errors=[
                OrderError(
                    code=OrderErrorCode.INVALID.value,
                    message="Failed to fetch tax codes from Xero.",
                )
            ],
        )

    return AvailableXeroTaxCodes(
        tax_codes=[
            XeroTaxCodeSummary(code=t["code"], name=t["name"], rate=t.get("rate", 0.0))
            for t in tax_codes
        ],
        errors=[],
    )
