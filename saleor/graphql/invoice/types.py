import graphene

from ...invoice import models
from ..core.context import SyncWebhookControlContext
from ..core.scalars import DateTime
from ..core.types import Job, ModelObjectType
from ..meta.types import ObjectWithMetadata
from ..order.dataloaders import OrderByIdLoader
from .enums import InvoiceTypeEnum


class Invoice(ModelObjectType[models.Invoice]):
    number = graphene.String(description="Invoice number.")
    external_url = graphene.String(
        description="URL to view an invoice.",
        required=False,
        deprecation_reason="Use `url` field.",
    )
    created_at = DateTime(
        required=True, description="Date and time at which invoice was created."
    )
    updated_at = DateTime(
        required=True, description="Date and time at which invoice was updated."
    )
    message = graphene.String(description="Message associated with an invoice.")
    url = graphene.String(description="URL to view/download an invoice.")
    order = graphene.Field(
        "saleor.graphql.order.types.Order",
        description="Order related to the invoice.",
    )
    xero_invoice_id = graphene.String(description="Xero invoice ID.")
    type = graphene.Field(InvoiceTypeEnum, description="Invoice type.")
    fulfillment = graphene.Field(
        "saleor.graphql.order.types.Fulfillment",
        description="Fulfillment related to the invoice.",
    )

    class Meta:
        description = "Represents an Invoice."
        interfaces = [ObjectWithMetadata, Job, graphene.relay.Node]
        model = models.Invoice

    @staticmethod
    def resolve_order(root: models.Invoice, info):
        def _wrap_with_sync_webhook_control_context(order):
            return SyncWebhookControlContext(node=order)

        return (
            OrderByIdLoader(info.context)
            .load(root.order_id)
            .then(_wrap_with_sync_webhook_control_context)
        )

    @staticmethod
    def resolve_fulfillment(root: models.Invoice, info):
        if root.fulfillment:
            return SyncWebhookControlContext(node=root.fulfillment)
        return None
