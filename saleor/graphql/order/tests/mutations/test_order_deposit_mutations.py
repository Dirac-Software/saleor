from decimal import Decimal
from unittest.mock import patch

import graphene
import pytest

from .....graphql.tests.utils import get_graphql_content
from .....order.error_codes import OrderErrorCode
from .....payment import CustomPaymentChoices

ORDER_SET_DEPOSIT_REQUIRED_MUTATION = """
    mutation setDeposit($id: ID!, $required: Boolean!, $percentage: Decimal) {
        orderSetDepositRequired(id: $id, required: $required, percentage: $percentage) {
            errors {
                field
                message
                code
            }
            order {
                id
                depositRequired
                depositPercentage
            }
        }
    }
"""

ORDER_SYNC_XERO_PAYMENT_MUTATION = """
    mutation syncXeroPayment(
        $orderId: ID!
        $xeroPaymentId: String!
        $isDeposit: Boolean
    ) {
        orderSyncXeroPayment(
            orderId: $orderId
            xeroPaymentId: $xeroPaymentId
            isDeposit: $isDeposit
        ) {
            errors {
                field
                message
                code
            }
            payment {
                id
                gateway
                pspReference
                capturedAmount {
                    amount
                }
            }
            order {
                id
                totalDepositPaid
                depositThresholdMet
                depositPaidAt
            }
        }
    }
"""


