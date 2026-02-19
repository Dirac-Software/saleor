from decimal import Decimal

import graphene
from django.test import override_settings
from django.utils import timezone

from ....webhook.event_types import WebhookEventAsyncType, WebhookEventSyncType
from ....webhook.models import Webhook
from ..subscription_payload import (
    generate_payload_from_subscription,
    generate_payload_promise_from_subscription,
    generate_pre_save_payloads,
    get_pre_save_payload_key,
    initialize_request,
)


def test_initialize_request(app):
    # when
    request = initialize_request(app=app)

    # then
    assert request.dataloaders == {}
    assert request.request_time is not None


def test_initialize_request_pass_params(app):
    # given
    dataloaders = {"test": "test"}
    request_time = timezone.now()

    # when
    request = initialize_request(
        app=app, dataloaders=dataloaders, request_time=request_time
    )

    # then
    assert request.dataloaders is dataloaders
    assert request.request_time is request_time


SUBSCRIPTION_QUERY = """
    subscription {
        event {
            ... on ProductVariantUpdated {
                productVariant {
                    name
                }
            }
        }
    }
"""


@override_settings(ENABLE_LIMITING_WEBHOOKS_FOR_IDENTICAL_PAYLOADS=False)
def test_generate_pre_save_payloads_disabled_with_env(webhook_app, variant):
    # given
    webhook = Webhook.objects.create(
        name="Webhook",
        app=webhook_app,
        subscription_query=SUBSCRIPTION_QUERY,
    )
    event_type = WebhookEventAsyncType.PRODUCT_VARIANT_UPDATED
    webhook.events.create(event_type=event_type)

    # when
    pre_save_payloads = generate_pre_save_payloads(
        [webhook],
        [variant],
        WebhookEventAsyncType.PRODUCT_VARIANT_UPDATED,
        None,
        timezone.now(),
    )

    # then
    assert pre_save_payloads == {}


@override_settings(ENABLE_LIMITING_WEBHOOKS_FOR_IDENTICAL_PAYLOADS=True)
def test_generate_pre_save_payloads_no_subscription_query(webhook_app, variant):
    # given
    webhook = Webhook.objects.create(
        name="Webhook",
        app=webhook_app,
        subscription_query=None,
    )
    event_type = WebhookEventAsyncType.PRODUCT_VARIANT_UPDATED
    webhook.events.create(event_type=event_type)

    # when
    pre_save_payloads = generate_pre_save_payloads(
        [webhook],
        [variant],
        WebhookEventAsyncType.PRODUCT_VARIANT_UPDATED,
        None,
        timezone.now(),
    )

    # then
    assert pre_save_payloads == {}


@override_settings(ENABLE_LIMITING_WEBHOOKS_FOR_IDENTICAL_PAYLOADS=True)
def test_generate_pre_save_payloads(webhook_app, variant):
    # given
    webhook = Webhook.objects.create(
        name="Webhook",
        app=webhook_app,
        subscription_query=SUBSCRIPTION_QUERY,
    )
    event_type = WebhookEventAsyncType.PRODUCT_VARIANT_UPDATED
    webhook.events.create(event_type=event_type)

    # when
    pre_save_payloads = generate_pre_save_payloads(
        [webhook],
        [variant],
        WebhookEventAsyncType.PRODUCT_VARIANT_UPDATED,
        None,
        timezone.now(),
    )

    # then
    key = get_pre_save_payload_key(webhook, variant)
    assert key in pre_save_payloads
    assert pre_save_payloads[key]


def test_generate_payload_from_subscription(checkout, subscription_webhook, app):
    # given
    query = """
    subscription {
      event {
        ... on CalculateTaxes {
          taxBase {
            sourceObject {
              ... on Checkout {
                id
              }
            }
          }
        }
      }
    }
    """
    webhook = subscription_webhook(
        query,
        WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES,
    )
    app = webhook.app
    request = initialize_request(app=app)
    checkout_global_id = graphene.Node.to_global_id("Checkout", checkout.pk)

    # when
    payload = generate_payload_from_subscription(
        event_type=WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES,
        subscribable_object=checkout,
        subscription_query=webhook.subscription_query,
        request=request,
    )

    # then
    assert payload["taxBase"]["sourceObject"]["id"] == checkout_global_id


