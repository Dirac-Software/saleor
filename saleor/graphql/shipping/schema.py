import graphene

from ...permission.enums import ShippingPermissions
from ...shipping import models
from ..core import ResolveInfo
from ..core.connection import create_connection_slice, filter_connection_queryset
from ..core.context import ChannelContext, get_database_connection_name
from ..core.doc_category import DOC_CATEGORY_SHIPPING
from ..core.fields import FilterConnectionField, PermissionsField
from ..core.utils import from_global_id_or_error
from ..translations.mutations import ShippingPriceTranslate
from .bulk_mutations import ShippingPriceBulkDelete, ShippingZoneBulkDelete
from .filters import ShippingZoneFilterInput
from .mutations import (
    FulfillmentLinkToShipment,
    OutboundShipmentCreate,
    ShipmentCreate,
    ShipmentMarkDeparted,
    ShippingPriceCreate,
    ShippingPriceDelete,
    ShippingPriceExcludeProducts,
    ShippingPriceRemoveProductFromExclude,
    ShippingPriceUpdate,
    ShippingZoneCreate,
    ShippingZoneDelete,
    ShippingZoneUpdate,
)
from .mutations.shipping_method_channel_listing_update import (
    ShippingMethodChannelListingUpdate,
)
from .resolvers import resolve_shipment, resolve_shipments, resolve_shipping_zones
from .types import (
    Shipment,
    ShipmentCountableConnection,
    ShippingZone,
    ShippingZoneCountableConnection,
)


