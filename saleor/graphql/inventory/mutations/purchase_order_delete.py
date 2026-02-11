import graphene
from django.core.exceptions import ValidationError

from ....inventory import PurchaseOrderItemStatus, models
from ....inventory.error_codes import PurchaseOrderErrorCode
from ....permission.enums import WarehousePermissions
from ...core import ResolveInfo
from ...core.doc_category import DOC_CATEGORY_PRODUCTS
from ...core.mutations import ModelDeleteMutation
from ..types import PurchaseOrder, PurchaseOrderError


class PurchaseOrderDelete(ModelDeleteMutation):
    """Delete a draft purchase order.

    Only purchase orders with all items in DRAFT status can be deleted.
    Confirmed or received purchase orders cannot be deleted as they're part
    of the accounting and inventory records.
    """

    class Arguments:
        id = graphene.ID(
            required=True,
            description="ID of the purchase order to delete.",
        )

    class Meta:
        description = "Deletes a draft purchase order."
        model = models.PurchaseOrder
        object_type = PurchaseOrder
        permissions = (WarehousePermissions.MANAGE_PURCHASE_ORDERS,)
        error_type_class = PurchaseOrderError
        error_type_field = "purchase_order_errors"
        doc_category = DOC_CATEGORY_PRODUCTS

    @classmethod
    def perform_mutation(cls, root, info: ResolveInfo, /, **data):
        # Get the purchase order instance
        purchase_order = cls.get_instance(info, **data)

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
                        f"Cannot delete purchase order with non-draft items. "
                        f"Found items with status: {statuses}. "
                        f"Only purchase orders with all items in DRAFT status can be deleted.",
                        code=PurchaseOrderErrorCode.INVALID.value,
                    )
                }
            )

        # Call parent's perform_mutation to handle deletion
        return super().perform_mutation(root, info, **data)
