from unittest.mock import ANY, call, patch

import graphene

# Override order_unconfirmed to ensure it has lines
import pytest
from django.test import override_settings

from .....core.models import EventDelivery
from .....core.notification.utils import get_site_context
from .....core.notify import NotifyEventType
from .....core.prices import quantize_price
from .....core.tests.utils import get_site_context_payload
from .....order import OrderStatus
from .....order import events as order_events
from .....order.actions import (
    WEBHOOK_EVENTS_FOR_ORDER_CHARGED,
    WEBHOOK_EVENTS_FOR_ORDER_CONFIRMED,
    call_order_event,
    handle_fully_paid_order,
    order_charged,
    order_confirmed,
)
from .....order.error_codes import OrderErrorCode
from .....order.fetch import fetch_order_info
from .....order.models import OrderEvent
from .....order.notifications import get_default_order_payload
from .....order.utils import updates_amounts_for_order
from .....product.models import ProductVariant
from .....webhook.event_types import WebhookEventAsyncType
from .....webhook.utils import get_webhooks_for_multiple_events
from ....core.utils import to_global_id_or_none
from ....tests.utils import assert_no_permission, get_graphql_content


@pytest.fixture
def order_unconfirmed(order_with_lines):
    """Order with lines in UNCONFIRMED status."""
    order_with_lines.status = OrderStatus.UNCONFIRMED
    order_with_lines.save(update_fields=["status"])
    return order_with_lines


ORDER_CONFIRM_MUTATION = """
    mutation orderConfirm($id: ID!) {
        orderConfirm(id: $id) {
            errors {
                field
                code
            }
            order {
                status
            }
        }
    }
"""


def setup_order_with_allocation_sources(
    order, owned_warehouse, channel, purchase_order_item
):
    """Set up order with allocations and sources for OrderConfirm tests."""
    from .....order.fetch import OrderLineInfo
    from .....plugins.manager import get_plugins_manager
    from .....warehouse.management import allocate_stocks
    from .....warehouse.models import Allocation, Stock

    order_line = order.lines.first()
    if not order_line:
        return

    variant = order_line.variant

    # Clear any existing allocations from the order
    Allocation.objects.filter(order_line__order=order).delete()

    # Update POI to match the test's variant and warehouse
    purchase_order_item.product_variant = variant
    purchase_order_item.order.destination_warehouse = owned_warehouse
    purchase_order_item.order.save(update_fields=["destination_warehouse"])

    # Ensure POI has enough quantity for the order
    needed_qty = order_line.quantity
    if purchase_order_item.quantity_ordered < needed_qty:
        purchase_order_item.quantity_ordered = needed_qty * 2  # Buffer
    purchase_order_item.save(update_fields=["product_variant", "quantity_ordered"])

    # Create stock in owned warehouse with enough quantity
    stock, created = Stock.objects.get_or_create(
        warehouse=owned_warehouse,
        product_variant=variant,
        defaults={"quantity": 10000, "quantity_allocated": 0},
    )
    if not created:
        stock.quantity = 10000
        stock.quantity_allocated = 0
        stock.save(update_fields=["quantity", "quantity_allocated"])

    # Allocate stocks (creates allocations and sources using the POI)
    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=order_line.quantity)],
        "US",
        channel,
        manager=get_plugins_manager(allow_replica=False),
    )

    # Reset order to UNCONFIRMED for the test
    # (allocate_stocks auto-confirms if all allocations have sources)
    order.refresh_from_db()
    order.status = OrderStatus.UNCONFIRMED
    order.save(update_fields=["status"])