def test_order_set_deposit_required(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order_id = graphene.Node.to_global_id("Order", order.id)

    response = staff_api_client.post_graphql(
        ORDER_SET_DEPOSIT_REQUIRED_MUTATION,
        {"id": order_id, "required": True, "percentage": 30.0},
    )

    content = get_graphql_content(response)
    data = content["data"]["orderSetDepositRequired"]
    assert not data["errors"]
    assert data["order"]["depositRequired"] is True
    assert float(data["order"]["depositPercentage"]) == 30.0

    order.refresh_from_db()
    assert order.deposit_required is True
    assert order.deposit_percentage == Decimal("30.0")


@pytest.mark.parametrize(
    ("percentage", "should_error"),
    [
        (-10, True),
        (150, True),
        (0, False),
        (100, False),
        (50.5, False),
    ],
)
def test_order_set_deposit_required_percentage_validation(
    staff_api_client,
    permission_group_manage_orders,
    order_with_lines,
    percentage,
    should_error,
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order_id = graphene.Node.to_global_id("Order", order_with_lines.id)

    response = staff_api_client.post_graphql(
        ORDER_SET_DEPOSIT_REQUIRED_MUTATION,
        {"id": order_id, "required": True, "percentage": percentage},
    )

    content = get_graphql_content(response)
    errors = content["data"]["orderSetDepositRequired"]["errors"]
    assert bool(errors) == should_error
    if should_error:
        assert errors[0]["code"] == OrderErrorCode.INVALID.name


def test_order_sync_xero_payment(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.save()
    order_id = graphene.Node.to_global_id("Order", order.id)
    xero_payment_id = "XERO-PMT-12345"
    amount = Decimal("100.00")

    mock_xero_data = {
        "payment_id": xero_payment_id,
        "amount": amount,
        "date": "2024-01-15T10:00:00",
        "invoice_id": "INV-123",
        "status": "AUTHORISED",
        "contact_id": "XERO-CONTACT-123",
    }

    with patch(
        "saleor.graphql.order.mutations.order_add_xero_payment.validate_xero_payment",
        return_value=mock_xero_data,
    ):
        response = staff_api_client.post_graphql(
            ORDER_SYNC_XERO_PAYMENT_MUTATION,
            {
                "orderId": order_id,
                "xeroPaymentId": xero_payment_id,
                "isDeposit": True,
            },
        )

    content = get_graphql_content(response)
    data = content["data"]["orderSyncXeroPayment"]
    assert not data["errors"]
    assert data["payment"]["gateway"] == "xero"
    assert data["payment"]["pspReference"] == xero_payment_id
    assert data["payment"]["capturedAmount"]["amount"] == float(amount)

    order.refresh_from_db()
    assert order.payments.filter(gateway=CustomPaymentChoices.XERO).count() == 1
    payment = order.payments.first()
    assert payment.psp_reference == xero_payment_id
    assert payment.captured_amount == amount
    assert payment.metadata["is_deposit"] is True


def test_order_sync_xero_payment_without_deposit_required_fails(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = False
    order.save()
    order_id = graphene.Node.to_global_id("Order", order.id)

    response = staff_api_client.post_graphql(
        ORDER_SYNC_XERO_PAYMENT_MUTATION,
        {
            "orderId": order_id,
            "xeroPaymentId": "XERO-PMT-12345",
            "isDeposit": True,
        },
    )

    content = get_graphql_content(response)
    errors = content["data"]["orderSyncXeroPayment"]["errors"]
    assert len(errors) == 1
    assert "does not require deposit" in errors[0]["message"]
    assert errors[0]["code"] == OrderErrorCode.INVALID.name


def test_order_sync_multiple_xero_payments(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.deposit_percentage = Decimal("30.00")
    order.total_gross_amount = Decimal("1000.00")
    order.save()
    order_id = graphene.Node.to_global_id("Order", order.id)

    mock_xero_data1 = {
        "payment_id": "PAY-001",
        "amount": Decimal("100.00"),
        "date": "2024-01-15T10:00:00",
        "invoice_id": "INV-123",
        "status": "AUTHORISED",
        "contact_id": "XERO-CONTACT-123",
    }

    with patch(
        "saleor.graphql.order.mutations.order_add_xero_payment.validate_xero_payment",
        return_value=mock_xero_data1,
    ):
        response1 = staff_api_client.post_graphql(
            ORDER_SYNC_XERO_PAYMENT_MUTATION,
            {
                "orderId": order_id,
                "xeroPaymentId": "PAY-001",
                "isDeposit": True,
            },
        )
    content1 = get_graphql_content(response1)
    assert not content1["data"]["orderSyncXeroPayment"]["errors"]
    assert (
        float(content1["data"]["orderSyncXeroPayment"]["order"]["totalDepositPaid"])
        == 100.0
    )
    assert (
        content1["data"]["orderSyncXeroPayment"]["order"]["depositThresholdMet"] is False
    )
    assert content1["data"]["orderSyncXeroPayment"]["order"]["depositPaidAt"] is None

    mock_xero_data2 = {
        "payment_id": "PAY-002",
        "amount": Decimal("200.00"),
        "date": "2024-01-15T11:00:00",
        "invoice_id": "INV-123",
        "status": "AUTHORISED",
        "contact_id": "XERO-CONTACT-123",
    }

    with patch(
        "saleor.graphql.order.mutations.order_add_xero_payment.validate_xero_payment",
        return_value=mock_xero_data2,
    ):
        response2 = staff_api_client.post_graphql(
            ORDER_SYNC_XERO_PAYMENT_MUTATION,
            {
                "orderId": order_id,
                "xeroPaymentId": "PAY-002",
                "isDeposit": True,
            },
        )
    content2 = get_graphql_content(response2)
    assert not content2["data"]["orderSyncXeroPayment"]["errors"]
    assert (
        float(content2["data"]["orderSyncXeroPayment"]["order"]["totalDepositPaid"])
        == 300.0
    )
    assert (
        content2["data"]["orderSyncXeroPayment"]["order"]["depositThresholdMet"] is True
    )
    assert content2["data"]["orderSyncXeroPayment"]["order"]["depositPaidAt"] is not None

    order.refresh_from_db()
    assert order.payments.filter(gateway=CustomPaymentChoices.XERO).count() == 2
    assert order.total_deposit_paid == Decimal("300.00")
    assert order.deposit_threshold_met is True
    assert order.deposit_paid_at is not None


def test_order_sync_duplicate_xero_payment_id_fails(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.save()
    order_id = graphene.Node.to_global_id("Order", order.id)
    payment_id = "PAY-001"

    mock_xero_data = {
        "payment_id": payment_id,
        "amount": Decimal("100.00"),
        "date": "2024-01-15T10:00:00",
        "invoice_id": "INV-123",
        "status": "AUTHORISED",
        "contact_id": "XERO-CONTACT-123",
    }

    with patch(
        "saleor.graphql.order.mutations.order_add_xero_payment.validate_xero_payment",
        return_value=mock_xero_data,
    ):
        response1 = staff_api_client.post_graphql(
            ORDER_SYNC_XERO_PAYMENT_MUTATION,
            {
                "orderId": order_id,
                "xeroPaymentId": payment_id,
                "isDeposit": True,
            },
        )
        content1 = get_graphql_content(response1)
        assert not content1["data"]["orderSyncXeroPayment"]["errors"]

        response2 = staff_api_client.post_graphql(
            ORDER_SYNC_XERO_PAYMENT_MUTATION,
            {
                "orderId": order_id,
                "xeroPaymentId": payment_id,
                "isDeposit": True,
            },
        )
    content2 = get_graphql_content(response2)
    errors = content2["data"]["orderSyncXeroPayment"]["errors"]
    assert len(errors) == 1
    assert "already exists" in errors[0]["message"]
    assert errors[0]["code"] == OrderErrorCode.UNIQUE.name


def test_order_sync_xero_payment_non_deposit(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order_id = graphene.Node.to_global_id("Order", order.id)

    mock_xero_data = {
        "payment_id": "XERO-FINAL-001",
        "amount": Decimal("500.00"),
        "date": "2024-01-15T10:00:00",
        "invoice_id": "INV-123",
        "status": "AUTHORISED",
        "contact_id": "XERO-CONTACT-123",
    }

    with patch(
        "saleor.graphql.order.mutations.order_add_xero_payment.validate_xero_payment",
        return_value=mock_xero_data,
    ):
        response = staff_api_client.post_graphql(
            ORDER_SYNC_XERO_PAYMENT_MUTATION,
            {
                "orderId": order_id,
                "xeroPaymentId": "XERO-FINAL-001",
                "isDeposit": False,
            },
        )

    content = get_graphql_content(response)
    data = content["data"]["orderSyncXeroPayment"]
    assert not data["errors"]
    assert data["payment"]["gateway"] == "xero"

    order.refresh_from_db()
    payment = order.payments.first()
    assert payment.metadata["is_deposit"] is False


def test_order_sync_xero_payment_validates_customer_match(
    staff_api_client, permission_group_manage_orders, order_with_lines, customer_user
):
    """Test that payment must belong to correct Xero contact."""
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.user = customer_user
    order.user.xero_contact_id = "XERO-CONTACT-CORRECT"
    order.user.save()
    order.save()
    order_id = graphene.Node.to_global_id("Order", order.id)

    # Payment belongs to different customer
    mock_xero_data = {
        "payment_id": "PAY-WRONG-CUSTOMER",
        "amount": Decimal("100.00"),
        "date": "2024-01-15T10:00:00",
        "invoice_id": "INV-123",
        "status": "AUTHORISED",
        "contact_id": "XERO-CONTACT-WRONG",
    }

    with patch(
        "saleor.graphql.order.mutations.order_add_xero_payment.validate_xero_payment",
        return_value=mock_xero_data,
    ):
        response = staff_api_client.post_graphql(
            ORDER_SYNC_XERO_PAYMENT_MUTATION,
            {
                "orderId": order_id,
                "xeroPaymentId": "PAY-WRONG-CUSTOMER",
                "isDeposit": False,
            },
        )

    content = get_graphql_content(response)
    errors = content["data"]["orderSyncXeroPayment"]["errors"]
    assert len(errors) == 1
    assert "different customer" in errors[0]["message"]
    assert errors[0]["code"] == OrderErrorCode.INVALID.name


def test_order_sync_xero_payment_auto_populates_contact_id(
    staff_api_client, permission_group_manage_orders, order_with_lines, customer_user
):
    """Test that user's xero_contact_id is auto-populated from payment."""
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.user = customer_user
    order.user.xero_contact_id = None  # Not set yet
    order.user.save()
    order.save()
    order_id = graphene.Node.to_global_id("Order", order.id)

    mock_xero_data = {
        "payment_id": "PAY-001",
        "amount": Decimal("100.00"),
        "date": "2024-01-15T10:00:00",
        "invoice_id": "INV-123",
        "status": "AUTHORISED",
        "contact_id": "XERO-CONTACT-NEW",
    }

    with patch(
        "saleor.graphql.order.mutations.order_add_xero_payment.validate_xero_payment",
        return_value=mock_xero_data,
    ):
        response = staff_api_client.post_graphql(
            ORDER_SYNC_XERO_PAYMENT_MUTATION,
            {
                "orderId": order_id,
                "xeroPaymentId": "PAY-001",
                "isDeposit": False,
            },
        )

    content = get_graphql_content(response)
    assert not content["data"]["orderSyncXeroPayment"]["errors"]

    # Verify contact ID was auto-populated
    order.user.refresh_from_db()
    assert order.user.xero_contact_id == "XERO-CONTACT-NEW"
