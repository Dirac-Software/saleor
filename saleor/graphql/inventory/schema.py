import graphene

from ...permission.enums import ProductPermissions
from ..core import ResolveInfo
from ..core.connection import create_connection_slice, filter_connection_queryset
from ..core.doc_category import DOC_CATEGORY_PRODUCTS
from ..core.fields import FilterConnectionField, PermissionsField
from ..core.utils import from_global_id_or_error
from .mutations import (
    PurchaseOrderConfirm,
    PurchaseOrderCreate,
    ReceiptComplete,
    ReceiptDelete,
    ReceiptLineDelete,
    ReceiptReceiveItem,
    ReceiptStart,
)
from .resolvers import (
    resolve_purchase_order,
    resolve_purchase_orders,
    resolve_receipt,
    resolve_receipts,
)
from .types import (
    PurchaseOrder,
    PurchaseOrderCountableConnection,
    PurchaseOrderItemAdjustment,
    PurchaseOrderItemAdjustmentCountableConnection,
    Receipt,
    ReceiptCountableConnection,
)


class InventoryQueries(graphene.ObjectType):
    purchase_order = PermissionsField(
        PurchaseOrder,
        id=graphene.Argument(
            graphene.ID,
            description="ID of the purchase order.",
            required=True,
        ),
        description="Look up a purchase order by ID.",
        permissions=[
            ProductPermissions.MANAGE_PRODUCTS,
        ],
        doc_category=DOC_CATEGORY_PRODUCTS,
    )
    purchase_orders = FilterConnectionField(
        PurchaseOrderCountableConnection,
        description="List of purchase orders.",
        permissions=[
            ProductPermissions.MANAGE_PRODUCTS,
        ],
        doc_category=DOC_CATEGORY_PRODUCTS,
    )
    receipt = PermissionsField(
        Receipt,
        id=graphene.Argument(
            graphene.ID,
            description="ID of the receipt.",
            required=True,
        ),
        description="Look up a receipt by ID.",
        permissions=[
            ProductPermissions.MANAGE_PRODUCTS,
        ],
        doc_category=DOC_CATEGORY_PRODUCTS,
    )
    receipts = FilterConnectionField(
        ReceiptCountableConnection,
        description="List of goods receipts.",
        permissions=[
            ProductPermissions.MANAGE_PRODUCTS,
        ],
        doc_category=DOC_CATEGORY_PRODUCTS,
    )
    pending_adjustments = FilterConnectionField(
        PurchaseOrderItemAdjustmentCountableConnection,
        description="List all pending purchase order item adjustments (not yet processed).",
        permissions=[
            ProductPermissions.MANAGE_PRODUCTS,
        ],
        doc_category=DOC_CATEGORY_PRODUCTS,
    )
    purchase_order_item_adjustment = PermissionsField(
        PurchaseOrderItemAdjustment,
        id=graphene.Argument(
            graphene.ID,
            description="ID of the adjustment.",
            required=True,
        ),
        description="Look up a purchase order item adjustment by ID.",
        permissions=[
            ProductPermissions.MANAGE_PRODUCTS,
        ],
        doc_category=DOC_CATEGORY_PRODUCTS,
    )

    @staticmethod
    def resolve_purchase_order(root, info: ResolveInfo, *, id):
        _, pk = from_global_id_or_error(id, "PurchaseOrder")
        return resolve_purchase_order(info, pk)

    @staticmethod
    def resolve_purchase_orders(root, info: ResolveInfo, **kwargs):
        qs = resolve_purchase_orders(info)
        qs = filter_connection_queryset(
            qs, kwargs, allow_replica=info.context.allow_replica
        )
        return create_connection_slice(
            qs, info, kwargs, PurchaseOrderCountableConnection
        )

    @staticmethod
    def resolve_receipt(root, info: ResolveInfo, *, id):
        _, pk = from_global_id_or_error(id, "Receipt")
        return resolve_receipt(info, pk)

    @staticmethod
    def resolve_receipts(root, info: ResolveInfo, **kwargs):
        qs = resolve_receipts(info)
        qs = filter_connection_queryset(
            qs, kwargs, allow_replica=info.context.allow_replica
        )
        return create_connection_slice(qs, info, kwargs, ReceiptCountableConnection)

    @staticmethod
    def resolve_pending_adjustments(root, info: ResolveInfo, **kwargs):
        from ...inventory.models import PurchaseOrderItemAdjustment

        return PurchaseOrderItemAdjustment.objects.filter(
            processed_at__isnull=True
        ).order_by("-created_at")

    @staticmethod
    def resolve_purchase_order_item_adjustment(root, info: ResolveInfo, *, id):
        from ...inventory.models import PurchaseOrderItemAdjustment

        _, pk = from_global_id_or_error(id, PurchaseOrderItemAdjustment)
        return PurchaseOrderItemAdjustment.objects.filter(pk=pk).first()


class InventoryMutations(graphene.ObjectType):
    create_purchase_order = PurchaseOrderCreate.Field()
    confirm_purchase_order = PurchaseOrderConfirm.Field()

    # Receipt workflow mutations
    start_receipt = ReceiptStart.Field()
    receive_item = ReceiptReceiveItem.Field()
    complete_receipt = ReceiptComplete.Field()
    delete_receipt = ReceiptDelete.Field()
    delete_receipt_line = ReceiptLineDelete.Field()