@patch("saleor.order.actions.handle_fully_paid_order", wraps=handle_fully_paid_order)
@patch(
    "saleor.graphql.order.mutations.order_confirm.order_confirmed",
    wraps=order_confirmed,
)
@patch(
    "saleor.graphql.order.mutations.order_confirm.order_charged", wraps=order_charged
)
@patch("saleor.plugins.manager.PluginsManager.notify")
@patch("saleor.payment.gateway.capture")
def test_order_confirm(
    capture_mock,
    mocked_notify,
    order_charged_mock,
    order_confirmed_mock,
    handle_fully_paid_order_mock,
    staff_api_client,
    order_unconfirmed,
    permission_group_manage_orders,
    payment_txn_preauth,
    site_settings,
    owned_warehouse,
    channel_USD,
    purchase_order_item,
):
    # given
    setup_order_with_allocation_sources(
        order_unconfirmed, owned_warehouse, channel_USD, purchase_order_item
    )

    webhook_event_map = get_webhooks_for_multiple_events(
        WEBHOOK_EVENTS_FOR_ORDER_CONFIRMED.union(WEBHOOK_EVENTS_FOR_ORDER_CHARGED)
    )
    payment_txn_preauth.order = order_unconfirmed
    payment_txn_preauth.captured_amount = order_unconfirmed.total.gross.amount
    payment_txn_preauth.total = order_unconfirmed.total.gross.amount
    payment_txn_preauth.save(update_fields=["order", "captured_amount", "total"])

    order_unconfirmed.total_charged = order_unconfirmed.total.gross
    order_unconfirmed.save(update_fields=["total_charged_amount"])
    updates_amounts_for_order(order_unconfirmed)

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    assert not OrderEvent.objects.exists()

    # when
    response = staff_api_client.post_graphql(
        ORDER_CONFIRM_MUTATION,
        {"id": graphene.Node.to_global_id("Order", order_unconfirmed.id)},
    )

    # then
    order_data = get_graphql_content(response)["data"]["orderConfirm"]["order"]

    assert order_data["status"] == OrderStatus.UNFULFILLED.upper()
    order_unconfirmed.refresh_from_db()
    assert order_unconfirmed.status == OrderStatus.UNFULFILLED
    assert OrderEvent.objects.count() == 3
    assert OrderEvent.objects.filter(
        order=order_unconfirmed,
        user=staff_api_client.user,
        type=order_events.OrderEvents.CONFIRMED,
    ).exists()

    assert OrderEvent.objects.filter(
        order=order_unconfirmed,
        user=staff_api_client.user,
        type=order_events.OrderEvents.ORDER_FULLY_PAID,
    ).exists()

    assert OrderEvent.objects.filter(
        order=order_unconfirmed,
        user=staff_api_client.user,
        type=order_events.OrderEvents.PAYMENT_CAPTURED,
        parameters__amount=payment_txn_preauth.get_total().amount,
    ).exists()

    order_info = fetch_order_info(order_unconfirmed)

    order_charged_mock.assert_called_once_with(
        order_info,
        staff_api_client.user,
        None,
        payment_txn_preauth.total,
        payment_txn_preauth,
        ANY,
        site_settings,
        payment_txn_preauth.gateway,
        webhook_event_map=webhook_event_map,
    )
    order_confirmed_mock.assert_called_once_with(
        order_unconfirmed,
        staff_api_client.user,
        None,
        ANY,
        send_confirmation_email=True,
        webhook_event_map=webhook_event_map,
    )
    handle_fully_paid_order_mock.assert_called_once_with(
        ANY,
        order_info,
        user=staff_api_client.user,
        app=None,
        site_settings=site_settings,
        gateway=payment_txn_preauth.gateway,
        webhook_event_map=webhook_event_map,
    )

    capture_mock.assert_called_once_with(
        payment_txn_preauth, ANY, channel_slug=order_unconfirmed.channel.slug
    )
    expected_confirmed_payload = {
        "order": get_default_order_payload(order_unconfirmed, ""),
        "recipient_email": order_unconfirmed.user.email,
        "requester_user_id": to_global_id_or_none(staff_api_client.user),
        "requester_app_id": None,
        **get_site_context_payload(site_settings.site),
    }
    expected_payment_payload = {
        "order": get_default_order_payload(order_unconfirmed),
        "recipient_email": order_unconfirmed.user.email,
        "payment": {
            "created": payment_txn_preauth.created_at,
            "modified": payment_txn_preauth.modified_at,
            "charge_status": payment_txn_preauth.charge_status,
            "total": quantize_price(
                payment_txn_preauth.total, payment_txn_preauth.currency
            ),
            "captured_amount": quantize_price(
                payment_txn_preauth.captured_amount, payment_txn_preauth.currency
            ),
            "currency": payment_txn_preauth.currency,
        },
        **get_site_context(),
    }

    mocked_notify.assert_has_calls(
        [
            call(
                NotifyEventType.ORDER_PAYMENT_CONFIRMATION,
                payload_func=ANY,
                channel_slug=order_unconfirmed.channel.slug,
            ),
            call(
                NotifyEventType.ORDER_CONFIRMED,
                payload_func=ANY,
                channel_slug=order_unconfirmed.channel.slug,
            ),
        ]
    )
    assert (
        mocked_notify.call_args_list[1].kwargs["payload_func"]()
        == expected_confirmed_payload
    )
    assert (
        mocked_notify.call_args_list[0].kwargs["payload_func"]()
        == expected_payment_payload
    )


