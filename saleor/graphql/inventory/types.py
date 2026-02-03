import graphene
from ...inventory import models
from ..core import ResolveInfo
from ..core.connection import CountableConnection
from ..core.doc_category import DOC_CATEGORY_PRODUCTS
from ..core.types import ModelObjectType, Money
from ..product.dataloaders import ProductVariantByIdLoader
from ..warehouse.dataloaders import WarehouseByIdLoader
from .enums import PurchaseOrderStateEnum


class PurchaseOrder(ModelObjectType[models.PurchaseOrder]):
    id = graphene.GlobalID(required=True, description="The ID of the purchase order.")
    supplier_warehouse = graphene.Field(
        "saleor.graphql.warehouse.types.Warehouse",
        required=True,
        description="Supplier warehouse (non-owned)."
    )
    state = PurchaseOrderStateEnum(
        required=True,
        description="Current state of the purchase order."
    )
    reference = graphene.String(description="Purchase order reference number.")
    order_date = graphene.Date(description="Date the order was placed.")
    expected_delivery_date = graphene.Date(description="Expected delivery date.")

    items = graphene.List(
        lambda: PurchaseOrderItem,
        required=True,
        description="Items in this purchase order."
    )

    pickings = graphene.List(
        "saleor.graphql.warehouse.types_picking.Picking",
        description="Receipt pickings for this purchase order."
    )

    invoice = graphene.Field(
        "saleor.graphql.invoice.types.Invoice",
        description="Supplier invoice."
    )

    created_at = graphene.DateTime(required=True)
    updated_at = graphene.DateTime(required=True)

    class Meta:
        description = "Represents a purchase order from a supplier."
        model = models.PurchaseOrder
        interfaces = [graphene.relay.Node]

    @staticmethod
    def resolve_supplier_warehouse(root, info: ResolveInfo):
        return WarehouseByIdLoader(info.context).load(root.supplier_warehouse_id)

    @staticmethod
    def resolve_items(root, info: ResolveInfo):
        return root.items.all()

    @staticmethod
    def resolve_pickings(root, info: ResolveInfo):
        return root.pickings.all()


class PurchaseOrderItem(ModelObjectType[models.PurchaseOrderItem]):
    id = graphene.GlobalID(required=True, description="The ID of the purchase order item.")
    purchase_order = graphene.Field(
        PurchaseOrder,
        required=True,
        description="Parent purchase order."
    )
    product_variant = graphene.Field(
        "saleor.graphql.product.types.ProductVariant",
        required=True,
        description="Product variant ordered."
    )
    quantity_ordered = graphene.Int(
        required=True,
        description="Quantity ordered."
    )

    expected_unit_cost = graphene.Field(
        Money,
        required=True,
        description="Expected unit cost from proforma."
    )
    expected_unit_cost_vat = graphene.Field(
        Money,
        required=True,
        description="Expected unit cost VAT."
    )
    expected_country_of_origin = graphene.String(
        description="Expected country of origin (ISO 2-letter code)."
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
