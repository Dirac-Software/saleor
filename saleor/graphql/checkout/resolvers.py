from uuid import UUID

from ...checkout import models
from ...core.exceptions import PermissionDenied
from ...permission.enums import (
    AccountPermissions,
    CheckoutPermissions,
    PaymentPermissions,
)
from ..channel.dataloaders.by_self import ChannelByIdLoader
from ..core.context import SyncWebhookControlContext, get_database_connection_name
from ..core.tracing import traced_resolver
from ..core.utils import from_global_id_or_error
from ..core.validators import validate_one_of_args_is_in_query
from ..utils import get_user_or_app_from_context
from .dataloaders.models import CheckoutByTokenLoader


def resolve_checkout_lines(info):
    queryset = models.CheckoutLine.objects.using(
        get_database_connection_name(info.context)
    ).all()
    return queryset


def resolve_checkouts(info, channel_slug):
    queryset = models.Checkout.objects.using(
        get_database_connection_name(info.context)
    ).all()
    if channel_slug:
        queryset = queryset.filter(channel__slug=channel_slug)
    return queryset


@traced_resolver
def resolve_checkout(info, token, id):
    validate_one_of_args_is_in_query("id", id, "token", token)

    if id:
        _, token = from_global_id_or_error(id, only_type="Checkout", raise_error=True)
        token = UUID(token)

    def with_checkout(checkout):
        if checkout is None:
            return None

        def _with_channel(channel):
            # always return checkout for active channel
            if channel.is_active:
                return SyncWebhookControlContext(
                    node=checkout, allow_sync_webhooks=True
                )
            # resolve checkout for staff or app
            if requester := get_user_or_app_from_context(info.context):
                has_manage_checkout = requester.has_perm(
                    CheckoutPermissions.MANAGE_CHECKOUTS
                )
                has_impersonate_user = requester.has_perm(
                    AccountPermissions.IMPERSONATE_USER
                )
                has_handle_payments = requester.has_perm(
                    PaymentPermissions.HANDLE_PAYMENTS
                )
                if has_manage_checkout or has_impersonate_user or has_handle_payments:
                    return SyncWebhookControlContext(
                        node=checkout, allow_sync_webhooks=True
                    )

            raise PermissionDenied(
                permissions=[
                    CheckoutPermissions.MANAGE_CHECKOUTS,
                    AccountPermissions.IMPERSONATE_USER,
                    PaymentPermissions.HANDLE_PAYMENTS,
                ]
            )

        return (
            ChannelByIdLoader(info.context)
            .load(checkout.channel_id)
            .then(_with_channel)
        )

    return CheckoutByTokenLoader(info.context).load(token).then(with_checkout)


@traced_resolver
def resolve_get_pack_allocation(
    _root,
    info,
    *,
    product_id,
    pack_size,
    channel_slug,
    checkout_id=None,
):
    """Resolve pack allocation preview with validation."""
    from ...attribute.models import Attribute
    from ...attribute.models.product import AssignedProductAttributeValue
    from ...channel.models import Channel
    from ...checkout.pack_utils import get_pack_for_product
    from ...core.db.connection import allow_writer
    from ...product.models import Product
    from .types import PackAllocation

    # Get product and channel
    _, product_pk = from_global_id_or_error(product_id, "Product")

    with allow_writer():
        product = Product.objects.using(get_database_connection_name(info.context)).get(
            pk=product_pk
        )
        channel = Channel.objects.using(get_database_connection_name(info.context)).get(
            slug=channel_slug
        )

        # Get pack allocation
        pack_allocation = get_pack_for_product(product, pack_size, channel)
        pack_qty = sum(qty for _, qty in pack_allocation)

        # Get current quantity in checkout
        current_qty = 0
        if checkout_id:
            from ...checkout.models import Checkout

            _, checkout_pk = from_global_id_or_error(checkout_id, "Checkout")
            checkout = Checkout.objects.using(
                get_database_connection_name(info.context)
            ).get(pk=checkout_pk)
            current_qty = sum(
                line.quantity
                for line in checkout.lines.all()
                if line.variant.product_id == product.id
            )

        # Get minimum-order-quantity attribute value
        min_required = None
        effective_minimum = None
        shortfall = 0
        can_add = True
        message = None

        try:
            attribute = Attribute.objects.get(slug="minimum-order-quantity")
            # Check if this attribute is assigned to this product type
            if attribute.product_types.filter(id=product.product_type_id).exists():
                # Get the assigned value for this product
                assigned_value = (
                    AssignedProductAttributeValue.objects.filter(
                        product=product, value__attribute=attribute
                    )
                    .select_related("value")
                    .first()
                )
                if assigned_value:
                    min_required = int(assigned_value.value.name)

                    # Calculate total available stock across all variants
                    from ...warehouse.availability import get_available_quantity

                    country_code = channel.default_country.code
                    variants = product.variants.all()
                    total_available = 0

                    for variant in variants:
                        available = get_available_quantity(
                            variant,
                            country_code,
                            channel.slug,
                            check_reservations=True,
                        )
                        total_available += available

                    # Calculate what would remain after this order
                    total_qty = current_qty + pack_qty
                    remaining_after_order = total_available - total_qty

                    # Determine effective minimum based on what would remain
                    if total_available < min_required:
                        # Can't meet MOQ with available stock, must take all available
                        effective_minimum = total_available
                        insufficient_remaining = False
                    elif (
                        remaining_after_order > 0
                        and remaining_after_order < min_required
                    ):
                        # Would leave insufficient stock - must take everything
                        effective_minimum = total_available
                        insufficient_remaining = True
                    else:
                        # Either takes everything or leaves sufficient stock
                        effective_minimum = min_required
                        insufficient_remaining = False

                    shortfall = max(0, effective_minimum - total_qty)
                    can_add = shortfall == 0

                    if not can_add:
                        if insufficient_remaining:
                            message = f"Cannot leave less than {min_required} items remaining. Add {shortfall} more to order all {total_available} available."
                        else:
                            message = f"Add {shortfall} more items to meet minimum order of {effective_minimum}"
        except Attribute.DoesNotExist:
            pass

        return PackAllocation(
            allocation=[
                {"variant": v, "quantity": q, "channel_slug": channel.slug}
                for v, q in pack_allocation
            ],
            can_add=can_add,
            current_quantity=current_qty,
            pack_quantity=pack_qty,
            total_quantity=current_qty + pack_qty,
            minimum_required=min_required,
            effective_minimum=effective_minimum,
            shortfall=shortfall,
            message=message,
        )
