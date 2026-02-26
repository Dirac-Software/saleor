from unittest.mock import MagicMock, patch

import pytest

from ....invoice import InvoiceType
from ....invoice.models import Invoice
from ....order import OrderOrigin
from ....plugins.manager import get_plugins_manager
from ....webhook.event_types import WebhookEventSyncType


@pytest.mark.django_db
@patch("saleor.plugins.webhook.plugin.get_webhooks_for_event")
@pytest.mark.parametrize(
    ("method", "event_type"),
    [
        ("xero_order_confirmed", WebhookEventSyncType.XERO_ORDER_CONFIRMED),
        ("xero_fulfillment_created", WebhookEventSyncType.XERO_FULFILLMENT_CREATED),
        ("xero_fulfillment_approved", WebhookEventSyncType.XERO_FULFILLMENT_APPROVED),
    ],
)
def test_xero_sync_webhooks_skip_checkout_orders(
    mock_get_webhooks, method, event_type, order, fulfillment, settings
):
    settings.PLUGINS = ["saleor.plugins.webhook.plugin.WebhookPlugin"]
    manager = get_plugins_manager(allow_replica=False)

    # given - order origin is CHECKOUT (paid via Stripe, no Xero prepayment needed)
    order.origin = OrderOrigin.CHECKOUT
    order.save(update_fields=["origin"])
    fulfillment.order = order

    # when
    if method == "xero_order_confirmed":
        manager.xero_order_confirmed(order)
    elif method == "xero_fulfillment_created":
        manager.xero_fulfillment_created(fulfillment)
    else:
        manager.xero_fulfillment_approved(fulfillment)

    # then - get_webhooks_for_event never called; Xero sync skipped entirely
    mock_get_webhooks.assert_not_called()


@pytest.mark.django_db
@patch("saleor.plugins.webhook.plugin.get_webhooks_for_event", return_value=[])
def test_xero_order_confirmed_proceeds_for_draft_order(
    mock_get_webhooks, order, settings
):
    settings.PLUGINS = ["saleor.plugins.webhook.plugin.WebhookPlugin"]
    manager = get_plugins_manager(allow_replica=False)

    # given - order created from draft (not checkout)
    order.origin = OrderOrigin.DRAFT
    order.save(update_fields=["origin"])

    # when
    manager.xero_order_confirmed(order)

    # then - webhook lookup was attempted (no webhooks registered, but the guard passed)
    mock_get_webhooks.assert_called_once_with(WebhookEventSyncType.XERO_ORDER_CONFIRMED)


@pytest.mark.django_db
@patch("saleor.plugins.webhook.plugin.get_webhooks_for_event", return_value=[])
def test_xero_fulfillment_created_proceeds_for_draft_order(
    mock_get_webhooks, fulfillment, settings
):
    settings.PLUGINS = ["saleor.plugins.webhook.plugin.WebhookPlugin"]
    manager = get_plugins_manager(allow_replica=False)

    # given - fulfillment whose order was created from draft
    order = fulfillment.order
    order.origin = OrderOrigin.DRAFT
    order.save(update_fields=["origin"])

    # when
    manager.xero_fulfillment_created(fulfillment)

    # then - webhook lookup was attempted
    mock_get_webhooks.assert_called_once_with(
        WebhookEventSyncType.XERO_FULFILLMENT_CREATED
    )


@pytest.mark.django_db
@patch("saleor.plugins.webhook.plugin.trigger_webhook_sync")
@patch("saleor.plugins.webhook.plugin.get_webhooks_for_event")
def test_xero_list_bank_accounts_calls_webhook_and_returns_accounts(
    mock_get_webhooks, mock_trigger, settings
):
    settings.PLUGINS = ["saleor.plugins.webhook.plugin.WebhookPlugin"]
    manager = get_plugins_manager(allow_replica=False)

    # given
    fake_webhook = MagicMock()
    mock_get_webhooks.return_value = [fake_webhook]
    mock_trigger.return_value = {
        "bank_accounts": [
            {
                "code": "ACC001",
                "name": "Main",
                "sort_code": "123456",
                "account_number": "12345678",
            }
        ]
    }

    # when
    result = manager.xero_list_bank_accounts(domain="default-channel")

    # then
    mock_get_webhooks.assert_called_once_with(
        WebhookEventSyncType.XERO_LIST_BANK_ACCOUNTS
    )
    mock_trigger.assert_called_once()
    call_kwargs = mock_trigger.call_args.kwargs
    assert call_kwargs["event_type"] == WebhookEventSyncType.XERO_LIST_BANK_ACCOUNTS
    assert call_kwargs["pregenerated_subscription_payload"] == {
        "domain": "default-channel"
    }
    assert result == [
        {
            "code": "ACC001",
            "name": "Main",
            "sort_code": "123456",
            "account_number": "12345678",
        }
    ]


