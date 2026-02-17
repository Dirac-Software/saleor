import graphene
from django.core.exceptions import ValidationError

from ....invoice import InvoiceType as InvoiceTypeEnum
from ....invoice.models import Invoice
from ....order.error_codes import OrderErrorCode
from ....order.models import Fulfillment
from ....permission.enums import OrderPermissions
from ...core import ResolveInfo
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.mutations import BaseMutation
from ...core.types import OrderError
from ..types import Invoice as InvoiceType


class InvoiceCreateFinal(BaseMutation):
    invoice = graphene.Field(InvoiceType, description="Created invoice record.")

    class Arguments:
        fulfillment_id = graphene.ID(
            required=True, description="ID of the fulfillment."
        )
        xero_invoice_id = graphene.String(
            required=True, description="Xero invoice ID to link."
        )
        invoice_number = graphene.String(
            required=False, description="Invoice number from Xero."
        )

    class Meta:
        description = (
            "Create an Invoice record linking a Xero final invoice to a fulfillment. "
            "Called by external integration after creating invoice in Xero."
        )
        doc_category = DOC_CATEGORY_ORDERS
        permissions = (OrderPermissions.MANAGE_ORDERS,)
        error_type_class = OrderError
        error_type_field = "order_errors"

    @classmethod
    def clean_input(cls, xero_invoice_id):
        if not xero_invoice_id or not xero_invoice_id.strip():
            raise ValidationError(
                {
                    "xero_invoice_id": ValidationError(
                        "Xero invoice ID cannot be empty.",
                        code=OrderErrorCode.REQUIRED.value,
                    )
                }
            )

        if Invoice.objects.filter(xero_invoice_id=xero_invoice_id.strip()).exists():
            raise ValidationError(
                {
                    "xero_invoice_id": ValidationError(
                        "An invoice with this Xero invoice ID already exists.",
                        code=OrderErrorCode.UNIQUE.value,
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
        fulfillment_id,
        xero_invoice_id,
        invoice_number=None,
    ):
        from ...order.types import Fulfillment as FulfillmentType

        fulfillment = cls.get_node_or_error(
            info, fulfillment_id, only_type=FulfillmentType
        )
        cls.check_channel_permissions(info, [fulfillment.order.channel_id])
        cls.clean_input(xero_invoice_id)

        # Create Invoice record
        invoice = Invoice.objects.create(
            order=fulfillment.order,
            fulfillment=fulfillment,
            xero_invoice_id=xero_invoice_id.strip(),
            number=invoice_number,
            type=InvoiceTypeEnum.FINAL,
        )

        return InvoiceCreateFinal(invoice=invoice)
