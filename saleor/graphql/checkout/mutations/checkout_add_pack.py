"""Checkout add pack mutation."""

import uuid
from dataclasses import dataclass

import graphene
from django.core.exceptions import ValidationError

from ....checkout.actions import call_checkout_info_event
from ....checkout.error_codes import CheckoutErrorCode
from ....checkout.fetch import fetch_checkout_info, fetch_checkout_lines
from ....checkout.pack_utils import get_pack_for_product
from ....checkout.utils import add_variants_to_checkout, invalidate_checkout
from ....warehouse.reservations import get_reservation_length
from ....webhook.event_types import WebhookEventAsyncType
from ...core import ResolveInfo
from ...core.context import SyncWebhookControlContext
from ...core.doc_category import DOC_CATEGORY_CHECKOUT
from ...core.mutations import BaseMutation
from ...core.types import CheckoutError
from ...core.utils import WebhookEventInfo
from ...plugins.dataloaders import get_plugin_manager_promise
from ...product.types import Product as ProductType
from ...site.dataloaders import get_site_promise
from ..types import Checkout
from .utils import (
    CheckoutLineData,
    get_checkout,
    mark_checkout_deliveries_as_stale_if_needed,
)


@dataclass
class MetadataItem:
    """Simple metadata item with key and value."""

    key: str
    value: str


class CheckoutAddPack(BaseMutation):
    """Add a pack of variants to checkout based on pack size."""

    checkout = graphene.Field(Checkout, description="An updated checkout.")

    class Arguments:
        id = graphene.ID(
            description="The checkout's ID.",
            required=True,
        )
        product_id = graphene.ID(
            description="The product ID to create a pack from.",
            required=True,
        )
        pack_size = graphene.Int(
            description="The size of the pack (e.g., 5, 10, 20).",
            required=True,
        )

    class Meta:
        description = (
            "Adds a pack of variants to checkout with minimum order validation."
        )
        doc_category = DOC_CATEGORY_CHECKOUT
        error_type_class = CheckoutError
        error_type_field = "checkout_errors"
        webhook_events_info = [
            WebhookEventInfo(
                type=WebhookEventAsyncType.CHECKOUT_UPDATED,
                description="A checkout was updated.",
            )
        ]

    @classmethod
    def validate_pack_size(cls, pack_size):
        """Validate pack size is positive."""
        if pack_size <= 0:
            raise ValidationError(
                {
                    "pack_size": ValidationError(
                        "Pack size must be greater than 0.",
                        code=CheckoutErrorCode.INVALID.value,
                    )
                }
            )

    @classmethod
    def validate_minimum_order_quantity(
        cls,
        assigned_attr,
        product,
        current_quantity: int,
        pack_quantity: int,
    ):
        """Validate minimum order quantity requirement."""
        if not assigned_attr:
            return  # No minimum requirement

        # Get attribute value from AssignedProductAttributeValue
        min_required = int(assigned_attr.value.name)
        total_quantity = current_quantity + pack_quantity

        if total_quantity < min_required:
            shortfall = min_required - total_quantity
            raise ValidationError(
                {
                    "pack_size": ValidationError(
                        f"Minimum order quantity for {product.name} is {min_required}. "
                        f"Current total would be {total_quantity}. "
                        f"Add {shortfall} more items.",
                        code=CheckoutErrorCode.INSUFFICIENT_STOCK.value,
                    )
                }
            )

    @classmethod
    def perform_mutation(  # type: ignore[override]
        cls,
        _root,
        info: ResolveInfo,
        /,
        *,
        id,
        product_id,
        pack_size,
    ):
        # Validate pack size
        cls.validate_pack_size(pack_size)

        # Get checkout
        checkout = get_checkout(cls, info, id=id)

        # Get product
        product = cls.get_node_or_error(
            info, product_id, only_type=ProductType, field="product_id"
        )

        # Get pack allocation
        pack_allocation = get_pack_for_product(product, pack_size, checkout.channel)

        if not pack_allocation:
            raise ValidationError(
                {
                    "product_id": ValidationError(
                        "Cannot create pack - no variants available.",
                        code=CheckoutErrorCode.PRODUCT_NOT_PUBLISHED.value,
                    )
                }
            )

        # Calculate quantities
        existing_lines_info, _ = fetch_checkout_lines(
            checkout, skip_lines_with_unavailable_variants=False
        )
        current_qty = sum(
            line.line.quantity
            for line in existing_lines_info
            if line.variant.product_id == product.id
        )
        pack_qty = sum(qty for _, qty in pack_allocation)

        # Get minimum-order-quantity attribute value
        from ....attribute.models import Attribute
        from ....attribute.models.product import AssignedProductAttributeValue

        assigned_attr = None
        try:
            attribute = Attribute.objects.get(slug="minimum-order-quantity")
            # Check if this attribute is assigned to this product type
            if attribute.product_types.filter(id=product.product_type_id).exists():
                # Get the assigned value for this product
                assigned_attr = (
                    AssignedProductAttributeValue.objects.filter(
                        product=product, value__attribute=attribute
                    )
                    .select_related("value")
                    .first()
                )
        except Attribute.DoesNotExist:
            pass

        # Validate minimum order quantity
        cls.validate_minimum_order_quantity(
            assigned_attr, product, current_qty, pack_qty
        )

        # Prepare checkout lines data
        pack_id = str(uuid.uuid4())
        variants_to_add = []
        checkout_lines_data = []

        for variant, quantity in pack_allocation:
            variants_to_add.append(variant)
            checkout_lines_data.append(
                CheckoutLineData(
                    variant_id=str(variant.id),
                    quantity=quantity,
                    quantity_to_update=True,
                    line_id=None,
                    custom_price=None,
                    metadata_list=[
                        MetadataItem(key="pack_id", value=pack_id),
                        MetadataItem(key="pack_size", value=str(pack_size)),
                        MetadataItem(key="is_pack_item", value="true"),
                    ],
                )
            )

        # Add variants to checkout (using existing utility)
        manager = get_plugin_manager_promise(info.context).get()
        checkout_info = fetch_checkout_info(checkout, [], manager)
        site = get_site_promise(info.context).get()

        updated_checkout = add_variants_to_checkout(
            checkout,
            variants_to_add,
            checkout_lines_data,
            checkout_info.channel,
            replace=False,
            replace_reservations=True,
            reservation_length=get_reservation_length(
                site=site, user=info.context.user
            ),
        )

        # Update checkout info
        lines, _ = fetch_checkout_lines(updated_checkout)
        checkout_info.lines = lines

        # Mark deliveries as stale and invalidate checkout
        shipping_update_fields = mark_checkout_deliveries_as_stale_if_needed(
            checkout_info.checkout, lines
        )
        invalidate_update_fields = invalidate_checkout(
            checkout_info, lines, manager, save=False
        )
        updated_checkout.save(
            update_fields=shipping_update_fields + invalidate_update_fields
        )

        # Fire webhook event
        call_checkout_info_event(
            manager,
            event_name=WebhookEventAsyncType.CHECKOUT_UPDATED,
            checkout_info=checkout_info,
            lines=lines,
        )

        return CheckoutAddPack(
            checkout=SyncWebhookControlContext(node=updated_checkout)
        )
