import graphene

from ...inventory import models
from ..core import ResolveInfo
from ..core.connection import CountableConnection
from ..core.doc_category import DOC_CATEGORY_PRODUCTS
from ..core.enums import PurchaseOrderErrorCode, ReceiptErrorCode
from ..core.scalars import PositiveDecimal
from ..core.types import BaseInputObjectType, Error, ModelObjectType, Money, NonNullList
from ..meta.inputs import MetadataInput
from ..product.dataloaders import ProductVariantByIdLoader
from ..warehouse.dataloaders import WarehouseByIdLoader
from .enums import PurchaseOrderItemAdjustmentReasonEnum, PurchaseOrderItemStatusEnum


class PurchaseOrder(ModelObjectType[models.PurchaseOrder]):
    id = graphene.GlobalID(required=True, description="The ID of the purchase order.")
    supplier_warehouse = graphene.Field(
        "saleor.graphql.warehouse.types.Warehouse",
        required=True,
        description="Supplier warehouse (non-owned).",
    )
    destination_warehouse = graphene.Field(
        "saleor.graphql.warehouse.types.Warehouse",
        required=True,
        description="Destination warehouse (owned).",
    )

    items = graphene.List(
        lambda: PurchaseOrderItem,
        required=True,
        description="Items in this purchase order.",
    )

    class Meta:
        description = "Represents a purchase order from a supplier."
        model = models.PurchaseOrder
        interfaces = [graphene.relay.Node]

    @staticmethod
    def resolve_supplier_warehouse(root, info: ResolveInfo):
        return WarehouseByIdLoader(info.context).load(root.source_warehouse_id)

    @staticmethod
    def resolve_destination_warehouse(root, info: ResolveInfo):
        return WarehouseByIdLoader(info.context).load(root.destination_warehouse_id)

    @staticmethod
    def resolve_items(root, info: ResolveInfo):
        return root.items.all()


class PurchaseOrderItem(ModelObjectType[models.PurchaseOrderItem]):
    id = graphene.GlobalID(
        required=True, description="The ID of the purchase order item."
    )
    purchase_order = graphene.Field(
        PurchaseOrder, required=True, description="Parent purchase order."
    )
    product_variant = graphene.Field(
        "saleor.graphql.product.types.ProductVariant",
        required=True,
        description="Product variant ordered.",
    )
    quantity_ordered = graphene.Int(required=True, description="Quantity ordered.")
    quantity_received = graphene.Int(required=True, description="Quantity received.")

    unit_price = graphene.Field(
        Money, required=True, description="Unit cost (buy price)."
    )
    country_of_origin = graphene.String(
        required=True,
        description="Country of origin (ISO 2-letter code)."
    )
    status = PurchaseOrderItemStatusEnum(
        required=True,
        description="Status of this purchase order item."
    )

    class Meta:
        description = "Represents a line item in a purchase order."
        model = models.PurchaseOrderItem
        interfaces = [graphene.relay.Node]

    @staticmethod
    def resolve_product_variant(root, info: ResolveInfo):
        return ProductVariantByIdLoader(info.context).load(root.product_variant_id)


class PurchaseOrderCountableConnection(CountableConnection):
    class Meta:
        doc_category = DOC_CATEGORY_PRODUCTS
        node = PurchaseOrder


# Error types
class PurchaseOrderError(Error):
    code = PurchaseOrderErrorCode(description="The error code.", required=True)


class ReceiptError(Error):
    code = ReceiptErrorCode(description="The error code.", required=True)
    warehouses = NonNullList(
        graphene.ID,
        description="List of warehouse IDs which cause the error.",
        required=False,
    )
    variants = NonNullList(
        graphene.ID,
        description="List of variant IDs which cause the error.",
        required=False,
    )

    class Meta:
        doc_category = DOC_CATEGORY_PRODUCTS


# Input types
class PurchaseOrderItemInput(BaseInputObjectType):
    variant_id = graphene.ID(
        required=True, description="Product variant to order."
    )
    quantity_ordered = graphene.Int(
        required=True, description="Quantity to order from supplier."
    )
    unit_price_amount = PositiveDecimal(
        required=True, description="Unit cost (buy price)."
    )
    currency = graphene.String(
        required=True, description="Currency code (e.g., GBP, USD)."
    )
    country_of_origin = graphene.String(
        required=True,
        description="ISO 2-letter country code for customs/duties.",
    )

    class Meta:
        doc_category = DOC_CATEGORY_PRODUCTS


class PurchaseOrderCreateInput(BaseInputObjectType):
    source_warehouse_id = graphene.ID(
        required=True,
        description="Supplier warehouse (must be non-owned warehouse).",
    )
    destination_warehouse_id = graphene.ID(
        required=True,
        description="Destination warehouse (must be owned warehouse).",
    )
    items = NonNullList(
        PurchaseOrderItemInput,
        required=True,
        description="Line items to order.",
    )
    metadata = NonNullList(
        MetadataInput,
        description="Public metadata (e.g., supplier PO number).",
    )
    private_metadata = NonNullList(
        MetadataInput,
        description="Private metadata (e.g., invoice parsing data for future automation).",
    )

    class Meta:
        doc_category = DOC_CATEGORY_PRODUCTS


class PurchaseOrderItemAdjustment(ModelObjectType[models.PurchaseOrderItemAdjustment]):
    id = graphene.GlobalID(
        required=True, description="The ID of the adjustment."
    )
    purchase_order_item = graphene.Field(
        PurchaseOrderItem,
        required=True,
        description="Purchase order item being adjusted.",
    )
    quantity_change = graphene.Int(
        required=True,
        description="Change in quantity (negative for losses, positive for gains).",
    )
    reason = PurchaseOrderItemAdjustmentReasonEnum(
        required=True,
        description="Reason for the adjustment.",
    )
    notes = graphene.String(
        description="Additional notes about the adjustment."
    )
    processed_at = graphene.DateTime(
        description="When the adjustment was processed (null if pending)."
    )
    created_at = graphene.DateTime(
        required=True,
        description="When the adjustment was created.",
    )
    created_by = graphene.Field(
        "saleor.graphql.account.types.User",
        description="User who created the adjustment.",
    )

    class Meta:
        description = "Represents an inventory adjustment to a purchase order item."
        model = models.PurchaseOrderItemAdjustment
        interfaces = [graphene.relay.Node]

    @staticmethod
    def resolve_purchase_order_item(root, info: ResolveInfo):
        return root.purchase_order_item


class PurchaseOrderItemAdjustmentCountableConnection(CountableConnection):
    class Meta:
        doc_category = DOC_CATEGORY_PRODUCTS
        node = PurchaseOrderItemAdjustment
