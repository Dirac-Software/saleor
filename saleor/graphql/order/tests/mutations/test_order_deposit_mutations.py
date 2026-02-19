from decimal import Decimal

import graphene
import pytest

from .....graphql.tests.utils import get_graphql_content
from .....order.error_codes import OrderErrorCode

ORDER_SET_DEPOSIT_REQUIRED_MUTATION = """
    mutation setDeposit(
        $id: ID!, $required: Boolean!, $percentage: Decimal, $xeroBankAccountCode: String
    ) {
        orderSetDepositRequired(
            id: $id, required: $required, percentage: $percentage,
            xeroBankAccountCode: $xeroBankAccountCode
        ) {
            errors {
                field
                message
                code
            }
            order {
                id
                depositRequired
                depositPercentage
                xeroBankAccountCode
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
        {
            "id": order_id,
            "required": True,
            "percentage": 30.0,
            "xeroBankAccountCode": "090",
        },
    )

    content = get_graphql_content(response)
    data = content["data"]["orderSetDepositRequired"]
    assert not data["errors"]
    assert data["order"]["depositRequired"] is True
    assert float(data["order"]["depositPercentage"]) == 30.0
    assert data["order"]["xeroBankAccountCode"] == "090"

    order.refresh_from_db()
    assert order.deposit_required is True
    assert order.deposit_percentage == Decimal("30.0")
    assert order.xero_bank_account_code == "090"


def test_order_set_deposit_required_without_bank_account_fails(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order_id = graphene.Node.to_global_id("Order", order_with_lines.id)

    response = staff_api_client.post_graphql(
        ORDER_SET_DEPOSIT_REQUIRED_MUTATION,
        {"id": order_id, "required": True, "percentage": 30.0},
    )

    content = get_graphql_content(response)
    errors = content["data"]["orderSetDepositRequired"]["errors"]
    assert len(errors) == 1
    assert errors[0]["field"] == "xeroBankAccountCode"
    assert errors[0]["code"] == OrderErrorCode.INVALID.name


def test_order_set_deposit_not_required_clears_bank_account(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.xero_bank_account_code = "090"
    order.save(update_fields=["deposit_required", "xero_bank_account_code"])
    order_id = graphene.Node.to_global_id("Order", order.id)

    response = staff_api_client.post_graphql(
        ORDER_SET_DEPOSIT_REQUIRED_MUTATION,
        {"id": order_id, "required": False},
    )

    content = get_graphql_content(response)
    data = content["data"]["orderSetDepositRequired"]
    assert not data["errors"]
    assert data["order"]["depositRequired"] is False
    assert data["order"]["xeroBankAccountCode"] is None

    order.refresh_from_db()
    assert order.xero_bank_account_code is None


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
        {
            "id": order_id,
            "required": True,
            "percentage": percentage,
            "xeroBankAccountCode": "090",
        },
    )

    content = get_graphql_content(response)
    errors = content["data"]["orderSetDepositRequired"]["errors"]
    assert bool(errors) == should_error
    if should_error:
        assert errors[0]["code"] == OrderErrorCode.INVALID.name