class ShippingQueries(graphene.ObjectType):
    shipment = PermissionsField(
        Shipment,
        id=graphene.Argument(
            graphene.ID,
            description="ID of the shipment.",
            required=True,
        ),
        description="Look up a shipment by ID.",
        permissions=[ShippingPermissions.MANAGE_SHIPPING],
        doc_category=DOC_CATEGORY_SHIPPING,
    )
    shipments = FilterConnectionField(
        ShipmentCountableConnection,
        description="List of inbound/outbound shipments.",
        permissions=[ShippingPermissions.MANAGE_SHIPPING],
        doc_category=DOC_CATEGORY_SHIPPING,
    )
    available_shipments_for_fulfillment = PermissionsField(
        graphene.List(graphene.NonNull(Shipment)),
        fulfillment_id=graphene.Argument(
            graphene.ID,
            description="Filter shipments compatible with this fulfillment.",
        ),
        warehouse_id=graphene.Argument(
            graphene.ID,
            description="Filter shipments from this warehouse.",
        ),
        description=(
            "List of outbound shipments that haven't departed yet and can accept "
            "more fulfillments. Useful for linking fulfillments to existing shipments."
        ),
        permissions=[ShippingPermissions.MANAGE_SHIPPING],
        doc_category=DOC_CATEGORY_SHIPPING,
    )
    shipping_zone = PermissionsField(
        ShippingZone,
        id=graphene.Argument(
            graphene.ID, description="ID of the shipping zone.", required=True
        ),
        channel=graphene.String(
            description="Slug of a channel for which the data should be returned."
        ),
        description="Look up a shipping zone by ID.",
        permissions=[ShippingPermissions.MANAGE_SHIPPING],
        doc_category=DOC_CATEGORY_SHIPPING,
    )
    shipping_zones = FilterConnectionField(
        ShippingZoneCountableConnection,
        filter=ShippingZoneFilterInput(
            description="Filtering options for shipping zones."
        ),
        channel=graphene.String(
            description="Slug of a channel for which the data should be returned."
        ),
        description="List of the shop's shipping zones.",
        permissions=[ShippingPermissions.MANAGE_SHIPPING],
        doc_category=DOC_CATEGORY_SHIPPING,
    )

    @staticmethod
    def resolve_shipment(_root, info: ResolveInfo, *, id):
        _, pk = from_global_id_or_error(id, "Shipment")
        return resolve_shipment(info, pk)

    @staticmethod
    def resolve_shipments(_root, info: ResolveInfo, **kwargs):
        qs = resolve_shipments(info)
        qs = filter_connection_queryset(
            qs, kwargs, allow_replica=info.context.allow_replica
        )
        return create_connection_slice(qs, info, kwargs, ShipmentCountableConnection)

    @staticmethod
    def resolve_shipping_zone(_root, info: ResolveInfo, *, id, channel=None):
        _, id = from_global_id_or_error(id, ShippingZone)
        instance = (
            models.ShippingZone.objects.using(
                get_database_connection_name(info.context)
            )
            .filter(id=id)
            .first()
        )
        return ChannelContext(node=instance, channel_slug=channel) if instance else None

    @staticmethod
    def resolve_shipping_zones(_root, info: ResolveInfo, *, channel=None, **kwargs):
        qs = resolve_shipping_zones(info, channel)
        qs = filter_connection_queryset(
            qs, kwargs, allow_replica=info.context.allow_replica
        )
        return create_connection_slice(
            qs, info, kwargs, ShippingZoneCountableConnection
        )

    @staticmethod
    def resolve_available_shipments_for_fulfillment(
        _root, info: ResolveInfo, *, fulfillment_id=None, warehouse_id=None
    ):
        """Return shipments that can accept fulfillments (haven't departed yet)."""
        from ...order.models import Fulfillment
        from ...shipping import ShipmentType

        qs = (
            models.Shipment.objects.using(get_database_connection_name(info.context))
            .filter(
                shipment_type=ShipmentType.OUTBOUND,
                departed_at__isnull=True,
            )
            .select_related("source", "destination")
        )

        # Filter by fulfillment compatibility
        if fulfillment_id:
            _, fulfillment_pk = from_global_id_or_error(fulfillment_id, "Fulfillment")
            try:
                fulfillment = Fulfillment.objects.select_related(
                    "order__shipping_address"
                ).get(pk=fulfillment_pk)

                # Filter to shipments with matching destination
                if fulfillment.order.shipping_address:
                    dest = fulfillment.order.shipping_address
                    qs = qs.filter(
                        destination__street_address_1=dest.street_address_1,
                        destination__city=dest.city,
                        destination__postal_code=dest.postal_code,
                        destination__country=dest.country,
                    )
            except Fulfillment.DoesNotExist:
                return []

        # Filter by warehouse
        if warehouse_id:
            from ...warehouse.models import Warehouse

            _, warehouse_pk = from_global_id_or_error(warehouse_id, "Warehouse")
            try:
                warehouse = Warehouse.objects.select_related("address").get(
                    pk=warehouse_pk
                )
                if warehouse.address:
                    addr = warehouse.address
                    qs = qs.filter(
                        source__street_address_1=addr.street_address_1,
                        source__city=addr.city,
                        source__postal_code=addr.postal_code,
                    )
            except Warehouse.DoesNotExist:
                return []

        return list(qs[:50])  # Limit to 50 results


class ShippingMutations(graphene.ObjectType):
    shipment_create = ShipmentCreate.Field()
    outbound_shipment_create = OutboundShipmentCreate.Field()
    shipment_mark_departed = ShipmentMarkDeparted.Field()
    fulfillment_link_to_shipment = FulfillmentLinkToShipment.Field()

    shipping_method_channel_listing_update = ShippingMethodChannelListingUpdate.Field()
    shipping_price_create = ShippingPriceCreate.Field()
    shipping_price_delete = ShippingPriceDelete.Field()
    shipping_price_bulk_delete = ShippingPriceBulkDelete.Field()
    shipping_price_update = ShippingPriceUpdate.Field()
    shipping_price_translate = ShippingPriceTranslate.Field()
    shipping_price_exclude_products = ShippingPriceExcludeProducts.Field()
    shipping_price_remove_product_from_exclude = (
        ShippingPriceRemoveProductFromExclude.Field()
    )

    shipping_zone_create = ShippingZoneCreate.Field()
    shipping_zone_delete = ShippingZoneDelete.Field()
    shipping_zone_bulk_delete = ShippingZoneBulkDelete.Field()
    shipping_zone_update = ShippingZoneUpdate.Field()