@patch("saleor.plugins.manager.PluginsManager.notify")
@patch("saleor.payment.gateway.capture")
def test_order_confirm_without_sku(
    capture_mock,
    mocked_notify,
    staff_api_client,
    order_unconfirmed,
    permission_group_manage_orders,
    payment_txn_preauth,
    site_settings,
    owned_warehouse,
    channel_USD,
    purchase_order_item,
):
    # given
    setup_order_with_allocation_sources(
        order_unconfirmed, owned_warehouse, channel_USD, purchase_order_item
    )

    order_unconfirmed.lines.update(product_sku=None)
    ProductVariant.objects.update(sku=None)

    payment_txn_preauth.order = order_unconfirmed
    payment_txn_preauth.save()
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    assert not OrderEvent.objects.exists()

    # when
    response = staff_api_client.post_graphql(
        ORDER_CONFIRM_MUTATION,
        {"id": graphene.Node.to_global_id("Order", order_unconfirmed.id)},
    )

    # then
    order_data = get_graphql_content(response)["data"]["orderConfirm"]["order"]

    assert order_data["status"] == OrderStatus.UNFULFILLED.upper()
    order_unconfirmed.refresh_from_db()
    assert order_unconfirmed.status == OrderStatus.UNFULFILLED
    assert OrderEvent.objects.count() == 2
    assert OrderEvent.objects.filter(
        order=order_unconfirmed,
        user=staff_api_client.user,
        type=order_events.OrderEvents.CONFIRMED,
    ).exists()

    assert OrderEvent.objects.filter(
        order=order_unconfirmed,
        user=staff_api_client.user,
        type=order_events.OrderEvents.PAYMENT_CAPTURED,
        parameters__amount=payment_txn_preauth.get_total().amount,
    ).exists()

    capture_mock.assert_called_once_with(
        payment_txn_preauth, ANY, channel_slug=order_unconfirmed.channel.slug
    )
    expected_payload = {
        "order": get_default_order_payload(order_unconfirmed, ""),
        "recipient_email": order_unconfirmed.user.email,
        "requester_user_id": to_global_id_or_none(staff_api_client.user),
        "requester_app_id": None,
        **get_site_context_payload(site_settings.site),
    }

    assert mocked_notify.call_count == 1
    call_args = mocked_notify.call_args_list[0]
    called_args = call_args.args
    called_kwargs = call_args.kwargs
    assert called_args[0] == NotifyEventType.ORDER_CONFIRMED
    assert len(called_kwargs) == 2
    assert called_kwargs["payload_func"]() == expected_payload
    assert called_kwargs["channel_slug"] == order_unconfirmed.channel.slug


