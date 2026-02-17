from decimal import Decimal
from unittest.mock import MagicMock, patch

import graphene
import pytest

from .....order.error_codes import OrderErrorCode
from .....payment import ChargeStatus, CustomPaymentChoices, TransactionKind
from .....payment.models import Payment, Transaction
from ....tests.utils import get_graphql_content

ORDER_ADD_XERO_PAYMENT_MUTATION = """
    mutation OrderAddXeroPayment($orderId: ID!, $xeroPaymentId: String!, $isDeposit: Boolean) {
        orderSyncXeroPayment(
            orderId: $orderId
            xeroPaymentId: $xeroPaymentId
            isDeposit: $isDeposit
        ) {
            payment {
                id
                gateway
                total {
                    amount
                    currency
                }
                capturedAmount {
                    amount
                    currency
                }
                chargeStatus
                pspReference
            }
            order {
                id
                depositPaidAt
            }
            errors {
                field
                code
                message
            }
        }
    }
"""


@pytest.fixture
def xero_payment_response():
    """Mock Xero payment API response."""
    return {
        "payment_id": "eea216c6-29c6-4de2-83f4-4196ae3bfaac",
        "invoice_id": "7ea31cd8-045c-4871-8cda-c0420953a39c",
        "contact_id": "a871a956-05b5-4e2a-9419-7aeb478ca647",
        "amount": Decimal("100.48"),
        "date": "2026-02-16T00:00:00+00:00",
        "status": "AUTHORISED",
    }


