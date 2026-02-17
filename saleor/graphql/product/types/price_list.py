import graphene
from django.core.signing import TimestampSigner
from django.urls import reverse
from graphene import relay
from graphql import ResolveInfo

from ....product import models
from ...core.connection import CountableConnection, create_connection_slice
from ...core.context import ChannelContext, get_database_connection_name
from ...core.doc_category import DOC_CATEGORY_PRODUCTS
from ...core.fields import ConnectionField, JSONString
from ...core.scalars import DateTime
from ...core.types import ModelObjectType
from saleor.core.utils import build_absolute_uri


class PriceListItemFilterInput(graphene.InputObjectType):
    is_valid = graphene.Boolean(description="Filter items by validation status.")


class PriceListItem(ModelObjectType[models.PriceListItem]):
    id = graphene.GlobalID(required=True, description="The ID of the price list item.")
    row_index = graphene.Int(
        required=True, description="Row index in the source Excel file."
    )
    product_code = graphene.String(required=True, description="Product code.")
    brand = graphene.String(required=True, description="Brand.")
    description = graphene.String(required=True, description="Product description.")
    category = graphene.String(required=True, description="Category name.")
    sizes_and_qty = JSONString(description="Map of size to quantity.")
    rrp = graphene.Float(description="Recommended retail price.")
    sell_price = graphene.Float(description="Sell price shown on the web.")
    buy_price = graphene.Float(description="Buy / cost price.")
    weight_kg = graphene.Float(description="Weight in kg.")
    image_url = graphene.String(description="Image URL.")
    hs_code = graphene.String(description="HS code (UK).")
    currency = graphene.String(description="Currency code for prices.")
    is_valid = graphene.Boolean(
        required=True, description="Whether the row parsed without errors."
    )
    validation_errors = graphene.List(
        graphene.NonNull(graphene.String),
        description="List of validation error messages.",
    )
    product = graphene.Field(
        "saleor.graphql.product.types.products.Product",
        description="Matched Saleor product, if resolved.",
    )

    @staticmethod
    def resolve_product(root: models.PriceListItem, info):
        if root.product_id is None:
            return None
        product = root.product
        return ChannelContext(node=product, channel_slug=None)

    class Meta:
        description = "A single row parsed from a price list Excel file."
        interfaces = [relay.Node]
        model = models.PriceListItem


class PriceListItemCountableConnection(CountableConnection):
    class Meta:
        doc_category = DOC_CATEGORY_PRODUCTS
        node = PriceListItem


class PriceList(ModelObjectType[models.PriceList]):
    id = graphene.GlobalID(required=True, description="The ID of the price list.")
    name = graphene.String(required=True, description="Human-readable name of the price list.")
    status = graphene.String(
        required=True, description="Current status: ACTIVE or INACTIVE."
    )
    google_drive_url = graphene.String(description="Source Google Drive URL.")
    created_at = DateTime(required=True, description="When the price list was created.")
    activated_at = DateTime(description="When the price list was last activated.")
    deactivated_at = DateTime(description="When the price list was last deactivated.")
    attempted_processing_at = DateTime(
        description="When processing was last triggered."
    )
    processing_completed_at = DateTime(
        description="When processing completed successfully."
    )
    processing_failed_at = DateTime(description="When processing last failed.")
    warehouse = graphene.Field(
        "saleor.graphql.warehouse.types.Warehouse",
        required=True,
        description="The warehouse this price list belongs to.",
    )
    replaced_by = graphene.Field(
        lambda: PriceList,
        description="The price list that replaced this one, if any.",
    )
    channels = graphene.List(
        graphene.NonNull("saleor.graphql.channel.types.Channel"),
        required=True,
        description="Channels this price list is active in.",
    )
    excel_file_url = graphene.String(
        description="URL to download the original Excel file."
    )
    items = ConnectionField(
        PriceListItemCountableConnection,
        filter=graphene.Argument(
            PriceListItemFilterInput,
            description="Filter items by validation status.",
        ),
        description="Items in this price list.",
    )
    item_count = graphene.Int(
        required=True, description="Total number of items in this price list."
    )
    valid_item_count = graphene.Int(
        required=True, description="Number of items that parsed without errors."
    )

    class Meta:
        description = (
            "A price list sourced from an Excel file, belonging to a warehouse."
        )
        interfaces = [relay.Node]
        model = models.PriceList

    @staticmethod
    def resolve_status(root: models.PriceList, info):
        return root.status.upper()

    @staticmethod
    def resolve_excel_file_url(root: models.PriceList, _info: ResolveInfo):
        if not root.excel_file:
            return None
        signed_id = TimestampSigner().sign(str(root.pk))
        path = reverse("serve-price-list", kwargs={"pk": root.pk, "signed_id": signed_id})
        return build_absolute_uri(path)

    @staticmethod
    def resolve_channels(root: models.PriceList, info):
        db = get_database_connection_name(info.context)
        return root.channels.using(db).all()

    @staticmethod
    def resolve_items(root: models.PriceList, info, **kwargs):
        db = get_database_connection_name(info.context)
        qs = root.items.using(db).select_related("product").all()
        filter_input = kwargs.pop("filter", None)
        if filter_input and filter_input.get("is_valid") is not None:
            qs = qs.filter(is_valid=filter_input["is_valid"])
        return create_connection_slice(
            qs, info, kwargs, PriceListItemCountableConnection
        )

    @staticmethod
    def resolve_item_count(root: models.PriceList, info):
        # Use pre-computed annotation when available (avoids N+1 in list queries)
        if hasattr(root, "_item_count"):
            return root._item_count
        db = get_database_connection_name(info.context)
        return root.items.using(db).count()

    @staticmethod
    def resolve_valid_item_count(root: models.PriceList, info):
        if hasattr(root, "_valid_item_count"):
            return root._valid_item_count
        db = get_database_connection_name(info.context)
        return root.items.using(db).filter(is_valid=True).count()

    @staticmethod
    def resolve_warehouse(root: models.PriceList, info):
        from ...warehouse.dataloaders import WarehouseByIdLoader

        return WarehouseByIdLoader(info.context).load(root.warehouse_id)

    @staticmethod
    def resolve_replaced_by(root: models.PriceList, info):
        # root.replaced_by is prefetched via select_related in the schema resolvers
        return root.replaced_by if root.replaced_by_id else None


class PriceListCountableConnection(CountableConnection):
    class Meta:
        doc_category = DOC_CATEGORY_PRODUCTS
        node = PriceList