def test_order_confirm_unfulfilled(
    staff_api_client, order, permission_group_manage_orders
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    response = staff_api_client.post_graphql(
        ORDER_CONFIRM_MUTATION, {"id": graphene.Node.to_global_id("Order", order.id)}
    )
    content = get_graphql_content(response)["data"]["orderConfirm"]
    errors = content["errors"]

    order.refresh_from_db()
    assert order.status == OrderStatus.UNFULFILLED
    assert content["order"] is None
    assert len(errors) == 1
    assert errors[0]["field"] == "id"
    assert errors[0]["code"] == OrderErrorCode.INVALID.name


def test_order_confirm_no_products_in_order(
    staff_api_client, order_unconfirmed, permission_group_manage_orders
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order_unconfirmed.lines.set([])
    response = staff_api_client.post_graphql(
        ORDER_CONFIRM_MUTATION,
        {"id": graphene.Node.to_global_id("Order", order_unconfirmed.id)},
    )
    content = get_graphql_content(response)["data"]["orderConfirm"]
    errors = content["errors"]

    order_unconfirmed.refresh_from_db()
    assert order_unconfirmed.is_unconfirmed()
    assert content["order"] is None
    assert len(errors) == 1
    assert errors[0]["field"] == "id"
    assert errors[0]["code"] == OrderErrorCode.INVALID.name


@patch("saleor.payment.gateway.capture")
def test_order_confirm_wont_call_capture_for_non_active_payment(
    capture_mock,
    staff_api_client,
    order_unconfirmed,
    permission_group_manage_orders,
    payment_txn_preauth,
    owned_warehouse,
    channel_USD,
    purchase_order_item,
):
    setup_order_with_allocation_sources(
        order_unconfirmed, owned_warehouse, channel_USD, purchase_order_item
    )
    payment_txn_preauth.order = order_unconfirmed
    payment_txn_preauth.is_active = False
    payment_txn_preauth.save()
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    assert not OrderEvent.objects.exists()
    response = staff_api_client.post_graphql(
        ORDER_CONFIRM_MUTATION,
        {"id": graphene.Node.to_global_id("Order", order_unconfirmed.id)},
    )
    order_data = get_graphql_content(response)["data"]["orderConfirm"]["order"]

    assert order_data["status"] == OrderStatus.UNFULFILLED.upper()
    order_unconfirmed.refresh_from_db()
    assert order_unconfirmed.status == OrderStatus.UNFULFILLED
    assert OrderEvent.objects.count() == 1
    assert OrderEvent.objects.filter(
        order=order_unconfirmed,
        user=staff_api_client.user,
        type=order_events.OrderEvents.CONFIRMED,
    ).exists()
    assert not capture_mock.called


def test_order_confirm_update_display_gross_prices(
    staff_api_client,
    order_with_lines,
    permission_group_manage_orders,
    owned_warehouse,
    purchase_order_item,
):
    # given
    order = order_with_lines
    order.status = OrderStatus.UNCONFIRMED
    order.save(update_fields=["status"])
    channel = order.channel
    setup_order_with_allocation_sources(
        order, owned_warehouse, channel, purchase_order_item
    )
    tax_config = channel.tax_configuration

    # Change the current display_gross_prices to the opposite of what is set in the
    # order.display_gross_prices.
    new_display_gross_prices = not order.display_gross_prices

    tax_config.display_gross_prices = new_display_gross_prices
    tax_config.save()
    tax_config.country_exceptions.all().delete()

    # when
    permission_group_manage_orders.user_set.add(staff_api_client.user)

    response = staff_api_client.post_graphql(
        ORDER_CONFIRM_MUTATION,
        {"id": graphene.Node.to_global_id("Order", order.id)},
    )
    content = get_graphql_content(response)

    # then
    assert not content["data"]["orderConfirm"]["errors"]
    order.refresh_from_db()
    assert order.display_gross_prices == new_display_gross_prices


def test_order_confirm_by_user_no_channel_access(
    staff_api_client,
    order_unconfirmed,
    permission_group_all_perms_channel_USD_only,
    payment_txn_preauth,
    channel_PLN,
):
    # given
    order_unconfirmed.total_charged = order_unconfirmed.total.gross
    order_unconfirmed.channel = channel_PLN
    order_unconfirmed.save(update_fields=["total_charged_amount", "channel"])

    payment_txn_preauth.order = order_unconfirmed
    payment_txn_preauth.captured_amount = order_unconfirmed.total.gross.amount
    payment_txn_preauth.total = order_unconfirmed.total.gross.amount
    payment_txn_preauth.save(update_fields=["order", "captured_amount", "total"])

    permission_group_all_perms_channel_USD_only.user_set.add(staff_api_client.user)
    assert not OrderEvent.objects.exists()

    # when
    response = staff_api_client.post_graphql(
        ORDER_CONFIRM_MUTATION,
        {"id": graphene.Node.to_global_id("Order", order_unconfirmed.id)},
    )

    # then
    assert_no_permission(response)


@patch("saleor.order.actions.handle_fully_paid_order", wraps=handle_fully_paid_order)
@patch(
    "saleor.graphql.order.mutations.order_confirm.order_confirmed",
    wraps=order_confirmed,
)
@patch(
    "saleor.graphql.order.mutations.order_confirm.order_charged", wraps=order_charged
)
@patch("saleor.plugins.manager.PluginsManager.notify")
@patch("saleor.payment.gateway.capture")
def test_order_confirm_by_app(
    capture_mock,
    mocked_notify,
    order_charged_mock,
    order_confirmed_mock,
    handle_fully_paid_order_mock,
    app_api_client,
    order_unconfirmed,
    permission_manage_orders,
    payment_txn_preauth,
    site_settings,
    owned_warehouse,
    channel_USD,
    purchase_order_item,
):
    # given
    setup_order_with_allocation_sources(
        order_unconfirmed, owned_warehouse, channel_USD, purchase_order_item
    )

    webhook_event_map = get_webhooks_for_multiple_events(
        WEBHOOK_EVENTS_FOR_ORDER_CONFIRMED.union(WEBHOOK_EVENTS_FOR_ORDER_CHARGED)
    )
    payment_txn_preauth.order = order_unconfirmed
    payment_txn_preauth.captured_amount = order_unconfirmed.total.gross.amount
    payment_txn_preauth.total = order_unconfirmed.total.gross.amount
    payment_txn_preauth.save(update_fields=["order", "captured_amount", "total"])

    order_unconfirmed.total_charged = order_unconfirmed.total.gross
    order_unconfirmed.save(update_fields=["total_charged_amount"])
    updates_amounts_for_order(order_unconfirmed)

    assert not OrderEvent.objects.exists()

    # when
    response = app_api_client.post_graphql(
        ORDER_CONFIRM_MUTATION,
        {"id": graphene.Node.to_global_id("Order", order_unconfirmed.id)},
        permissions=(permission_manage_orders,),
    )

    # then
    order_data = get_graphql_content(response)["data"]["orderConfirm"]["order"]

    assert order_data["status"] == OrderStatus.UNFULFILLED.upper()
    order_unconfirmed.refresh_from_db()
    assert order_unconfirmed.status == OrderStatus.UNFULFILLED
    assert OrderEvent.objects.count() == 3
    assert OrderEvent.objects.filter(
        order=order_unconfirmed,
        app=app_api_client.app,
        type=order_events.OrderEvents.CONFIRMED,
    ).exists()

    assert OrderEvent.objects.filter(
        order=order_unconfirmed,
        app=app_api_client.app,
        type=order_events.OrderEvents.ORDER_FULLY_PAID,
    ).exists()

    assert OrderEvent.objects.filter(
        order=order_unconfirmed,
        app=app_api_client.app,
        type=order_events.OrderEvents.PAYMENT_CAPTURED,
        parameters__amount=payment_txn_preauth.get_total().amount,
    ).exists()

    order_info = fetch_order_info(order_unconfirmed)

    order_charged_mock.assert_called_once_with(
        order_info,
        None,
        app_api_client.app,
        payment_txn_preauth.total,
        payment_txn_preauth,
        ANY,
        site_settings,
        payment_txn_preauth.gateway,
        webhook_event_map=webhook_event_map,
    )
    order_confirmed_mock.assert_called_once_with(
        order_unconfirmed,
        None,
        app_api_client.app,
        ANY,
        send_confirmation_email=True,
        webhook_event_map=webhook_event_map,
    )
    handle_fully_paid_order_mock.assert_called_once_with(
        ANY,
        order_info,
        user=None,
        app=app_api_client.app,
        site_settings=site_settings,
        gateway=payment_txn_preauth.gateway,
        webhook_event_map=webhook_event_map,
    )

    capture_mock.assert_called_once_with(
        payment_txn_preauth, ANY, channel_slug=order_unconfirmed.channel.slug
    )
    expected_confirmed_payload = {
        "order": get_default_order_payload(order_unconfirmed, ""),
        "recipient_email": order_unconfirmed.user.email,
        "requester_user_id": None,
        "requester_app_id": to_global_id_or_none(app_api_client.app),
        **get_site_context_payload(site_settings.site),
    }
    expected_payment_payload = {
        "order": get_default_order_payload(order_unconfirmed),
        "recipient_email": order_unconfirmed.user.email,
        "payment": {
            "created": payment_txn_preauth.created_at,
            "modified": payment_txn_preauth.modified_at,
            "charge_status": payment_txn_preauth.charge_status,
            "total": quantize_price(
                payment_txn_preauth.total, payment_txn_preauth.currency
            ),
            "captured_amount": quantize_price(
                payment_txn_preauth.captured_amount, payment_txn_preauth.currency
            ),
            "currency": payment_txn_preauth.currency,
        },
        **get_site_context(),
    }

    mocked_notify.assert_has_calls(
        [
            call(
                NotifyEventType.ORDER_PAYMENT_CONFIRMATION,
                payload_func=ANY,
                channel_slug=order_unconfirmed.channel.slug,
            ),
            call(
                NotifyEventType.ORDER_CONFIRMED,
                payload_func=ANY,
                channel_slug=order_unconfirmed.channel.slug,
            ),
        ]
    )
    assert (
        mocked_notify.call_args_list[1].kwargs["payload_func"]()
        == expected_confirmed_payload
    )
    assert (
        mocked_notify.call_args_list[0].kwargs["payload_func"]()
        == expected_payment_payload
    )


@patch("saleor.order.actions.handle_fully_paid_order")
@patch("saleor.plugins.manager.PluginsManager.notify")
@patch("saleor.payment.gateway.capture")
def test_order_confirm_skip_address_validation(
    capture_mock,
    mocked_notify,
    handle_fully_paid_order_mock,
    staff_api_client,
    order_unconfirmed,
    permission_group_manage_orders,
    payment_txn_preauth,
    site_settings,
    graphql_address_data,
    owned_warehouse,
    channel_USD,
    purchase_order_item,
):
    # given
    order = order_unconfirmed
    setup_order_with_allocation_sources(
        order, owned_warehouse, channel_USD, purchase_order_item
    )
    payment_txn_preauth.order = order
    payment_txn_preauth.captured_amount = order.total.gross.amount
    payment_txn_preauth.total = order.total.gross.amount
    payment_txn_preauth.save(update_fields=["order", "captured_amount", "total"])

    address_data = graphql_address_data
    invalid_postal_code = "invalid_postal_code"
    address_data["postalCode"] = invalid_postal_code

    shipping_address = order.shipping_address
    shipping_address.postal_code = invalid_postal_code
    shipping_address.save(update_fields=["postal_code"])
    billing_address = order.billing_address
    billing_address.postal_code = invalid_postal_code
    billing_address.save(update_fields=["postal_code"])

    order.total_charged = order.total.gross
    order.save(update_fields=["total_charged_amount"])
    updates_amounts_for_order(order)
    permission_group_manage_orders.user_set.add(staff_api_client.user)

    # when
    response = staff_api_client.post_graphql(
        ORDER_CONFIRM_MUTATION,
        {"id": graphene.Node.to_global_id("Order", order.id)},
    )
    content = get_graphql_content(response)

    # then
    data = content["data"]["orderConfirm"]
    assert not data["errors"]
    order.refresh_from_db()
    assert order.shipping_address.postal_code == invalid_postal_code
    assert order.billing_address.postal_code == invalid_postal_code
    assert order.status == OrderStatus.UNFULFILLED


@patch(
    "saleor.order.actions.call_order_event",
    wraps=call_order_event,
)
@patch("saleor.webhook.transport.synchronous.transport.send_webhook_request_sync")
@patch(
    "saleor.webhook.transport.asynchronous.transport.send_webhook_request_async.apply_async"
)
@patch("saleor.payment.gateway.capture")
@override_settings(PLUGINS=["saleor.plugins.webhook.plugin.WebhookPlugin"])
def test_order_confirm_triggers_webhooks(
    capture_mock,
    mocked_send_webhook_request_async,
    mocked_send_webhook_request_sync,
    wrapped_call_order_event,
    setup_order_webhooks,
    staff_api_client,
    order_unconfirmed,
    permission_group_manage_orders,
    payment_txn_preauth,
    settings,
    owned_warehouse,
    channel_USD,
    purchase_order_item,
):
    # given
    setup_order_with_allocation_sources(
        order_unconfirmed, owned_warehouse, channel_USD, purchase_order_item
    )
    mocked_send_webhook_request_sync.return_value = []
    (
        tax_webhook,
        shipping_filter_webhook,
        additional_order_webhook,
    ) = setup_order_webhooks(
        [
            WebhookEventAsyncType.ORDER_CONFIRMED,
            WebhookEventAsyncType.ORDER_UPDATED,
            WebhookEventAsyncType.ORDER_FULLY_PAID,
            WebhookEventAsyncType.ORDER_PAID,
        ]
    )

    payment_txn_preauth.order = order_unconfirmed
    payment_txn_preauth.captured_amount = order_unconfirmed.total.gross.amount
    payment_txn_preauth.total = order_unconfirmed.total.gross.amount
    payment_txn_preauth.save(update_fields=["order", "captured_amount", "total"])

    order_unconfirmed.total_charged = order_unconfirmed.total.gross
    order_unconfirmed.save(update_fields=["total_charged_amount"])
    updates_amounts_for_order(order_unconfirmed)

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    assert not OrderEvent.objects.exists()

    # when
    response = staff_api_client.post_graphql(
        ORDER_CONFIRM_MUTATION,
        {"id": graphene.Node.to_global_id("Order", order_unconfirmed.id)},
    )

    # then
    assert not get_graphql_content(response)["data"]["orderConfirm"]["errors"]

    # confirm that event delivery was generated for each webhook.
    order_confirmed_delivery = EventDelivery.objects.get(
        webhook_id=additional_order_webhook.id,
        event_type=WebhookEventAsyncType.ORDER_CONFIRMED,
    )
    order_updated_delivery = EventDelivery.objects.get(
        webhook_id=additional_order_webhook.id,
        event_type=WebhookEventAsyncType.ORDER_UPDATED,
    )
    order_fully_paid_delivery = EventDelivery.objects.get(
        webhook_id=additional_order_webhook.id,
        event_type=WebhookEventAsyncType.ORDER_FULLY_PAID,
    )
    order_paid_delivery = EventDelivery.objects.get(
        webhook_id=additional_order_webhook.id,
        event_type=WebhookEventAsyncType.ORDER_PAID,
    )
    order_deliveries = [
        order_confirmed_delivery,
        order_updated_delivery,
        order_fully_paid_delivery,
        order_paid_delivery,
    ]

    mocked_send_webhook_request_async.assert_has_calls(
        [
            call(
                kwargs={"event_delivery_id": delivery.id, "telemetry_context": ANY},
                queue=settings.ORDER_WEBHOOK_EVENTS_CELERY_QUEUE_NAME,
                MessageGroupId="example.com:saleorappadditional",
            )
            for delivery in order_deliveries
        ],
        any_order=True,
    )
    assert not mocked_send_webhook_request_sync.called
    assert wrapped_call_order_event.called


def test_order_confirm_blocked_without_allocation_sources(
    staff_api_client,
    order_unconfirmed,
    permission_group_manage_orders,
    owned_warehouse,
):
    """OrderConfirm mutation fails when allocations lack AllocationSources."""
    from .....warehouse.models import Allocation, Stock

    # given
    order_line = order_unconfirmed.lines.first()
    variant = order_line.variant
    stock = Stock.objects.create(
        warehouse=owned_warehouse, product_variant=variant, quantity=100
    )

    # Create allocation WITHOUT AllocationSource
    Allocation.objects.create(order_line=order_line, stock=stock, quantity_allocated=50)

    permission_group_manage_orders.user_set.add(staff_api_client.user)

    # when
    response = staff_api_client.post_graphql(
        ORDER_CONFIRM_MUTATION,
        {"id": graphene.Node.to_global_id("Order", order_unconfirmed.id)},
    )

    # then
    content = get_graphql_content(response)
    errors = content["data"]["orderConfirm"]["errors"]
    assert len(errors) == 1
    assert errors[0]["code"] == OrderErrorCode.INVALID.name

    # Order should remain UNCONFIRMED
    order_unconfirmed.refresh_from_db()
    assert order_unconfirmed.status == OrderStatus.UNCONFIRMED


def test_order_confirm_succeeds_with_allocation_sources(
    staff_api_client,
    order_unconfirmed,
    permission_group_manage_orders,
    owned_warehouse,
    purchase_order_item,
    channel_USD,
):
    """OrderConfirm succeeds when allocations have proper AllocationSources."""
    from .....order.fetch import OrderLineInfo
    from .....plugins.manager import get_plugins_manager
    from .....warehouse.management import allocate_stocks
    from .....warehouse.models import Allocation, Stock

    # given
    order_line = order_unconfirmed.lines.first()
    variant = order_line.variant

    # Clear any existing allocations
    Allocation.objects.filter(order_line__order=order_unconfirmed).delete()

    # Set up purchase order item for the variant and warehouse
    purchase_order_item.product_variant = variant
    purchase_order_item.order.destination_warehouse = owned_warehouse
    purchase_order_item.quantity_ordered = 100
    purchase_order_item.order.save(update_fields=["destination_warehouse"])
    purchase_order_item.save(update_fields=["product_variant", "quantity_ordered"])

    Stock.objects.create(
        warehouse=owned_warehouse, product_variant=variant, quantity=100
    )

    # Use allocate_stocks which creates AllocationSources automatically
    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=order_line.quantity)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # Reset order to UNCONFIRMED for the test
    # (allocate_stocks auto-confirms if all allocations have sources)
    order_unconfirmed.refresh_from_db()
    order_unconfirmed.status = OrderStatus.UNCONFIRMED
    order_unconfirmed.save(update_fields=["status"])

    permission_group_manage_orders.user_set.add(staff_api_client.user)

    # when
    response = staff_api_client.post_graphql(
        ORDER_CONFIRM_MUTATION,
        {"id": graphene.Node.to_global_id("Order", order_unconfirmed.id)},
    )

    # then
    content = get_graphql_content(response)
    order_data = content["data"]["orderConfirm"]["order"]

    assert order_data["status"] == OrderStatus.UNFULFILLED.upper()
    order_unconfirmed.refresh_from_db()
    assert order_unconfirmed.status == OrderStatus.UNFULFILLED


def test_order_confirm_blocked_with_nonowned_warehouse(
    staff_api_client,
    order_unconfirmed,
    permission_group_manage_orders,
    nonowned_warehouse,
    channel_USD,
):
    """OrderConfirm fails when allocations are in non-owned warehouses."""
    from .....order.fetch import OrderLineInfo
    from .....plugins.manager import get_plugins_manager
    from .....warehouse.management import allocate_stocks
    from .....warehouse.models import Stock

    # given
    order_line = order_unconfirmed.lines.first()
    variant = order_line.variant
    Stock.objects.create(
        warehouse=nonowned_warehouse, product_variant=variant, quantity=100
    )

    # Allocate in non-owned warehouse (no AllocationSources created)
    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=order_line.quantity)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    permission_group_manage_orders.user_set.add(staff_api_client.user)

    # when
    response = staff_api_client.post_graphql(
        ORDER_CONFIRM_MUTATION,
        {"id": graphene.Node.to_global_id("Order", order_unconfirmed.id)},
    )

    # then
    content = get_graphql_content(response)
    errors = content["data"]["orderConfirm"]["errors"]
    assert len(errors) == 1
    assert errors[0]["code"] == OrderErrorCode.INVALID.name

    # Order should remain UNCONFIRMED
    order_unconfirmed.refresh_from_db()
    assert order_unconfirmed.status == OrderStatus.UNCONFIRMED
