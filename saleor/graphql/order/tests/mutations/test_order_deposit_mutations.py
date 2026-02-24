from decimal import Decimal
from unittest.mock import patch

import graphene
import pytest
from django.utils import timezone

from .....graphql.tests.utils import get_graphql_content
from .....order.error_codes import OrderErrorCode

ORDER_OVERRIDE_DEPOSIT_THRESHOLD_MUTATION = """
    mutation overrideDepositThreshold($id: ID!, $override: Boolean!) {
        orderOverrideDepositThreshold(id: $id, override: $override) {
            errors {
                field
                message
                code
            }
            order {
                id
            }
        }
    }
"""

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


def test_order_set_deposit_required_blocked_after_prepayment_created(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.deposit_percentage = Decimal(30)
    order.xero_bank_account_code = "090"
    order.xero_deposit_prepayment_id = "existing-prepayment-uuid"
    order.save(
        update_fields=[
            "deposit_required",
            "deposit_percentage",
            "xero_bank_account_code",
            "xero_deposit_prepayment_id",
        ]
    )
    order_id = graphene.Node.to_global_id("Order", order.id)

    response = staff_api_client.post_graphql(
        ORDER_SET_DEPOSIT_REQUIRED_MUTATION,
        {
            "id": order_id,
            "required": True,
            "percentage": 50.0,
            "xeroBankAccountCode": "090",
        },
    )

    content = get_graphql_content(response)
    errors = content["data"]["orderSetDepositRequired"]["errors"]
    assert len(errors) == 1
    assert errors[0]["code"] == OrderErrorCode.INVALID.name


ORDER_REPLACE_DEPOSIT_PREPAYMENT_MUTATION = """
    mutation replaceDeposit(
        $id: ID!, $xeroDepositPrepaymentId: String!,
        $percentage: Decimal, $xeroBankAccountCode: String
    ) {
        orderReplaceDepositPrepayment(
            id: $id, xeroDepositPrepaymentId: $xeroDepositPrepaymentId,
            percentage: $percentage, xeroBankAccountCode: $xeroBankAccountCode
        ) {
            errors {
                field
                message
                code
            }
            order {
                id
                depositPercentage
                xeroBankAccountCode
            }
        }
    }
"""


def test_order_replace_deposit_prepayment(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.deposit_percentage = Decimal(30)
    order.xero_bank_account_code = "090"
    order.xero_deposit_prepayment_id = "old-prepayment-uuid"
    order.save(
        update_fields=[
            "deposit_required",
            "deposit_percentage",
            "xero_bank_account_code",
            "xero_deposit_prepayment_id",
        ]
    )
    order_id = graphene.Node.to_global_id("Order", order.id)

    with patch(
        "saleor.plugins.manager.PluginsManager.xero_check_prepayment_status",
        return_value={"isPaid": False},
    ):
        response = staff_api_client.post_graphql(
            ORDER_REPLACE_DEPOSIT_PREPAYMENT_MUTATION,
            {"id": order_id, "xeroDepositPrepaymentId": "new-prepayment-uuid"},
        )

    content = get_graphql_content(response)
    data = content["data"]["orderReplaceDepositPrepayment"]
    assert not data["errors"]

    order.refresh_from_db()
    assert order.xero_deposit_prepayment_id == "new-prepayment-uuid"
    assert order.deposit_percentage == Decimal(30)


def test_order_replace_deposit_prepayment_updates_percentage_and_bank_account(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.deposit_percentage = Decimal(30)
    order.xero_bank_account_code = "090"
    order.xero_deposit_prepayment_id = "old-prepayment-uuid"
    order.save(
        update_fields=[
            "deposit_required",
            "deposit_percentage",
            "xero_bank_account_code",
            "xero_deposit_prepayment_id",
        ]
    )
    order_id = graphene.Node.to_global_id("Order", order.id)

    with patch(
        "saleor.plugins.manager.PluginsManager.xero_check_prepayment_status",
        return_value={"isPaid": False},
    ):
        response = staff_api_client.post_graphql(
            ORDER_REPLACE_DEPOSIT_PREPAYMENT_MUTATION,
            {
                "id": order_id,
                "xeroDepositPrepaymentId": "new-prepayment-uuid",
                "percentage": 50.0,
                "xeroBankAccountCode": "091",
            },
        )

    content = get_graphql_content(response)
    data = content["data"]["orderReplaceDepositPrepayment"]
    assert not data["errors"]

    order.refresh_from_db()
    assert order.xero_deposit_prepayment_id == "new-prepayment-uuid"
    assert order.deposit_percentage == Decimal(50)
    assert order.xero_bank_account_code == "091"


def test_order_replace_deposit_prepayment_records_payment_if_already_paid(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.deposit_percentage = Decimal(30)
    order.xero_bank_account_code = "090"
    order.xero_deposit_prepayment_id = "old-prepayment-uuid"
    order.save(
        update_fields=[
            "deposit_required",
            "deposit_percentage",
            "xero_bank_account_code",
            "xero_deposit_prepayment_id",
        ]
    )
    order_id = graphene.Node.to_global_id("Order", order.id)

    with patch(
        "saleor.plugins.manager.PluginsManager.xero_check_prepayment_status",
        return_value={"isPaid": True, "amountPaid": 100.00, "datePaid": "2024-01-15"},
    ):
        response = staff_api_client.post_graphql(
            ORDER_REPLACE_DEPOSIT_PREPAYMENT_MUTATION,
            {"id": order_id, "xeroDepositPrepaymentId": "new-prepayment-uuid"},
        )

    content = get_graphql_content(response)
    data = content["data"]["orderReplaceDepositPrepayment"]
    assert not data["errors"]

    order.refresh_from_db()
    assert order.xero_deposit_prepayment_id == "new-prepayment-uuid"
    assert order.deposit_paid_at is not None
    assert order.payments.filter(psp_reference="new-prepayment-uuid").exists()


def test_order_replace_deposit_prepayment_fails_if_deposit_not_required(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order_id = graphene.Node.to_global_id("Order", order_with_lines.id)

    response = staff_api_client.post_graphql(
        ORDER_REPLACE_DEPOSIT_PREPAYMENT_MUTATION,
        {"id": order_id, "xeroDepositPrepaymentId": "new-prepayment-uuid"},
    )

    content = get_graphql_content(response)
    errors = content["data"]["orderReplaceDepositPrepayment"]["errors"]
    assert len(errors) == 1
    assert errors[0]["code"] == OrderErrorCode.INVALID.name


def test_order_replace_deposit_prepayment_fails_if_already_paid(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    from django.utils import timezone

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.deposit_percentage = Decimal(30)
    order.xero_deposit_prepayment_id = "old-prepayment-uuid"
    order.deposit_paid_at = timezone.now()
    order.save(
        update_fields=[
            "deposit_required",
            "deposit_percentage",
            "xero_deposit_prepayment_id",
            "deposit_paid_at",
        ]
    )
    order_id = graphene.Node.to_global_id("Order", order.id)

    response = staff_api_client.post_graphql(
        ORDER_REPLACE_DEPOSIT_PREPAYMENT_MUTATION,
        {"id": order_id, "xeroDepositPrepaymentId": "new-prepayment-uuid"},
    )

    content = get_graphql_content(response)
    errors = content["data"]["orderReplaceDepositPrepayment"]["errors"]
    assert len(errors) == 1
    assert errors[0]["code"] == OrderErrorCode.INVALID.name


def test_order_replace_deposit_prepayment_no_duplicate_if_payment_already_recorded(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    """If a payment with the new prepayment ID was already recorded (e.g. by the CRON task in a race window), the mutation must not create a second payment."""
    from .....payment import ChargeStatus, CustomPaymentChoices
    from .....payment.models import Payment

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.deposit_percentage = Decimal(30)
    order.xero_bank_account_code = "090"
    order.xero_deposit_prepayment_id = "old-prepayment-uuid"
    order.save(
        update_fields=[
            "deposit_required",
            "deposit_percentage",
            "xero_bank_account_code",
            "xero_deposit_prepayment_id",
        ]
    )
    # Simulate CRON having already recorded a payment for the new prepayment ID
    Payment.objects.create(
        order=order,
        gateway=CustomPaymentChoices.XERO,
        psp_reference="new-prepayment-uuid",
        total=Decimal("100.00"),
        captured_amount=Decimal("100.00"),
        charge_status=ChargeStatus.FULLY_CHARGED,
        currency=order.currency,
    )
    order_id = graphene.Node.to_global_id("Order", order.id)

    with patch(
        "saleor.plugins.manager.PluginsManager.xero_check_prepayment_status",
        return_value={"isPaid": True, "amountPaid": 100.00, "datePaid": "2024-01-15"},
    ):
        response = staff_api_client.post_graphql(
            ORDER_REPLACE_DEPOSIT_PREPAYMENT_MUTATION,
            {"id": order_id, "xeroDepositPrepaymentId": "new-prepayment-uuid"},
        )

    content = get_graphql_content(response)
    assert not content["data"]["orderReplaceDepositPrepayment"]["errors"]

    # then - no duplicate payment created
    assert Payment.objects.filter(psp_reference="new-prepayment-uuid").count() == 1


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


def test_order_override_deposit_threshold_sets_deposit_paid_at(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    # given
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.deposit_percentage = Decimal(50)
    order.save(update_fields=["deposit_required", "deposit_percentage"])
    assert order.deposit_paid_at is None

    order_id = graphene.Node.to_global_id("Order", order.id)

    # when
    response = staff_api_client.post_graphql(
        ORDER_OVERRIDE_DEPOSIT_THRESHOLD_MUTATION,
        {"id": order_id, "override": True},
    )

    # then
    content = get_graphql_content(response)
    assert not content["data"]["orderOverrideDepositThreshold"]["errors"]
    order.refresh_from_db()
    assert order.deposit_threshold_met_override is True
    assert order.deposit_paid_at is not None


def test_order_override_deposit_threshold_does_not_overwrite_existing_deposit_paid_at(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    # given
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.deposit_percentage = Decimal(50)
    existing_ts = timezone.now()
    order.deposit_paid_at = existing_ts
    order.save(
        update_fields=["deposit_required", "deposit_percentage", "deposit_paid_at"]
    )

    order_id = graphene.Node.to_global_id("Order", order.id)

    # when
    response = staff_api_client.post_graphql(
        ORDER_OVERRIDE_DEPOSIT_THRESHOLD_MUTATION,
        {"id": order_id, "override": True},
    )

    # then
    content = get_graphql_content(response)
    assert not content["data"]["orderOverrideDepositThreshold"]["errors"]
    order.refresh_from_db()
    assert order.deposit_paid_at == existing_ts


def test_order_override_deposit_threshold_false_does_not_clear_deposit_paid_at(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    # given
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.deposit_percentage = Decimal(50)
    order.deposit_threshold_met_override = True
    existing_ts = timezone.now()
    order.deposit_paid_at = existing_ts
    order.save(
        update_fields=[
            "deposit_required",
            "deposit_percentage",
            "deposit_threshold_met_override",
            "deposit_paid_at",
        ]
    )

    order_id = graphene.Node.to_global_id("Order", order.id)

    # when
    response = staff_api_client.post_graphql(
        ORDER_OVERRIDE_DEPOSIT_THRESHOLD_MUTATION,
        {"id": order_id, "override": False},
    )

    # then
    content = get_graphql_content(response)
    assert not content["data"]["orderOverrideDepositThreshold"]["errors"]
    order.refresh_from_db()
    assert order.deposit_threshold_met_override is False
    assert order.deposit_paid_at == existing_ts


def test_order_override_deposit_threshold_requires_deposit(
    staff_api_client, permission_group_manage_orders, order_with_lines
):
    # given
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    assert order.deposit_required is False
    order_id = graphene.Node.to_global_id("Order", order.id)

    # when
    response = staff_api_client.post_graphql(
        ORDER_OVERRIDE_DEPOSIT_THRESHOLD_MUTATION,
        {"id": order_id, "override": True},
    )

    # then
    content = get_graphql_content(response)
    errors = content["data"]["orderOverrideDepositThreshold"]["errors"]
    assert errors[0]["code"] == OrderErrorCode.INVALID.name
