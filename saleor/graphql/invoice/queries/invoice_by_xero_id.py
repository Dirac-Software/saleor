import graphene

from ....invoice.models import Invoice
from ....permission.enums import OrderPermissions
from ...core import ResolveInfo
from ...core.context import get_database_connection_name
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ..types import Invoice as InvoiceType


def resolve_invoice_by_xero_id(
    _root, info: ResolveInfo, xero_invoice_id: str
) -> InvoiceType | None:
    """Look up an Invoice by Xero invoice ID.

    Returns None if not found.
    """
    if not info.context.user.has_perm(OrderPermissions.MANAGE_ORDERS):
        return None

    database_connection_name = get_database_connection_name(info.context)

    try:
        invoice = (
            Invoice.objects.using(database_connection_name)
            .select_related("order", "fulfillment")
            .get(xero_invoice_id=xero_invoice_id)
        )
        return invoice
    except Invoice.DoesNotExist:
        return None