def test_generate_payload_from_subscription_missing_permissions(
    gift_card, subscription_gift_card_created_webhook, permission_manage_gift_card
):
    # given

    webhook = subscription_gift_card_created_webhook
    app = webhook.app
    app.permissions.remove(permission_manage_gift_card)
    request = initialize_request(app=app, requestor=app, sync_event=False)

    # when
    payload = generate_payload_from_subscription(
        event_type=WebhookEventAsyncType.GIFT_CARD_CREATED,
        subscribable_object=gift_card,
        subscription_query=webhook.subscription_query,
        request=request,
    )

    # then
    error_code = "PermissionDenied"
    assert "errors" in payload.keys()
    assert not payload["giftCard"]
    error = payload["errors"][0]
    assert error["extensions"]["exception"]["code"] == error_code


def test_generate_payload_from_subscription_circular_call(
    checkout, subscription_webhook, permission_handle_taxes
):
    # given
    query = """
    subscription {
      event {
        ... on CalculateTaxes {
          taxBase {
            sourceObject {
              ...on Checkout{
                totalPrice {
                  gross {
                    amount
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    webhook = subscription_webhook(
        query,
        WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES,
    )
    app = webhook.app
    request = initialize_request(app=app, requestor=app, sync_event=True)

    # when
    payload = generate_payload_from_subscription(
        event_type=WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES,
        subscribable_object=checkout,
        subscription_query=webhook.subscription_query,
        request=request,
    )
    # then
    error_code = "CircularSubscriptionSyncEvent"
    assert list(payload.keys()) == ["errors"]
    error = payload["errors"][0]
    assert (
        error["message"] == "Resolving this field is not allowed in synchronous events."
    )
    assert error["extensions"]["exception"]["code"] == error_code


def test_generate_payload_from_subscription_unable_to_build_payload(
    checkout, subscription_webhook
):
    # given
    query = """
    subscription {
      event {
        ... on OrderCalculateTaxes {
          taxBase {
            sourceObject {
              ...on Checkout{
                totalPrice {
                  gross {
                    amount
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    webhook = subscription_webhook(
        query,
        WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES,
    )
    app = webhook.app
    request = initialize_request(app=app, requestor=app, sync_event=True)

    # when
    payload = generate_payload_from_subscription(
        event_type=WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES,
        subscribable_object=checkout,
        subscription_query=webhook.subscription_query,
        request=request,
    )
    # then
    assert payload is None


def test_generate_payload_promise_from_subscription(
    checkout,
    subscription_webhook,
):
    # given
    query = """
    subscription {
      event {
        ... on CalculateTaxes {
          taxBase {
            sourceObject {
              ... on Checkout {
                id
              }
            }
          }
        }
      }
    }
    """
    webhook = subscription_webhook(
        query,
        WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES,
    )
    app = webhook.app
    request = initialize_request(app=app)
    checkout_global_id = graphene.Node.to_global_id("Checkout", checkout.pk)

    # when
    payload = generate_payload_promise_from_subscription(
        event_type=WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES,
        subscribable_object=checkout,
        subscription_query=webhook.subscription_query,
        request=request,
    )

    # then
    payload = payload.get()
    assert payload["taxBase"]["sourceObject"]["id"] == checkout_global_id


def test_generate_payload_promise_from_subscription_missing_permissions(
    gift_card, subscription_gift_card_created_webhook, permission_manage_gift_card
):
    # given

    webhook = subscription_gift_card_created_webhook
    app = webhook.app
    app.permissions.remove(permission_manage_gift_card)
    request = initialize_request(app=app, requestor=app, sync_event=False)

    # when
    payload = generate_payload_promise_from_subscription(
        event_type=WebhookEventAsyncType.GIFT_CARD_CREATED,
        subscribable_object=gift_card,
        subscription_query=webhook.subscription_query,
        request=request,
    )

    # then
    payload = payload.get()
    error_code = "PermissionDenied"
    assert "errors" in payload.keys()
    assert not payload["giftCard"]
    error = payload["errors"][0]
    assert error["extensions"]["exception"]["code"] == error_code


def test_generate_payload_promise_from_subscription_circular_call(
    checkout, subscription_webhook, permission_handle_taxes
):
    # given
    query = """
    subscription {
      event {
        ... on CalculateTaxes {
          taxBase {
            sourceObject {
              ...on Checkout{
                totalPrice {
                  gross {
                    amount
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    webhook = subscription_webhook(
        query,
        WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES,
    )
    app = webhook.app
    request = initialize_request(app=app, requestor=app, sync_event=True)

    # when
    payload = generate_payload_promise_from_subscription(
        event_type=WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES,
        subscribable_object=checkout,
        subscription_query=webhook.subscription_query,
        request=request,
    )
    # then
    payload = payload.get()
    error_code = "CircularSubscriptionSyncEvent"
    assert list(payload.keys()) == ["errors"]
    error = payload["errors"][0]
    assert (
        error["message"] == "Resolving this field is not allowed in synchronous events."
    )
    assert error["extensions"]["exception"]["code"] == error_code


def test_generate_payload_promise_from_subscription_unable_to_build_payload(
    checkout, subscription_webhook
):
    # given
    query = """
    subscription {
      event {
        ... on OrderCalculateTaxes {
          taxBase {
            sourceObject {
              ...on Checkout{
                totalPrice {
                  gross {
                    amount
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    webhook = subscription_webhook(
        query,
        WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES,
    )
    app = webhook.app
    request = initialize_request(app=app, requestor=app, sync_event=True)

    # when
    payload = generate_payload_promise_from_subscription(
        event_type=WebhookEventSyncType.CHECKOUT_CALCULATE_TAXES,
        subscribable_object=checkout,
        subscription_query=webhook.subscription_query,
        request=request,
    )
    # then
    payload = payload.get()
    assert payload is None


XERO_ORDER_CONFIRMED_QUERY = """
    subscription {
      event {
        ... on XeroOrderConfirmed {
          order { id xeroBankAccountCode }
          calculatedAmounts {
            depositAmount {
              amount
              currency
            }
          }
        }
      }
    }
"""


def test_xero_order_confirmed_calculated_amounts_serializes_as_decimal(
    order, app, webhook_app
):
    # given - order with a known gross total and deposit percentage
    order.total_gross_amount = Decimal("1000.00")
    order.deposit_percentage = Decimal(30)
    order.currency = "USD"
    order.xero_bank_account_code = "XERO-001"
    order.save(
        update_fields=[
            "total_gross_amount",
            "deposit_percentage",
            "currency",
            "xero_bank_account_code",
        ]
    )

    request = initialize_request(app=app)

    # when
    payload = generate_payload_from_subscription(
        event_type=WebhookEventSyncType.XERO_ORDER_CONFIRMED,
        subscribable_object=order,
        subscription_query=XERO_ORDER_CONFIRMED_QUERY,
        request=request,
    )

    # then - 30% of 1000 = 300, no errors, order field resolves
    assert "errors" not in payload
    assert payload["order"] is not None
    assert payload["order"]["id"] is not None
    assert payload["order"]["xeroBankAccountCode"] == "XERO-001"
    assert payload["calculatedAmounts"]["depositAmount"]["amount"] == 300.0
    assert payload["calculatedAmounts"]["depositAmount"]["currency"] == "USD"


XERO_FULFILLMENT_CREATED_QUERY = """
    subscription {
      event {
        ... on XeroFulfillmentCreated {
          fulfillment { id }
          calculatedAmounts {
            proformaAmount { amount currency }
            shippingCost { amount currency }
            shippingVatRate
          }
        }
      }
    }
"""


def test_xero_fulfillment_created_calculated_amounts_serializes_as_decimal(
    fulfillment, app
):
    # given
    order = fulfillment.order
    order.shipping_price_gross_amount = Decimal("120.00")
    order.shipping_price_net_amount = Decimal("100.00")
    order.currency = "USD"
    order.save(
        update_fields=[
            "shipping_price_gross_amount",
            "shipping_price_net_amount",
            "currency",
        ]
    )
    fulfillment.deposit_allocated_amount = Decimal("50.00")
    fulfillment.save(update_fields=["deposit_allocated_amount"])

    for line in fulfillment.lines.all():
        line.order_line.unit_price_gross_amount = Decimal("100.00")
        line.order_line.save(update_fields=["unit_price_gross_amount"])

    request = initialize_request(app=app)

    # when
    payload = generate_payload_from_subscription(
        event_type=WebhookEventSyncType.XERO_FULFILLMENT_CREATED,
        subscribable_object=fulfillment,
        subscription_query=XERO_FULFILLMENT_CREATED_QUERY,
        request=request,
    )

    # then - no errors, amounts are numeric and correct
    assert "errors" not in payload
    amounts = payload["calculatedAmounts"]
    assert amounts["shippingCost"]["amount"] == 120.0
    assert amounts["shippingCost"]["currency"] == "USD"
    assert amounts["shippingVatRate"] == "0.20"


FULFILLMENT_APPROVED_XERO_FIELDS_QUERY = """
    subscription {
      event {
        ... on FulfillmentApproved {
          fulfillment {
            privateMetadata { key value }
            depositAllocatedAmount { amount currency }
          }
          order {
            privateMetadata { key value }
            shippingPrice { gross { amount currency } }
            shippingTaxRate
          }
        }
      }
    }
"""


def test_fulfillment_approved_exposes_xero_fields(fulfillment, webhook_app):
    # given
    order = fulfillment.order
    order.shipping_price_gross_amount = Decimal("60.00")
    order.currency = "GBP"
    order.store_value_in_private_metadata({"xeroDepositPrepaymentId": "prepay-123"})
    order.save(
        update_fields=[
            "shipping_price_gross_amount",
            "currency",
            "private_metadata",
        ]
    )
    fulfillment.deposit_allocated_amount = Decimal("25.00")
    fulfillment.store_value_in_private_metadata(
        {"xeroProformaPrepaymentId": "proforma-456"}
    )
    fulfillment.save(update_fields=["deposit_allocated_amount", "private_metadata"])

    request = initialize_request(app=webhook_app)

    # when
    payload = generate_payload_promise_from_subscription(
        event_type=WebhookEventAsyncType.FULFILLMENT_APPROVED,
        subscribable_object={"fulfillment": fulfillment, "notify_customer": True},
        subscription_query=FULFILLMENT_APPROVED_XERO_FIELDS_QUERY,
        request=request,
    ).get()

    # then
    assert "errors" not in payload
    assert payload["fulfillment"] is not None
    assert payload["order"] is not None

    f = payload["fulfillment"]
    assert f["depositAllocatedAmount"]["amount"] == 25.0
    assert f["depositAllocatedAmount"]["currency"] == "GBP"
    assert any(m["key"] == "xeroProformaPrepaymentId" for m in f["privateMetadata"])

    o = payload["order"]
    assert o["shippingPrice"]["gross"]["amount"] == 60.0
    assert o["shippingPrice"]["gross"]["currency"] == "GBP"
    assert o["shippingTaxRate"] is not None
    assert any(m["key"] == "xeroDepositPrepaymentId" for m in o["privateMetadata"])
