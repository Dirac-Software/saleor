from decimal import Decimal
from unittest.mock import patch

import graphene

from .....graphql.tests.utils import get_graphql_content
from .....order.error_codes import OrderErrorCode

AVAILABLE_XERO_PAYMENTS_QUERY = """
    query AvailableXeroPayments($orderId: ID!) {
        availableXeroPayments(orderId: $orderId) {
            payments {
                paymentId
                amount
                date
                invoiceNumber
                status
            }
            errors {
                field
                code
                message
            }
        }
    }
"""


def test_available_xero_payments(
    staff_api_client, permission_group_manage_orders, order_with_lines, customer_user
):
    """Test fetching available Xero payments for an order's customer."""
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.user = customer_user
    order.user.xero_contact_id = "XERO-CONTACT-123"
    order.user.save()
    order.save()
    order_id = graphene.Node.to_global_id("Order", order.id)

    mock_payments = [
        {
            "payment_id": "PAY-001",
            "amount": Decimal("100.00"),
            "date": "2024-01-15T10:00:00",
            "invoice_number": "INV-001",
            "status": "AUTHORISED",
        },
        {
            "payment_id": "PAY-002",
            "amount": Decimal("200.00"),
            "date": "2024-01-14T10:00:00",
            "invoice_number": "INV-002",
            "status": "AUTHORISED",
        },
    ]

    with patch(
        "saleor.graphql.order.queries.xero_payments.get_xero_contact_payments",
        return_value=mock_payments,
    ):
        response = staff_api_client.post_graphql(
            AVAILABLE_XERO_PAYMENTS_QUERY, {"orderId": order_id}
        )

    content = get_graphql_content(response)
    data = content["data"]["availableXeroPayments"]
    assert not data["errors"]
    assert len(data["payments"]) == 2
    assert data["payments"][0]["paymentId"] == "PAY-001"
    assert float(data["payments"][0]["amount"]) == 100.0
    assert data["payments"][1]["paymentId"] == "PAY-002"


def test_available_xero_payments_no_contact_id(
    staff_api_client, permission_group_manage_orders, order_with_lines, customer_user
):
    """Test error when customer has no Xero contact ID."""
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.user = customer_user
    order.user.xero_contact_id = None
    order.user.save()
    order.save()
    order_id = graphene.Node.to_global_id("Order", order.id)

    response = staff_api_client.post_graphql(
        AVAILABLE_XERO_PAYMENTS_QUERY, {"orderId": order_id}
    )

    content = get_graphql_content(response)
    data = content["data"]["availableXeroPayments"]
    assert len(data["errors"]) == 1
    assert "not linked to Xero" in data["errors"][0]["message"]
    assert data["errors"][0]["code"] == OrderErrorCode.INVALID.name


def test_available_xero_payments_no_user(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    """Test error when order has no user."""
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.user = None
    order.save()
    order_id = graphene.Node.to_global_id("Order", order.id)

    response = staff_api_client.post_graphql(
        AVAILABLE_XERO_PAYMENTS_QUERY, {"orderId": order_id}
    )

    content = get_graphql_content(response)
    data = content["data"]["availableXeroPayments"]
    assert len(data["errors"]) == 1
    assert "no customer" in data["errors"][0]["message"]
    assert data["errors"][0]["code"] == OrderErrorCode.INVALID.name