@pytest.mark.django_db
@patch("saleor.plugins.webhook.plugin.get_webhooks_for_event", return_value=[])
def test_xero_list_bank_accounts_no_webhooks_returns_empty(mock_get_webhooks, settings):
    settings.PLUGINS = ["saleor.plugins.webhook.plugin.WebhookPlugin"]
    manager = get_plugins_manager(allow_replica=False)

    # when
    result = manager.xero_list_bank_accounts(domain="default-channel")

    # then
    assert result == []


@pytest.mark.django_db
@patch("saleor.plugins.webhook.plugin.trigger_webhook_sync")
@patch("saleor.plugins.webhook.plugin.get_webhooks_for_event")
def test_xero_list_tax_codes_calls_webhook_and_returns_codes(
    mock_get_webhooks, mock_trigger, settings
):
    settings.PLUGINS = ["saleor.plugins.webhook.plugin.WebhookPlugin"]
    manager = get_plugins_manager(allow_replica=False)

    # given
    fake_webhook = MagicMock()
    mock_get_webhooks.return_value = [fake_webhook]
    mock_trigger.return_value = {
        "tax_codes": [
            {"code": "OUTPUT2", "name": "20% (VAT on Income)", "rate": 0.2},
            {"code": "ZERORATEDINPUT", "name": "Zero Rated", "rate": 0.0},
        ]
    }

    # when
    result = manager.xero_list_tax_codes(domain="default-channel")

    # then
    mock_get_webhooks.assert_called_once_with(WebhookEventSyncType.XERO_LIST_TAX_CODES)
    mock_trigger.assert_called_once()
    call_kwargs = mock_trigger.call_args.kwargs
    assert call_kwargs["event_type"] == WebhookEventSyncType.XERO_LIST_TAX_CODES
    assert call_kwargs["pregenerated_subscription_payload"] == {
        "domain": "default-channel"
    }
    assert len(result) == 2
    assert result[0]["code"] == "OUTPUT2"


@pytest.mark.django_db
@patch("saleor.plugins.webhook.plugin.get_webhooks_for_event", return_value=[])
def test_xero_list_tax_codes_no_webhooks_returns_empty(mock_get_webhooks, settings):
    settings.PLUGINS = ["saleor.plugins.webhook.plugin.WebhookPlugin"]
    manager = get_plugins_manager(allow_replica=False)

    # when
    result = manager.xero_list_tax_codes(domain="default-channel")

    # then
    assert result == []


@pytest.mark.django_db
@patch("saleor.plugins.webhook.plugin.get_webhooks_for_event", return_value=[])
def test_xero_fulfillment_approved_proceeds_for_draft_order(
    mock_get_webhooks, fulfillment, settings
):
    settings.PLUGINS = ["saleor.plugins.webhook.plugin.WebhookPlugin"]
    manager = get_plugins_manager(allow_replica=False)

    # given
    order = fulfillment.order
    order.origin = OrderOrigin.DRAFT
    order.save(update_fields=["origin"])

    # when
    manager.xero_fulfillment_approved(fulfillment)

    # then - webhook lookup was attempted
    mock_get_webhooks.assert_called_once_with(
        WebhookEventSyncType.XERO_FULFILLMENT_APPROVED
    )


@pytest.mark.django_db
@patch("saleor.plugins.webhook.plugin.trigger_webhook_sync")
@patch("saleor.plugins.webhook.plugin.get_webhooks_for_event")
def test_xero_fulfillment_approved_creates_final_invoice(
    mock_get_webhooks, mock_trigger, fulfillment, settings
):
    settings.PLUGINS = ["saleor.plugins.webhook.plugin.WebhookPlugin"]
    manager = get_plugins_manager(allow_replica=False)

    # given
    order = fulfillment.order
    order.origin = OrderOrigin.DRAFT
    order.save(update_fields=["origin"])

    fake_webhook = MagicMock()
    mock_get_webhooks.return_value = [fake_webhook]
    mock_trigger.return_value = {
        "invoicePdfUrl": "https://example.com/invoice.pdf",
        "xeroInvoiceId": "XERO-INV-001",
        "xeroInvoiceNumber": "INV-0042",
    }

    # when
    manager.xero_fulfillment_approved(fulfillment)

    # then - FINAL invoice created with returned data
    invoice = Invoice.objects.get(fulfillment=fulfillment, type=InvoiceType.FINAL)
    assert invoice.external_url == "https://example.com/invoice.pdf"
    assert invoice.xero_invoice_id == "XERO-INV-001"
    assert invoice.number == "INV-0042"
    assert invoice.order == order


