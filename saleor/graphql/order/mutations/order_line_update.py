import graphene
from django.core.exceptions import ValidationError

from ....core.exceptions import InsufficientStock
from ....core.tracing import traced_atomic_transaction
from ....order import models
from ....order.error_codes import OrderErrorCode
from ....order.fetch import OrderLineInfo
from ....order.utils import (
    change_order_line_quantity,
    invalidate_order_prices,
    recalculate_order_weight,
)
from ....permission.enums import OrderPermissions
from ....tax.models import TaxClass
from ...app.dataloaders import get_app_promise
from ...core import ResolveInfo
from ...core.context import SyncWebhookControlContext
from ...core.mutations import ModelWithRestrictedChannelAccessMutation
from ...core.types import OrderError
from ...plugins.dataloaders import get_plugin_manager_promise
from ...tax.types import TaxClass as TaxClassType
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
        tax_class_id = data.pop("tax_class", None)
        price_in_input = "price" in data
        cleaned_input = super().clean_input(info, instance, data, **kwargs)
        cleaned_input["_price_in_input"] = price_in_input
        if tax_class_id:
            cleaned_input["tax_class"] = cls.get_node_or_error(
                info, tax_class_id, only_type=TaxClassType, field="tax_class"
            )

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

            # Handle tax class override
            tax_class: TaxClass | None = cleaned_input.get("tax_class")
            if tax_class is not None:
                instance.tax_class = tax_class
                instance.save(update_fields=["tax_class_id"])

            # Handle custom price update
            if cleaned_input.get("price") is not None:
                from decimal import Decimal

                from ....core.prices import quantize_price

                custom_price = Decimal(str(cleaned_input["price"]))
                currency = instance.currency
                custom_price = quantize_price(custom_price, currency)
                instance.base_unit_price = Money(custom_price, currency)
                instance.undiscounted_base_unit_price = Money(custom_price, currency)
                # Set both net and gross to the entered value temporarily; the tax
                # recalculation triggered below will compute the correct split based
                # on the channel's prices_entered_with_tax setting.
                instance.unit_price_net_amount = custom_price
                instance.unit_price_gross_amount = custom_price
                instance.undiscounted_unit_price_net_amount = custom_price
                instance.undiscounted_unit_price_gross_amount = custom_price
                instance.save(
                    update_fields=[
                        "base_unit_price_amount",
                        "undiscounted_base_unit_price_amount",
                        "unit_price_net_amount",
                        "unit_price_gross_amount",
                        "undiscounted_unit_price_net_amount",
                        "undiscounted_unit_price_gross_amount",
                    ]
                )

            price_or_tax_changed = (
                cleaned_input.get("price") is not None
                or cleaned_input.get("tax_class") is not None
            )
            price_explicitly_null = (
                cleaned_input.get("_price_in_input")
                and cleaned_input.get("price") is None
            )
            quantity_changed = instance.quantity != instance.old_quantity

            recalculate_order_weight(order)
            order_save_fields = ["weight", "updated_at"]
            if price_or_tax_changed:
                invalidate_order_prices(order, save=False)
                order_save_fields.append("should_refresh_prices")
            order.save(update_fields=order_save_fields)

            if (
                quantity_changed
                and not price_or_tax_changed
                and not price_explicitly_null
            ):
                from ....order.calculations import fetch_order_prices_if_expired

                fetch_order_prices_if_expired(order, manager, force_update=True)

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
