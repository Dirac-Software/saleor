import graphene
from django.core.exceptions import ValidationError

from ....permission.enums import ShippingPermissions
from ....shipping.receiving import create_shipment
from ...account.dataloaders import AddressByIdLoader
from ...core import ResolveInfo
from ...core.doc_category import DOC_CATEGORY_SHIPPING
from ...core.mutations import BaseMutation
from ...core.scalars import PositiveDecimal
from ...core.types import BaseInputObjectType, NonNullList, ShippingError
from ...core.utils import from_global_id_or_error
from ..types import Shipment


class ShipmentCreateInput(BaseInputObjectType):
    source_address_id = graphene.ID(
        required=True,
        description="Source address ID (where shipment originates).",
    )
    destination_address_id = graphene.ID(
        required=True,
        description="Destination address ID (where shipment is going).",
    )
    purchase_order_item_ids = NonNullList(
        graphene.ID,
        required=True,
        description="Purchase order items to include in shipment.",
    )
    carrier = graphene.String(description="Carrier name (e.g., DHL, FedEx).")
    tracking_number = graphene.String(description="Tracking number from carrier.")
    shipping_cost = PositiveDecimal(description="Estimated shipping cost.")
    shipping_cost_vat = PositiveDecimal(description="Estimated VAT on shipping.")
    currency = graphene.String(
        description="Currency code (default: GBP).",
        default_value="GBP",
    )

    class Meta:
        doc_category = DOC_CATEGORY_SHIPPING


class ShipmentCreate(BaseMutation):
    shipment = graphene.Field(Shipment, description="Created shipment.")

    class Arguments:
        input = ShipmentCreateInput(
            required=True,
            description="Fields required to create a shipment.",
        )

    class Meta:
        description = "Creates a new shipment for inbound goods from supplier."
        permissions = (ShippingPermissions.MANAGE_SHIPPING,)
        error_type_class = ShippingError
        error_type_field = "shipping_errors"
        doc_category = DOC_CATEGORY_SHIPPING

    @classmethod
    def perform_mutation(cls, _root, info: ResolveInfo, /, **data):
        from ....inventory.models import PurchaseOrderItem

        input_data = data["input"]

        # Load addresses
        _, source_address_pk = from_global_id_or_error(
            input_data["source_address_id"], "Address"
        )
        _, destination_address_pk = from_global_id_or_error(
            input_data["destination_address_id"], "Address"
        )

        source_address = AddressByIdLoader(info.context).load(source_address_pk)
        destination_address = AddressByIdLoader(info.context).load(
            destination_address_pk
        )

        # Load purchase order items
        poi_pks = []
        for poi_id in input_data["purchase_order_item_ids"]:
            _, poi_pk = from_global_id_or_error(poi_id, "PurchaseOrderItem")
            poi_pks.append(poi_pk)

        purchase_order_items = list(
            PurchaseOrderItem.objects.filter(pk__in=poi_pks).select_for_update()
        )

        if len(purchase_order_items) != len(poi_pks):
            raise ValidationError("Some purchase order items not found.")

        # Create shipment
        try:
            shipment = create_shipment(
                source_address=source_address,
                destination_address=destination_address,
                purchase_order_items=purchase_order_items,
                carrier=input_data.get("carrier"),
                tracking_number=input_data.get("tracking_number"),
                shipping_cost=input_data.get("shipping_cost"),
                shipping_cost_vat=input_data.get("shipping_cost_vat"),
                currency=input_data.get("currency", "GBP"),
            )
        except ValueError as e:
            raise ValidationError(str(e))

        return ShipmentCreate(shipment=shipment)
