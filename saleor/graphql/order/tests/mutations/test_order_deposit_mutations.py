from decimal import Decimal

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

ORDER_ADD_XERO_PAYMENT_MUTATION = """
    mutation addXeroPayment(
        $orderId: ID!
        $xeroPaymentId: String!
        $amount: Decimal!
        $isDeposit: Boolean
        $paidAt: DateTime
    ) {
        orderAddXeroPayment(
            orderId: $orderId
            xeroPaymentId: $xeroPaymentId
            amount: $amount
            isDeposit: $isDeposit
            paidAt: $paidAt
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


def test_order_add_xero_payment(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.save()
    order_id = graphene.Node.to_global_id("Order", order.id)
    xero_payment_id = "XERO-PMT-12345"
    amount = "100.00"

    response = staff_api_client.post_graphql(
        ORDER_ADD_XERO_PAYMENT_MUTATION,
        {
            "orderId": order_id,
            "xeroPaymentId": xero_payment_id,
            "amount": amount,
            "isDeposit": True,
        },
    )

    content = get_graphql_content(response)
    data = content["data"]["orderAddXeroPayment"]
    assert not data["errors"]
    assert data["payment"]["gateway"] == "xero"
    assert data["payment"]["pspReference"] == xero_payment_id
    assert data["payment"]["capturedAmount"]["amount"] == float(amount)

    order.refresh_from_db()
    assert order.payments.filter(gateway=CustomPaymentChoices.XERO).count() == 1
    payment = order.payments.first()
    assert payment.psp_reference == xero_payment_id
    assert payment.captured_amount == Decimal(amount)
    assert payment.metadata["is_deposit"] is True


def test_order_add_xero_payment_without_deposit_required_fails(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = False
    order.save()
    order_id = graphene.Node.to_global_id("Order", order.id)

    response = staff_api_client.post_graphql(
        ORDER_ADD_XERO_PAYMENT_MUTATION,
        {
            "orderId": order_id,
            "xeroPaymentId": "XERO-PMT-12345",
            "amount": "100.00",
            "isDeposit": True,
        },
    )

    content = get_graphql_content(response)
    errors = content["data"]["orderAddXeroPayment"]["errors"]
    assert len(errors) == 1
    assert "does not require deposit" in errors[0]["message"]
    assert errors[0]["code"] == OrderErrorCode.INVALID.name


def test_order_add_multiple_xero_payments(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.deposit_percentage = Decimal("30.00")
    order.total_gross_amount = Decimal("1000.00")
    order.save()
    order_id = graphene.Node.to_global_id("Order", order.id)

    response1 = staff_api_client.post_graphql(
        ORDER_ADD_XERO_PAYMENT_MUTATION,
        {
            "orderId": order_id,
            "xeroPaymentId": "PAY-001",
            "amount": "100.00",
            "isDeposit": True,
        },
    )
    content1 = get_graphql_content(response1)
    assert not content1["data"]["orderAddXeroPayment"]["errors"]
    assert (
        float(content1["data"]["orderAddXeroPayment"]["order"]["totalDepositPaid"])
        == 100.0
    )
    assert (
        content1["data"]["orderAddXeroPayment"]["order"]["depositThresholdMet"] is False
    )
    assert content1["data"]["orderAddXeroPayment"]["order"]["depositPaidAt"] is None

    response2 = staff_api_client.post_graphql(
        ORDER_ADD_XERO_PAYMENT_MUTATION,
        {
            "orderId": order_id,
            "xeroPaymentId": "PAY-002",
            "amount": "200.00",
            "isDeposit": True,
        },
    )
    content2 = get_graphql_content(response2)
    assert not content2["data"]["orderAddXeroPayment"]["errors"]
    assert (
        float(content2["data"]["orderAddXeroPayment"]["order"]["totalDepositPaid"])
        == 300.0
    )
    assert (
        content2["data"]["orderAddXeroPayment"]["order"]["depositThresholdMet"] is True
    )
    assert content2["data"]["orderAddXeroPayment"]["order"]["depositPaidAt"] is not None

    order.refresh_from_db()
    assert order.payments.filter(gateway=CustomPaymentChoices.XERO).count() == 2
    assert order.total_deposit_paid == Decimal("300.00")
    assert order.deposit_threshold_met is True
    assert order.deposit_paid_at is not None


def test_order_add_duplicate_xero_payment_id_fails(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.save()
    order_id = graphene.Node.to_global_id("Order", order.id)
    payment_id = "PAY-001"

    response1 = staff_api_client.post_graphql(
        ORDER_ADD_XERO_PAYMENT_MUTATION,
        {
            "orderId": order_id,
            "xeroPaymentId": payment_id,
            "amount": "100.00",
            "isDeposit": True,
        },
    )
    content1 = get_graphql_content(response1)
    assert not content1["data"]["orderAddXeroPayment"]["errors"]

    response2 = staff_api_client.post_graphql(
        ORDER_ADD_XERO_PAYMENT_MUTATION,
        {
            "orderId": order_id,
            "xeroPaymentId": payment_id,
            "amount": "50.00",
            "isDeposit": True,
        },
    )
    content2 = get_graphql_content(response2)
    errors = content2["data"]["orderAddXeroPayment"]["errors"]
    assert len(errors) == 1
    assert "already exists" in errors[0]["message"]
    assert errors[0]["code"] == OrderErrorCode.UNIQUE.name


def test_order_add_zero_amount_fails(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.save()
    order_id = graphene.Node.to_global_id("Order", order.id)

    response = staff_api_client.post_graphql(
        ORDER_ADD_XERO_PAYMENT_MUTATION,
        {
            "orderId": order_id,
            "xeroPaymentId": "PAY-001",
            "amount": "0",
            "isDeposit": True,
        },
    )

    content = get_graphql_content(response)
    errors = content["data"]["orderAddXeroPayment"]["errors"]
    assert len(errors) == 1
    assert "greater than zero" in errors[0]["message"]
    assert errors[0]["code"] == OrderErrorCode.INVALID.name


def test_order_add_xero_payment_non_deposit(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order_id = graphene.Node.to_global_id("Order", order.id)

    response = staff_api_client.post_graphql(
        ORDER_ADD_XERO_PAYMENT_MUTATION,
        {
            "orderId": order_id,
            "xeroPaymentId": "XERO-FINAL-001",
            "amount": "500.00",
            "isDeposit": False,
        },
    )

    content = get_graphql_content(response)
    data = content["data"]["orderAddXeroPayment"]
    assert not data["errors"]
    assert data["payment"]["gateway"] == "xero"

    order.refresh_from_db()
    payment = order.payments.first()
    assert payment.metadata["is_deposit"] is False
