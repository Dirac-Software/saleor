import graphene

from ...permission.enums import OrderPermissions
from ..core.doc_category import DOC_CATEGORY_ORDERS
from ..core.fields import PermissionsField
from .mutations import (
    InvoiceDelete,
    InvoiceRequestDelete,
    InvoiceSendNotification,
    InvoiceUpdate,
)
from .mutations.invoice_create_final import InvoiceCreateFinal
from .queries.invoice_by_xero_id import resolve_invoice_by_xero_id
from .types import Invoice


class InvoiceQueries(graphene.ObjectType):
    invoice_by_xero_id = graphene.Field(
        Invoice,
        description="Look up an Invoice by Xero invoice ID.",
        xero_invoice_id=graphene.Argument(
            graphene.String,
            required=True,
            description="Xero invoice ID to look up.",
        ),
    )

    @staticmethod
    def resolve_invoice_by_xero_id(_root, info, *, xero_invoice_id):
        return resolve_invoice_by_xero_id(_root, info, xero_invoice_id)


class InvoiceMutations(graphene.ObjectType):
    invoice_request_delete = InvoiceRequestDelete.Field()
    invoice_delete = InvoiceDelete.Field()
    invoice_update = InvoiceUpdate.Field()
    invoice_send_notification = InvoiceSendNotification.Field()
    invoice_create_final = InvoiceCreateFinal.Field()
