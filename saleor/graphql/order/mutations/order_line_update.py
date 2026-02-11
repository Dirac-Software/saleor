import graphene
from django.core.exceptions import ValidationError

from ....core.exceptions import InsufficientStock
from ....core.tracing import traced_atomic_transaction
from ....order import models
from ....order.calculations import fetch_order_prices_if_expired
from ....order.error_codes import OrderErrorCode
from ....order.fetch import OrderLineInfo
from ....order.utils import (
    change_order_line_quantity,
    invalidate_order_prices,
    recalculate_order_weight,
)
from ....permission.enums import OrderPermissions
from ...app.dataloaders import get_app_promise
from ...core import ResolveInfo
from ...core.context import SyncWebhookControlContext
from ...core.mutations import ModelWithRestrictedChannelAccessMutation
from ...core.types import OrderError
from ...plugins.dataloaders import get_plugin_manager_promise
from ..types import Order, OrderLine
from .draft_order_create import OrderLineInput
from .utils import EditableOrderValidationMixin, call_event_by_order_status


class OrderLineUpdate(
    EditableOrderValidationMixin, ModelWithRestrictedChannelAccessMutation
):
    order = graphene.Field(Order, description="Related order.")

    class Arguments:
        id = graphene.ID(description="ID of the order line to update.", required=True)
        input = OrderLineInput(
            required=True, description="Fields required to update an order line."
        )

    class Meta:
        description = "Updates an order line of an order."
        model = models.OrderLine
        object_type = OrderLine
        permissions = (OrderPermissions.MANAGE_ORDERS,)
        error_type_class = OrderError
        error_type_field = "order_errors"

    @classmethod
    def clean_input(cls, info: ResolveInfo, instance, data, **kwargs):
        instance.old_quantity = instance.quantity
        cleaned_input = super().clean_input(info, instance, data, **kwargs)

        # Check price validation before general order validation to give more specific error
        if "price" in data and not instance.order.is_draft():
            raise ValidationError(
                {
                    "price": ValidationError(
                        "Price can only be changed on draft orders.",
                        code=OrderErrorCode.CANNOT_DISCOUNT.value,
                    )
                }
            )

        cls.validate_order(instance.order)

        quantity = data["quantity"]
        if quantity <= 0:
            raise ValidationError(
                {
                    "quantity": ValidationError(
                        "Ensure this value is greater than 0.",
                        code=OrderErrorCode.ZERO_QUANTITY.value,
                    )
                }
            )
        if instance.is_gift:
            raise ValidationError(
                {
                    "id": ValidationError(
                        "Order line marked as gift can't be edited.",
                        code=OrderErrorCode.NON_EDITABLE_GIFT_LINE.value,
                    )
                }
            )

        return cleaned_input

    @classmethod
    def save(cls, info: ResolveInfo, instance, cleaned_input, instance_tracker=None):
        from prices import Money

        manager = get_plugin_manager_promise(info.context).get()

        order_is_unconfirmed = instance.order.is_unconfirmed()
        line_allocation = instance.allocations.first()
        warehouse_pk = (
            line_allocation.stock.warehouse.pk
            if line_allocation and order_is_unconfirmed
            else None
        )
        app = get_app_promise(info.context).get()
        with traced_atomic_transaction():
            line_info = OrderLineInfo(
                line=instance,
                quantity=instance.quantity,
                variant=instance.variant,
                warehouse_pk=warehouse_pk,
            )
            order = instance.order
            try:
                change_order_line_quantity(
                    info.context.user,
                    app,
                    line_info,
                    instance.old_quantity,
                    instance.quantity,
                    order,
                    manager,
                    allocate_stock=order_is_unconfirmed,
                )
            except InsufficientStock as e:
                raise ValidationError(
                    "Cannot set new quantity because of insufficient stock.",
                    code=OrderErrorCode.INSUFFICIENT_STOCK.value,
                ) from e

            # Handle custom price updates
            price_net = cleaned_input.get("price_net")
            price_gross = cleaned_input.get("price_gross")
            legacy_price = cleaned_input.get("price")
            should_invalidate_prices = False

            # Validate: priceGross cannot be set without priceNet
            if price_gross is not None and price_net is None:
                raise ValidationError(
                    {
                        "price_gross": ValidationError(
                            "Cannot set priceGross without priceNet. "
                            "Provide priceNet or both priceNet and priceGross.",
                            code=OrderErrorCode.REQUIRED.value,
                        )
                    }
                )

            if price_net is not None or price_gross is not None:
                from decimal import Decimal

                from ....core.prices import quantize_price

                currency = instance.currency

                # Convert to Decimal and quantize
                if price_net is not None:
                    price_net = Decimal(str(price_net))
                    price_net = quantize_price(price_net, currency)
                if price_gross is not None:
                    price_gross = Decimal(str(price_gross))
                    price_gross = quantize_price(price_gross, currency)

                # Case 1: Only priceNet provided
                if price_gross is None:
                    instance.unit_price_net_amount = price_net
                    instance.base_unit_price = Money(price_net, currency)
                    instance.undiscounted_base_unit_price = Money(price_net, currency)
                    instance.undiscounted_unit_price_net_amount = price_net
                    # Mark for refresh so tax system calculates gross
                    invalidate_order_prices(order, save=False)
                    should_invalidate_prices = True
                    instance.save(
                        update_fields=[
                            "unit_price_net_amount",
                            "base_unit_price_amount",
                            "undiscounted_base_unit_price_amount",
                            "undiscounted_unit_price_net_amount",
                        ]
                    )

                # Case 2: Both priceNet and priceGross provided
                else:
                    quantity = instance.quantity
                    instance.unit_price_net_amount = price_net
                    instance.unit_price_gross_amount = price_gross
                    instance.total_price_net_amount = price_net * quantity
                    instance.total_price_gross_amount = price_gross * quantity
                    instance.base_unit_price = Money(price_net, currency)
                    instance.undiscounted_base_unit_price = Money(price_net, currency)
                    instance.undiscounted_unit_price_net_amount = price_net
                    instance.undiscounted_unit_price_gross_amount = price_gross
                    instance.undiscounted_total_price_net_amount = price_net * quantity
                    instance.undiscounted_total_price_gross_amount = (
                        price_gross * quantity
                    )
                    # Don't mark for refresh - manual override is final
                    instance.save(
                        update_fields=[
                            "unit_price_net_amount",
                            "unit_price_gross_amount",
                            "total_price_net_amount",
                            "total_price_gross_amount",
                            "base_unit_price_amount",
                            "undiscounted_base_unit_price_amount",
                            "undiscounted_unit_price_net_amount",
                            "undiscounted_unit_price_gross_amount",
                            "undiscounted_total_price_net_amount",
                            "undiscounted_total_price_gross_amount",
                        ]
                    )

            elif legacy_price is not None:
                # Legacy price field behavior (for backward compatibility)
                from decimal import Decimal

                from ....core.prices import quantize_price

                custom_price = Decimal(str(legacy_price))
                currency = instance.currency
                custom_price = quantize_price(custom_price, currency)
                instance.base_unit_price = Money(custom_price, currency)
                instance.undiscounted_base_unit_price = Money(custom_price, currency)
                instance.undiscounted_unit_price_net_amount = custom_price
                instance.undiscounted_unit_price_gross_amount = custom_price
                instance.unit_price_net_amount = custom_price
                instance.unit_price_gross_amount = custom_price
                instance.save(
                    update_fields=[
                        "base_unit_price_amount",
                        "undiscounted_base_unit_price_amount",
                        "undiscounted_unit_price_net_amount",
                        "undiscounted_unit_price_gross_amount",
                        "unit_price_net_amount",
                        "unit_price_gross_amount",
                    ]
                )
            else:
                # No custom prices provided
                # Only invalidate if the line doesn't have both net and gross already set
                # If both are set, preserve them (custom pricing)
                has_custom_pricing = (
                    instance.unit_price_net_amount is not None
                    and instance.unit_price_gross_amount is not None
                    and instance.unit_price_net_amount
                    != instance.unit_price_gross_amount
                )
                if not has_custom_pricing:
                    # Mark for refresh so discounts and taxes are recalculated
                    invalidate_order_prices(order, save=False)
                    should_invalidate_prices = True

            recalculate_order_weight(order)
            update_fields = ["weight", "updated_at"]
            if should_invalidate_prices:
                update_fields.append("should_refresh_prices")
            order.save(update_fields=update_fields)

            # Refresh prices if needed (but not for manual net+gross override)
            if should_invalidate_prices:
                order, _ = fetch_order_prices_if_expired(
                    order, manager, None, force_update=True
                )

            call_event_by_order_status(order, manager)

    @classmethod
    def success_response(cls, instance):
        return cls(
            orderLine=SyncWebhookControlContext(node=instance),
            order=SyncWebhookControlContext(node=instance.order),
            errors=[],
        )

    @classmethod
    def get_instance_channel_id(cls, instance, **data):
        """Retrieve the instance channel id for channel permission accessible check."""
        return instance.order.channel_id
