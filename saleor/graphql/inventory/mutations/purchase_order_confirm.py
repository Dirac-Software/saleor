import graphene
from django.core.exceptions import ValidationError
from django.db import transaction

from ....inventory import PurchaseOrderItemStatus, events, models
from ....inventory.error_codes import PurchaseOrderErrorCode
from ....inventory.stock_management import confirm_purchase_order_item
from ....permission.enums import ProductPermissions
from ....webhook.event_types import WebhookEventAsyncType
from ...app.dataloaders import get_app_promise
from ...core import ResolveInfo
from ...core.doc_category import DOC_CATEGORY_PRODUCTS
from ...core.mutations import BaseMutation
from ...core.utils import from_global_id_or_error
from ...plugins.dataloaders import get_plugin_manager_promise
from ..types import PurchaseOrder, PurchaseOrderError


class PurchaseOrderConfirm(BaseMutation):
    """Confirms a purchase order with the supplier.

    This mutation:
    1. Moves stock from supplier (non-owned) warehouse to owned warehouse
    2. Changes all purchase order items from DRAFT to CONFIRMED status
    3. Makes the stock available for allocation and fulfillment
    4. Sets confirmed_at timestamp on all items
    """

    purchase_order = graphene.Field(
        PurchaseOrder, description="The confirmed purchase order."
    )

    class Arguments:
        id = graphene.ID(
            required=True,
            description="ID of the purchase order to confirm.",
        )

    class Meta:
        description = "Confirms a purchase order with the supplier."
        permissions = (ProductPermissions.MANAGE_PRODUCTS,)
        error_type_class = PurchaseOrderError
        error_type_field = "purchase_order_errors"
        doc_category = DOC_CATEGORY_PRODUCTS

    @classmethod
    def perform_mutation(cls, root, info: ResolveInfo, /, **data):
        """Confirm the purchase order and all its items."""
        purchase_order_id = data["id"]
        manager = get_plugin_manager_promise(info.context).get()
        app = get_app_promise(info.context).get()

        # Get purchase order
        try:
            _, pk = from_global_id_or_error(purchase_order_id, "PurchaseOrder")
            purchase_order = models.PurchaseOrder.objects.prefetch_related(
                "items__product_variant"
            ).get(pk=pk)
        except models.PurchaseOrder.DoesNotExist:
            raise ValidationError(
                {
                    "id": ValidationError(
                        "Purchase order not found.",
                        code=PurchaseOrderErrorCode.GRAPHQL_ERROR.value,
                    )
                }
            ) from None

        # Validate all items are in DRAFT status
        non_draft_items = [
            item
            for item in purchase_order.items.all()
            if item.status != PurchaseOrderItemStatus.DRAFT
        ]
        if non_draft_items:
            statuses = ", ".join({item.status for item in non_draft_items})
            raise ValidationError(
                {
                    "id": ValidationError(
                        f"All items must be in DRAFT status. Found items with status: {statuses}",
                        code=PurchaseOrderErrorCode.GRAPHQL_ERROR.value,
                    )
                }
            )

        # Confirm all items in a transaction
        try:
            with transaction.atomic():
                for item in purchase_order.items.all():
                    confirm_purchase_order_item(item)

                # Log the event for audit trail
                events.purchase_order_confirmed_event(
                    purchase_order=purchase_order,
                    user=info.context.user,
                    app=app,
                )

                # Trigger webhook
                cls.call_event(manager.purchase_order_confirmed, purchase_order)

        except ValueError as e:
            # Handle errors from confirm_purchase_order_item (insufficient stock, etc.)
            raise ValidationError(
                {
                    "id": ValidationError(
                        str(e),
                        code=PurchaseOrderErrorCode.GRAPHQL_ERROR.value,
                    )
                }
            ) from e

        # Trigger async webhook
        cls.call_event(
            manager.event_delivery_retry,
            WebhookEventAsyncType.PURCHASE_ORDER_CONFIRMED,
            purchase_order,
        )

        # Refresh to get updated items
        purchase_order.refresh_from_db()

        return PurchaseOrderConfirm(purchase_order=purchase_order)
