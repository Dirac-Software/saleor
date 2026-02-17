from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from ...payment import ChargeStatus, CustomPaymentChoices, TransactionKind
from ...payment.models import Payment, Transaction
from ..utils import record_external_payment


@pytest.mark.django_db
class TestRecordExternalPayment:
    def test_creates_payment_with_correct_attributes(self, order):
        amount = Decimal("100.00")
        psp_ref = "test-payment-123"
        metadata = {"key": "value"}

        payment = record_external_payment(
            order=order,
            amount=amount,
            gateway=CustomPaymentChoices.MANUAL,
            psp_reference=psp_ref,
            transaction_kind=TransactionKind.EXTERNAL,
            metadata=metadata,
        )

        assert payment.order == order
        assert payment.gateway == CustomPaymentChoices.MANUAL
        assert payment.psp_reference == psp_ref
        assert payment.total == amount
        assert payment.captured_amount == amount
        assert payment.charge_status == ChargeStatus.FULLY_CHARGED
        assert payment.currency == order.currency
        assert payment.is_active is True
        assert payment.billing_email == order.user_email
        assert payment.metadata == metadata

    def test_creates_transaction_record(self, order):
        amount = Decimal("50.00")
        psp_ref = "txn-456"

        payment = record_external_payment(
            order=order,
            amount=amount,
            gateway=CustomPaymentChoices.XERO,
            psp_reference=psp_ref,
            transaction_kind=TransactionKind.CAPTURE,
        )

        transaction = Transaction.objects.get(payment=payment)
        assert transaction.kind == TransactionKind.CAPTURE
        assert transaction.amount == amount
        assert transaction.currency == order.currency
        assert transaction.is_success is True
        assert transaction.token == psp_ref

    def test_updates_order_total_charged(self, order):
        amount = Decimal("75.00")
        initial_charged = order.total_charged_amount

        record_external_payment(
            order=order,
            amount=amount,
            gateway=CustomPaymentChoices.MANUAL,
            transaction_kind=TransactionKind.EXTERNAL,
        )

        order.refresh_from_db()
        assert order.total_charged_amount == initial_charged + amount

    def test_updates_order_charge_status_to_full(self, order):
        record_external_payment(
            order=order,
            amount=order.total.gross.amount,
            gateway=CustomPaymentChoices.MANUAL,
            transaction_kind=TransactionKind.EXTERNAL,
        )

        order.refresh_from_db()
        assert order.is_fully_paid()

    def test_updates_order_charge_status_to_partial(self, order_with_lines):
        order = order_with_lines
        order.total_charged_amount = Decimal("0")
        order.save()

        assert order.total.gross.amount > Decimal("0"), "Order must have non-zero total"
        partial_amount = order.total.gross.amount / 2

        record_external_payment(
            order=order,
            amount=partial_amount,
            gateway=CustomPaymentChoices.MANUAL,
            transaction_kind=TransactionKind.EXTERNAL,
        )

        order.refresh_from_db()
        assert order.total_charged_amount == partial_amount
        assert order.total_charged < order.total.gross

    def test_handles_empty_psp_reference(self, order):
        payment = record_external_payment(
            order=order,
            amount=Decimal("10.00"),
            gateway=CustomPaymentChoices.MANUAL,
            psp_reference=None,
            transaction_kind=TransactionKind.EXTERNAL,
        )

        assert payment.psp_reference == ""
        transaction = Transaction.objects.get(payment=payment)
        assert transaction.token == ""

    def test_handles_empty_metadata(self, order):
        payment = record_external_payment(
            order=order,
            amount=Decimal("10.00"),
            gateway=CustomPaymentChoices.MANUAL,
            transaction_kind=TransactionKind.EXTERNAL,
            metadata=None,
        )

        assert payment.metadata == {}

    def test_creates_order_event_when_user_provided(self, order, staff_user):
        psp_ref = "event-test-789"

        with patch("saleor.order.utils.events.order_manually_marked_as_paid_event") as mock_event:
            record_external_payment(
                order=order,
                amount=Decimal("100.00"),
                gateway=CustomPaymentChoices.MANUAL,
                psp_reference=psp_ref,
                transaction_kind=TransactionKind.EXTERNAL,
                user=staff_user,
            )

            mock_event.assert_called_once_with(
                order=order,
                user=staff_user,
                app=None,
                transaction_reference=psp_ref,
            )

    def test_creates_order_event_when_app_provided(self, order, app):
        psp_ref = "app-test-101"

        with patch("saleor.order.utils.events.order_manually_marked_as_paid_event") as mock_event:
            record_external_payment(
                order=order,
                amount=Decimal("100.00"),
                gateway=CustomPaymentChoices.MANUAL,
                psp_reference=psp_ref,
                transaction_kind=TransactionKind.EXTERNAL,
                app=app,
            )

            mock_event.assert_called_once_with(
                order=order,
                user=None,
                app=app,
                transaction_reference=psp_ref,
            )

    def test_does_not_create_event_without_user_or_app(self, order):
        with patch("saleor.order.utils.events.order_manually_marked_as_paid_event") as mock_event:
            record_external_payment(
                order=order,
                amount=Decimal("100.00"),
                gateway=CustomPaymentChoices.MANUAL,
                transaction_kind=TransactionKind.EXTERNAL,
            )

            mock_event.assert_not_called()

    def test_triggers_webhooks_when_fully_paid(self, order, plugins_manager):
        with patch("saleor.order.actions.call_order_events") as mock_webhooks:
            record_external_payment(
                order=order,
                amount=order.total.gross.amount,
                gateway=CustomPaymentChoices.MANUAL,
                transaction_kind=TransactionKind.EXTERNAL,
                manager=plugins_manager,
            )

            order.refresh_from_db()
            assert order.is_fully_paid()
            mock_webhooks.assert_called_once()

    def test_does_not_trigger_webhooks_when_partially_paid(self, order_with_lines, plugins_manager):
        order = order_with_lines
        order.total_charged_amount = Decimal("0")
        order.save()

        assert order.total.gross.amount > Decimal("0"), "Order must have non-zero total"
        partial_amount = order.total.gross.amount / 2

        with patch("saleor.order.actions.call_order_events") as mock_webhooks:
            record_external_payment(
                order=order,
                amount=partial_amount,
                gateway=CustomPaymentChoices.MANUAL,
                transaction_kind=TransactionKind.EXTERNAL,
                manager=plugins_manager,
            )

            order.refresh_from_db()
            assert order.total_charged < order.total.gross
            mock_webhooks.assert_not_called()

    def test_does_not_trigger_webhooks_without_manager(self, order):
        with patch("saleor.order.actions.call_order_events") as mock_webhooks:
            record_external_payment(
                order=order,
                amount=order.total.gross.amount,
                gateway=CustomPaymentChoices.MANUAL,
                transaction_kind=TransactionKind.EXTERNAL,
                manager=None,
            )

            mock_webhooks.assert_not_called()

    def test_multiple_payments_accumulate_total_charged(self, order):
        first_amount = Decimal("50.00")
        second_amount = Decimal("30.00")

        record_external_payment(
            order=order,
            amount=first_amount,
            gateway=CustomPaymentChoices.MANUAL,
            transaction_kind=TransactionKind.EXTERNAL,
        )

        record_external_payment(
            order=order,
            amount=second_amount,
            gateway=CustomPaymentChoices.MANUAL,
            transaction_kind=TransactionKind.EXTERNAL,
        )

        order.refresh_from_db()
        assert order.total_charged_amount == first_amount + second_amount

    def test_supports_xero_gateway(self, order):
        payment = record_external_payment(
            order=order,
            amount=Decimal("100.00"),
            gateway=CustomPaymentChoices.XERO,
            psp_reference="xero-123",
            transaction_kind=TransactionKind.CAPTURE,
            metadata={"xero_invoice_id": "inv-456"},
        )

        assert payment.gateway == CustomPaymentChoices.XERO
        assert payment.metadata["xero_invoice_id"] == "inv-456"
        transaction = Transaction.objects.get(payment=payment)
        assert transaction.kind == TransactionKind.CAPTURE

    def test_returns_created_payment(self, order):
        result = record_external_payment(
            order=order,
            amount=Decimal("100.00"),
            gateway=CustomPaymentChoices.MANUAL,
            transaction_kind=TransactionKind.EXTERNAL,
        )

        assert isinstance(result, Payment)
        assert result.id is not None
        assert Payment.objects.filter(id=result.id).exists()