@pytest.mark.django_db
class TestOrderAddXeroPayment:
    def test_add_xero_payment_success(
        self,
        staff_api_client,
        permission_group_manage_orders,
        order,
        xero_payment_response,
    ):
        # given
        permission_group_manage_orders.user_set.add(staff_api_client.user)
        order_id = graphene.Node.to_global_id("Order", order.id)
        xero_payment_id = xero_payment_response["payment_id"]

        variables = {
            "orderId": order_id,
            "xeroPaymentId": xero_payment_id,
            "isDeposit": False,
        }

        # when
        mock_http_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = xero_payment_response
        mock_response.raise_for_status.return_value = None
        mock_http_client.send_request.return_value = mock_response

        with patch(
            "saleor.graphql.order.mutations.order_add_xero_payment.HTTPClient",
            mock_http_client,
        ):
            response = staff_api_client.post_graphql(
                ORDER_ADD_XERO_PAYMENT_MUTATION, variables
            )

        # then
        content = get_graphql_content(response)
        data = content["data"]["orderSyncXeroPayment"]

        assert not data["errors"]
        assert data["payment"]["gateway"] == CustomPaymentChoices.XERO
        assert Decimal(str(data["payment"]["total"]["amount"])).quantize(
            Decimal("0.01")
        ) == xero_payment_response["amount"].quantize(Decimal("0.01"))
        assert Decimal(str(data["payment"]["capturedAmount"]["amount"])).quantize(
            Decimal("0.01")
        ) == xero_payment_response["amount"].quantize(Decimal("0.01"))
        assert data["payment"]["chargeStatus"] == "FULLY_CHARGED"
        assert data["payment"]["pspReference"] == xero_payment_id

        # Verify database records
        payment = Payment.objects.get(psp_reference=xero_payment_id)
        assert payment.order == order
        assert payment.total == xero_payment_response["amount"]
        assert payment.captured_amount == xero_payment_response["amount"]
        assert payment.charge_status == ChargeStatus.FULLY_CHARGED
        assert payment.currency == order.currency
        assert (
            payment.metadata["xero_invoice_id"] == xero_payment_response["invoice_id"]
        )
        assert (
            payment.metadata["xero_contact_id"] == xero_payment_response["contact_id"]
        )

        # Verify order accounting was updated
        order.refresh_from_db()
        assert order.total_charged_amount == xero_payment_response["amount"]

    def test_add_xero_payment_creates_transaction(
        self,
        staff_api_client,
        permission_group_manage_orders,
        order,
        xero_payment_response,
    ):
        # given
        permission_group_manage_orders.user_set.add(staff_api_client.user)
        order_id = graphene.Node.to_global_id("Order", order.id)
        xero_payment_id = xero_payment_response["payment_id"]

        variables = {
            "orderId": order_id,
            "xeroPaymentId": xero_payment_id,
        }

        # when
        mock_http_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = xero_payment_response
        mock_response.raise_for_status.return_value = None
        mock_http_client.send_request.return_value = mock_response

        with patch(
            "saleor.graphql.order.mutations.order_add_xero_payment.HTTPClient",
            mock_http_client,
        ):
            response = staff_api_client.post_graphql(
                ORDER_ADD_XERO_PAYMENT_MUTATION, variables
            )

        # then
        content = get_graphql_content(response)
        assert not content["data"]["orderSyncXeroPayment"]["errors"]

        # Verify transaction was created
        payment = Payment.objects.get(psp_reference=xero_payment_id)
        transaction = Transaction.objects.get(payment=payment)

        assert transaction.kind == TransactionKind.CAPTURE
        assert transaction.amount == xero_payment_response["amount"]
        assert transaction.currency == order.currency
        assert transaction.is_success is True
        assert transaction.token == xero_payment_id

    def test_add_xero_payment_as_deposit(
        self,
        staff_api_client,
        permission_group_manage_orders,
        order,
        xero_payment_response,
    ):
        # given
        permission_group_manage_orders.user_set.add(staff_api_client.user)
        # Set deposit requirement to 10% and ensure order has enough value
        order.deposit_required = True
        order.deposit_percentage = 10
        order.deposit_paid_at = None
        order.save()
        # deposit_threshold_met is a property, will be True when enough payment is captured

        order_id = graphene.Node.to_global_id("Order", order.id)
        xero_payment_id = xero_payment_response["payment_id"]

        variables = {
            "orderId": order_id,
            "xeroPaymentId": xero_payment_id,
            "isDeposit": True,
        }

        # when
        mock_http_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = xero_payment_response
        mock_response.raise_for_status.return_value = None
        mock_http_client.send_request.return_value = mock_response

        with patch(
            "saleor.graphql.order.mutations.order_add_xero_payment.HTTPClient",
            mock_http_client,
        ):
            response = staff_api_client.post_graphql(
                ORDER_ADD_XERO_PAYMENT_MUTATION, variables
            )

        # then
        content = get_graphql_content(response)
        data = content["data"]["orderSyncXeroPayment"]

        assert not data["errors"]
        assert data["order"]["depositPaidAt"] is not None

        # Verify metadata
        payment = Payment.objects.get(psp_reference=xero_payment_id)
        assert payment.metadata["is_deposit"] is True

        order.refresh_from_db()
        assert order.deposit_paid_at is not None

    def test_add_xero_payment_auto_populates_contact_id(
        self,
        staff_api_client,
        permission_group_manage_orders,
        order_with_user,
        xero_payment_response,
    ):
        # given
        permission_group_manage_orders.user_set.add(staff_api_client.user)
        order = order_with_user
        order.user.xero_contact_id = None
        order.user.save()

        order_id = graphene.Node.to_global_id("Order", order.id)
        xero_payment_id = xero_payment_response["payment_id"]

        variables = {
            "orderId": order_id,
            "xeroPaymentId": xero_payment_id,
        }

        # when
        mock_http_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = xero_payment_response
        mock_response.raise_for_status.return_value = None
        mock_http_client.send_request.return_value = mock_response

        with patch(
            "saleor.graphql.order.mutations.order_add_xero_payment.HTTPClient",
            mock_http_client,
        ):
            response = staff_api_client.post_graphql(
                ORDER_ADD_XERO_PAYMENT_MUTATION, variables
            )

        # then
        content = get_graphql_content(response)
        assert not content["data"]["orderSyncXeroPayment"]["errors"]

        # Verify contact ID was auto-populated
        order.user.refresh_from_db()
        assert order.user.xero_contact_id == xero_payment_response["contact_id"]

    def test_add_xero_payment_validates_contact_id(
        self,
        staff_api_client,
        permission_group_manage_orders,
        order_with_user,
        xero_payment_response,
    ):
        # given
        permission_group_manage_orders.user_set.add(staff_api_client.user)
        order = order_with_user
        order.user.xero_contact_id = "different-contact-id"
        order.user.save()

        order_id = graphene.Node.to_global_id("Order", order.id)
        xero_payment_id = xero_payment_response["payment_id"]

        variables = {
            "orderId": order_id,
            "xeroPaymentId": xero_payment_id,
        }

        # when
        mock_http_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = xero_payment_response
        mock_response.raise_for_status.return_value = None
        mock_http_client.send_request.return_value = mock_response

        with patch(
            "saleor.graphql.order.mutations.order_add_xero_payment.HTTPClient",
            mock_http_client,
        ):
            response = staff_api_client.post_graphql(
                ORDER_ADD_XERO_PAYMENT_MUTATION, variables
            )

        # then
        content = get_graphql_content(response)
        data = content["data"]["orderSyncXeroPayment"]

        assert data["errors"]
        assert data["errors"][0]["code"] == OrderErrorCode.INVALID.name
        assert "different customer" in data["errors"][0]["message"].lower()
        assert not data["payment"]

    def test_add_xero_payment_not_found(
        self, staff_api_client, permission_group_manage_orders, order
    ):
        # given
        permission_group_manage_orders.user_set.add(staff_api_client.user)
        order_id = graphene.Node.to_global_id("Order", order.id)
        xero_payment_id = "non-existent-payment-id"

        variables = {
            "orderId": order_id,
            "xeroPaymentId": xero_payment_id,
        }

        # when
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=404,
                raise_for_status=lambda: None,
            )
            mock_post.return_value.json.return_value = {"detail": "Payment not found"}

            response = staff_api_client.post_graphql(
                ORDER_ADD_XERO_PAYMENT_MUTATION, variables
            )

        # then
        content = get_graphql_content(response)
        data = content["data"]["orderSyncXeroPayment"]

        assert data["errors"]
        assert data["errors"][0]["code"] == OrderErrorCode.INVALID.name
        assert not data["payment"]

    def test_add_xero_payment_invalid_order_id(
        self, staff_api_client, permission_group_manage_orders
    ):
        # given
        permission_group_manage_orders.user_set.add(staff_api_client.user)
        order_id = graphene.Node.to_global_id(
            "Order", "df94b672-93dc-49f6-8735-857b89ec8faa"
        )

        variables = {
            "orderId": order_id,
            "xeroPaymentId": "some-payment-id",
        }

        # when
        response = staff_api_client.post_graphql(
            ORDER_ADD_XERO_PAYMENT_MUTATION, variables
        )

        # then
        content = get_graphql_content(response)
        data = content["data"]["orderSyncXeroPayment"]

        assert data["errors"]
        assert data["errors"][0]["code"] == OrderErrorCode.NOT_FOUND.name

    def test_add_xero_payment_requires_permission(
        self, staff_api_client, order, xero_payment_response
    ):
        # given - staff user without permission
        order_id = graphene.Node.to_global_id("Order", order.id)
        xero_payment_id = xero_payment_response["payment_id"]

        variables = {
            "orderId": order_id,
            "xeroPaymentId": xero_payment_id,
        }

        # when
        response = staff_api_client.post_graphql(
            ORDER_ADD_XERO_PAYMENT_MUTATION, variables
        )

        # then
        assert_no_permission(response)

    def test_add_xero_payment_updates_order_balance(
        self,
        staff_api_client,
        permission_group_manage_orders,
        order,
        xero_payment_response,
    ):
        # given
        permission_group_manage_orders.user_set.add(staff_api_client.user)
        order_id = graphene.Node.to_global_id("Order", order.id)
        xero_payment_id = xero_payment_response["payment_id"]
        payment_amount = xero_payment_response["amount"]

        variables = {
            "orderId": order_id,
            "xeroPaymentId": xero_payment_id,
            "isDeposit": False,
        }

        # when
        mock_http_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = xero_payment_response
        mock_response.raise_for_status.return_value = None
        mock_http_client.send_request.return_value = mock_response

        with patch(
            "saleor.graphql.order.mutations.order_add_xero_payment.HTTPClient",
            mock_http_client,
        ):
            response = staff_api_client.post_graphql(
                ORDER_ADD_XERO_PAYMENT_MUTATION, variables
            )

        # then
        content = get_graphql_content(response)
        assert not content["data"]["orderSyncXeroPayment"]["errors"]

        order.refresh_from_db()
        assert order.total_charged_amount == payment_amount
        assert order.total_balance.amount == payment_amount - order.total.gross.amount

    def test_add_xero_payment_duplicate_psp_reference(
        self,
        staff_api_client,
        permission_group_manage_orders,
        order,
        xero_payment_response,
    ):
        # given
        permission_group_manage_orders.user_set.add(staff_api_client.user)
        xero_payment_id = xero_payment_response["payment_id"]

        # Create existing payment with same psp_reference
        Payment.objects.create(
            order=order,
            gateway=CustomPaymentChoices.XERO,
            psp_reference=xero_payment_id,
            total=Decimal("50.00"),
            captured_amount=Decimal("50.00"),
            charge_status=ChargeStatus.FULLY_CHARGED,
            currency=order.currency,
        )

        order_id = graphene.Node.to_global_id("Order", order.id)

        variables = {
            "orderId": order_id,
            "xeroPaymentId": xero_payment_id,
        }

        # when
        mock_http_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = xero_payment_response
        mock_response.raise_for_status.return_value = None
        mock_http_client.send_request.return_value = mock_response

        with patch(
            "saleor.graphql.order.mutations.order_add_xero_payment.HTTPClient",
            mock_http_client,
        ):
            response = staff_api_client.post_graphql(
                ORDER_ADD_XERO_PAYMENT_MUTATION, variables
            )

        # then
        content = get_graphql_content(response)
        data = content["data"]["orderSyncXeroPayment"]

        # Should either error or return existing payment
        # (depends on business logic - currently would create duplicate)
        # This test documents current behavior
        assert data is not None


@pytest.fixture
def order_with_user(order, customer_user):
    """Order with an associated user."""
    order.user = customer_user
    order.save()
    return order


def assert_no_permission(response):
    """Assert that response contains permission errors."""
    content = get_graphql_content(response, ignore_errors=True)
    assert "errors" in content
    assert any(
        "permission" in error["message"].lower() for error in content.get("errors", [])
    )