@pytest.mark.django_db
@patch("saleor.plugins.webhook.plugin.trigger_webhook_sync")
@patch("saleor.plugins.webhook.plugin.get_webhooks_for_event")
def test_xero_fulfillment_approved_idempotent_on_repeat_call(
    mock_get_webhooks, mock_trigger, fulfillment, settings
):
    settings.PLUGINS = ["saleor.plugins.webhook.plugin.WebhookPlugin"]
    manager = get_plugins_manager(allow_replica=False)

    # given
    order = fulfillment.order
    order.origin = OrderOrigin.DRAFT
    order.save(update_fields=["origin"])

    fake_webhook = MagicMock()
    mock_get_webhooks.return_value = [fake_webhook]
    mock_trigger.return_value = {
        "invoicePdfUrl": "https://example.com/invoice-v2.pdf",
        "xeroInvoiceId": "XERO-INV-001",
        "xeroInvoiceNumber": "INV-0042",
    }

    # pre-create the FINAL invoice (simulates prior approval)
    Invoice.objects.create(
        fulfillment=fulfillment,
        order=order,
        type=InvoiceType.FINAL,
        external_url="https://example.com/invoice-v1.pdf",
        xero_invoice_id="XERO-INV-001",
        number="INV-0042",
    )

    # when - second approval call (idempotent update)
    manager.xero_fulfillment_approved(fulfillment)

    # then - still only one FINAL invoice, URL updated
    assert (
        Invoice.objects.filter(fulfillment=fulfillment, type=InvoiceType.FINAL).count()
        == 1
    )
    invoice = Invoice.objects.get(fulfillment=fulfillment, type=InvoiceType.FINAL)
    assert invoice.external_url == "https://example.com/invoice-v2.pdf"


@pytest.mark.django_db
@patch("saleor.plugins.webhook.plugin.trigger_webhook_sync")
@patch("saleor.plugins.webhook.plugin.get_webhooks_for_event")
def test_xero_fulfillment_approved_handles_none_response(
    mock_get_webhooks, mock_trigger, fulfillment, settings
):
    settings.PLUGINS = ["saleor.plugins.webhook.plugin.WebhookPlugin"]
    manager = get_plugins_manager(allow_replica=False)

    # given
    order = fulfillment.order
    order.origin = OrderOrigin.DRAFT
    order.save(update_fields=["origin"])

    fake_webhook = MagicMock()
    mock_get_webhooks.return_value = [fake_webhook]
    mock_trigger.return_value = None

    # when
    manager.xero_fulfillment_approved(fulfillment)

    # then - no invoice created when webhook returns None
    assert not Invoice.objects.filter(
        fulfillment=fulfillment, type=InvoiceType.FINAL
    ).exists()


@pytest.mark.django_db
@patch("saleor.plugins.webhook.plugin.trigger_webhook_sync")
@patch("saleor.plugins.webhook.plugin.get_webhooks_for_event")
def test_xero_fulfillment_created_stores_quote_pdf_on_proforma_invoice(
    mock_get_webhooks, mock_trigger, fulfillment, settings
):
    settings.PLUGINS = ["saleor.plugins.webhook.plugin.WebhookPlugin"]
    manager = get_plugins_manager(allow_replica=False)

    # given
    order = fulfillment.order
    order.origin = OrderOrigin.DRAFT
    order.save(update_fields=["origin"])

    proforma_inv = Invoice.objects.create(
        fulfillment=fulfillment,
        order=order,
        type=InvoiceType.PROFORMA,
        number="Q-001",
    )

    fake_webhook = MagicMock()
    mock_get_webhooks.return_value = [fake_webhook]
    mock_trigger.return_value = {
        "xeroQuoteId": "QUOTE-123",
        "xeroQuoteNumber": "Q-001",
        "quotePdfUrl": "https://example.com/quote.pdf",
    }

    # when
    manager.xero_fulfillment_created(fulfillment)

    # then - proforma invoice external_url updated with PDF link
    proforma_inv.refresh_from_db()
    assert proforma_inv.external_url == "https://example.com/quote.pdf"
