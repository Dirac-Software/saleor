import graphene
from django.utils import timezone

from ....inventory import ReceiptStatus
from ....inventory.models import Receipt, ReceiptLine
from ...core import ResolveInfo
from ...core.connection import CountableConnection
from ...core.doc_category import DOC_CATEGORY_PRODUCTS
from ...core.types import ModelObjectType
from ...product.types import ProductVariant
from ...user.types import User
from .purchase_order import PurchaseOrderItem


class ReceiptStatusEnum(graphene.Enum):
    IN_PROGRESS = ReceiptStatus.IN_PROGRESS
    COMPLETED = ReceiptStatus.COMPLETED
    CANCELLED = ReceiptStatus.CANCELLED


class ReceiptLine(ModelObjectType[ReceiptLine]):
    purchase_order_item = graphene.Field(
        PurchaseOrderItem,
        description="The purchase order item being received.",
        required=True,
    )
    quantity_received = graphene.Int(
        description="Quantity received in this line.",
        required=True,
    )
    received_at = graphene.DateTime(
        description="When this item was scanned/received.",
        required=True,
    )
    received_by = graphene.Field(
        User,
        description="Warehouse staff who scanned this item.",
    )
    notes = graphene.String(
        description="Notes about this specific receipt line.",
    )

    class Meta:
        description = "Represents a line item in a goods receipt."
        interfaces = [graphene.relay.Node]
        model = ReceiptLine
        doc_category = DOC_CATEGORY_PRODUCTS


class ReceiptLineCountableConnection(CountableConnection):
    class Meta:
        node = ReceiptLine


class Receipt(ModelObjectType[Receipt]):
    status = ReceiptStatusEnum(
        description="Current status of the receipt.",
        required=True,
    )
    lines = graphene.List(
        graphene.NonNull(ReceiptLine),
        description="Items received in this receipt.",
        required=True,
    )
    created_at = graphene.DateTime(
        description="When the receipt was started.",
        required=True,
    )
    completed_at = graphene.DateTime(
        description="When the receipt was completed.",
    )
    created_by = graphene.Field(
        User,
        description="User who started the receipt.",
    )
    completed_by = graphene.Field(
        User,
        description="User who completed the receipt.",
    )
    notes = graphene.String(
        description="Notes about this receipt.",
    )

    class Meta:
        description = "Represents a goods receipt for an inbound shipment."
        interfaces = [graphene.relay.Node]
        model = Receipt
        doc_category = DOC_CATEGORY_PRODUCTS

    @staticmethod
    def resolve_lines(root: Receipt, info: ResolveInfo):
        return root.lines.all()


class ReceiptCountableConnection(CountableConnection):
    class Meta:
        node = Receipt
