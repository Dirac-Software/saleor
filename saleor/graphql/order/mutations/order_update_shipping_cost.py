from typing import cast

import graphene
from django.core.exceptions import ValidationError

from ....order import models
from ....order.actions import call_order_event
from ....order.error_codes import OrderErrorCode
from ....permission.enums import OrderPermissions
from ....webhook.event_types import WebhookEventAsyncType
from ...core import ResolveInfo
from ...core.context import SyncWebhookControlContext
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.mutations import BaseMutation
from ...core.scalars import PositiveDecimal
from ...core.types import BaseInputObjectType, OrderError
from ...plugins.dataloaders import get_plugin_manager_promise
from ..types import Order
from .utils import EditableOrderValidationMixin


class OrderUpdateShippingCostInput(BaseInputObjectType):
    shipping_cost_net = PositiveDecimal(
        description="Net shipping cost amount (excluding VAT).",
        required=True,
    )
    vat_percentage = PositiveDecimal(
        description=(
            "VAT percentage to apply to shipping cost. "
            "Defaults to 20% (UK standard rate). "
            "Common UK rates: 20% (standard), 5% (reduced), 0% (zero-rated)."
        ),
        required=False,
    )
    inco_term = graphene.String(
        description=(
            "Incoterm (International Commercial Terms) defining shipping responsibility. "
            "Default is 'DDP' (Delivered Duty Paid) - seller pays all shipping costs. "
            "Use 'EXW' (Ex Works) to allow $0 shipping cost when buyer pays shipping. "
            "Other options: FOB, DAP, CIF, etc."
        ),
        required=False,
    )

    class Meta:
        doc_category = DOC_CATEGORY_ORDERS


class OrderUpdateShippingCost(EditableOrderValidationMixin, BaseMutation):
    order = graphene.Field(Order, description="Order with updated shipping cost.")

    class Arguments:
        id = graphene.ID(
            required=True,
            description="ID of the order to update shipping cost.",
        )
        input = OrderUpdateShippingCostInput(
            description="Fields required to update shipping cost.",
            required=True,
        )

    class Meta:
        description = (
            "Manually updates the shipping cost of an order. "
            "Provide net cost and VAT percentage (defaults to 20% UK standard rate). "
            "Gross cost is auto-calculated. "
            "Only works for draft and unconfirmed orders."
        )
        doc_category = DOC_CATEGORY_ORDERS
        permissions = (OrderPermissions.MANAGE_ORDERS,)
        error_type_class = OrderError
        error_type_field = "order_errors"

    @classmethod
    def _get_or_create_manual_shipping_method(cls, channel):
        from ....shipping.models import ShippingMethod, ShippingMethodChannelListing, ShippingZone
        from decimal import Decimal

        # Find or create a global "MANUAL" shipping zone
        manual_zone, _ = ShippingZone.objects.get_or_create(
            name="MANUAL",
            defaults={
                "countries": [],
                "description": "Automatic zone for manual shipping costs",
            },
        )

        # Ensure the channel is linked to this zone
        if not manual_zone.channels.filter(id=channel.id).exists():
            manual_zone.channels.add(channel)

        # Find or create the MANUAL shipping method
        manual_method, created = ShippingMethod.objects.get_or_create(
            shipping_zone=manual_zone,
            name="MANUAL",
            defaults={
                "type": "manual",
            },
        )

        # Update existing methods to use manual type
        if not created and manual_method.type != "manual":
            manual_method.type = "manual"
            manual_method.save(update_fields=["type"])

        # Ensure channel listing exists (zero cost - actual price from manual input)
        ShippingMethodChannelListing.objects.get_or_create(
            shipping_method=manual_method,
            channel=channel,
            defaults={
                "minimum_order_price_amount": Decimal(0),
                "price_amount": Decimal(0),
                "currency": channel.currency_code,
            },
        )

        return manual_method

    @classmethod
    def perform_mutation(cls, _root, info: ResolveInfo, /, **data):
        order = cls.get_node_or_error(
            info,
            data["id"],
            only_type=Order,
            qs=models.Order.objects.prefetch_related("lines"),
        )

        order = cast(models.Order, order)
        input = data["input"]

        cls.check_channel_permissions(info, [order.channel_id])
        cls.validate_order(order)

        if not order.is_shipping_required():
            raise ValidationError(
                {
                    "order": ValidationError(
                        "Cannot set shipping cost for order without shippable products.",
                        code=OrderErrorCode.SHIPPING_METHOD_NOT_APPLICABLE.value,
                    )
                }
            )

        from decimal import Decimal

        from ....shipping import IncoTerm

        net_amount = input["shipping_cost_net"]
        vat_percentage = input.get("vat_percentage", Decimal(20))
        inco_term = input.get("inco_term")

        vat_multiplier = Decimal(1) + (vat_percentage / Decimal(100))
        gross_amount = net_amount * vat_multiplier

        if inco_term and inco_term not in [choice[0] for choice in IncoTerm.CHOICES]:
            raise ValidationError(
                {
                    "inco_term": ValidationError(
                        f"Invalid inco_term. Must be one of: {', '.join([c[0] for c in IncoTerm.CHOICES])}",
                        code=OrderErrorCode.INVALID.value,
                    )
                }
            )

        order.shipping_price_net_amount = net_amount
        order.shipping_price_gross_amount = gross_amount
        order.base_shipping_price_amount = net_amount
        order.undiscounted_base_shipping_price_amount = net_amount
        order.shipping_tax_rate = vat_percentage / Decimal(100)

        needs_manual_method = not order.shipping_method
        if needs_manual_method:
            order.shipping_method_name = "Manual Shipping Cost"
            # Auto-assign MANUAL shipping method to satisfy validation
            manual_method = cls._get_or_create_manual_shipping_method(order.channel)
            order.shipping_method = manual_method

        if inco_term:
            order.inco_term = inco_term

        order.should_refresh_prices = False

        update_fields = [
            "shipping_price_net_amount",
            "shipping_price_gross_amount",
            "base_shipping_price_amount",
            "undiscounted_base_shipping_price_amount",
            "shipping_tax_rate",
            "should_refresh_prices",
            "updated_at",
        ]
        if needs_manual_method:
            update_fields.extend(["shipping_method", "shipping_method_name"])
        if inco_term:
            update_fields.append("inco_term")

        order.save(update_fields=update_fields)

        manager = get_plugin_manager_promise(info.context).get()
        is_draft_order = order.is_draft()
        event_to_emit = (
            WebhookEventAsyncType.DRAFT_ORDER_UPDATED
            if is_draft_order
            else WebhookEventAsyncType.ORDER_UPDATED
        )
        call_order_event(manager, event_to_emit, order)

        return OrderUpdateShippingCost(order=SyncWebhookControlContext(order))
