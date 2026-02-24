from decimal import Decimal
from unittest.mock import patch

import graphene

from .....graphql.tests.utils import get_graphql_content
from .....order.error_codes import OrderErrorCode

FULFILLMENT_REPLACE_PREPAYMENT_MUTATION = """
    mutation replacePrepayment($id: ID!, $xeroProformaPrepaymentId: String!) {
        fulfillmentReplacePrepayment(
            id: $id, xeroProformaPrepaymentId: $xeroProformaPrepaymentId
        ) {
            errors {
                field
                message
                code
            }
            fulfillment {
                id
            }
            order {
                id
            }
        }
    }
"""


def test_fulfillment_replace_prepayment(
    staff_api_client, permission_group_manage_orders, fulfillment
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    fulfillment.xero_proforma_prepayment_id = "old-prepayment-uuid"
    fulfillment.save(update_fields=["xero_proforma_prepayment_id"])
    fulfillment_id = graphene.Node.to_global_id("Fulfillment", fulfillment.id)

    with patch(
        "saleor.plugins.manager.PluginsManager.xero_check_prepayment_status",
        return_value={"isPaid": False},
    ):
        response = staff_api_client.post_graphql(
            FULFILLMENT_REPLACE_PREPAYMENT_MUTATION,
            {"id": fulfillment_id, "xeroProformaPrepaymentId": "new-prepayment-uuid"},
        )

    content = get_graphql_content(response)
    data = content["data"]["fulfillmentReplacePrepayment"]
    assert not data["errors"]

    fulfillment.refresh_from_db()
    assert fulfillment.xero_proforma_prepayment_id == "new-prepayment-uuid"


def test_fulfillment_replace_prepayment_records_payment_if_already_paid(
    staff_api_client, permission_group_manage_orders, fulfillment
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    fulfillment.xero_proforma_prepayment_id = "old-prepayment-uuid"
    fulfillment.save(update_fields=["xero_proforma_prepayment_id"])
    fulfillment_id = graphene.Node.to_global_id("Fulfillment", fulfillment.id)

    with patch(
        "saleor.plugins.manager.PluginsManager.xero_check_prepayment_status",
        return_value={"isPaid": True, "amountPaid": 200.00, "datePaid": "2024-03-01"},
    ):
        response = staff_api_client.post_graphql(
            FULFILLMENT_REPLACE_PREPAYMENT_MUTATION,
            {"id": fulfillment_id, "xeroProformaPrepaymentId": "new-prepayment-uuid"},
        )

    content = get_graphql_content(response)
    data = content["data"]["fulfillmentReplacePrepayment"]
    assert not data["errors"]

    fulfillment.refresh_from_db()
    assert fulfillment.xero_proforma_prepayment_id == "new-prepayment-uuid"
    order = fulfillment.order
    assert order.payments.filter(psp_reference="new-prepayment-uuid").exists()


def test_fulfillment_replace_prepayment_no_duplicate_if_payment_already_recorded(
    staff_api_client, permission_group_manage_orders, fulfillment
):
    from .....payment import ChargeStatus, CustomPaymentChoices
    from .....payment.models import Payment

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    fulfillment.xero_proforma_prepayment_id = "old-prepayment-uuid"
    fulfillment.save(update_fields=["xero_proforma_prepayment_id"])
    order = fulfillment.order
    Payment.objects.create(
        order=order,
        gateway=CustomPaymentChoices.XERO,
        psp_reference="new-prepayment-uuid",
        total=Decimal("200.00"),
        captured_amount=Decimal("200.00"),
        charge_status=ChargeStatus.FULLY_CHARGED,
        currency=order.currency,
        is_active=True,
        billing_email=order.user_email,
    )
    fulfillment_id = graphene.Node.to_global_id("Fulfillment", fulfillment.id)

    with patch(
        "saleor.plugins.manager.PluginsManager.xero_check_prepayment_status",
        return_value={"isPaid": True, "amountPaid": 200.00, "datePaid": "2024-03-01"},
    ):
        response = staff_api_client.post_graphql(
            FULFILLMENT_REPLACE_PREPAYMENT_MUTATION,
            {"id": fulfillment_id, "xeroProformaPrepaymentId": "new-prepayment-uuid"},
        )

    content = get_graphql_content(response)
    data = content["data"]["fulfillmentReplacePrepayment"]
    assert not data["errors"]

    assert order.payments.filter(psp_reference="new-prepayment-uuid").count() == 1


def test_fulfillment_replace_prepayment_fails_if_no_prepayment_id(
    staff_api_client, permission_group_manage_orders, fulfillment
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    assert not fulfillment.xero_proforma_prepayment_id
    fulfillment_id = graphene.Node.to_global_id("Fulfillment", fulfillment.id)

    response = staff_api_client.post_graphql(
        FULFILLMENT_REPLACE_PREPAYMENT_MUTATION,
        {"id": fulfillment_id, "xeroProformaPrepaymentId": "new-prepayment-uuid"},
    )

    content = get_graphql_content(response)
    errors = content["data"]["fulfillmentReplacePrepayment"]["errors"]
    assert len(errors) == 1
    assert errors[0]["code"] == OrderErrorCode.INVALID.name


def test_fulfillment_replace_prepayment_fails_if_already_paid(
    staff_api_client, permission_group_manage_orders, fulfillment
):
    from .....payment import ChargeStatus, CustomPaymentChoices
    from .....payment.models import Payment

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    fulfillment.xero_proforma_prepayment_id = "existing-prepayment-uuid"
    fulfillment.save(update_fields=["xero_proforma_prepayment_id"])

    order = fulfillment.order
    Payment.objects.create(
        order=order,
        gateway=CustomPaymentChoices.XERO,
        psp_reference="existing-prepayment-uuid",
        total=Decimal("200.00"),
        captured_amount=Decimal("200.00"),
        charge_status=ChargeStatus.FULLY_CHARGED,
        currency=order.currency,
        is_active=True,
        billing_email=order.user_email,
    )
    fulfillment_id = graphene.Node.to_global_id("Fulfillment", fulfillment.id)

    response = staff_api_client.post_graphql(
        FULFILLMENT_REPLACE_PREPAYMENT_MUTATION,
        {"id": fulfillment_id, "xeroProformaPrepaymentId": "new-prepayment-uuid"},
    )

    content = get_graphql_content(response)
    errors = content["data"]["fulfillmentReplacePrepayment"]["errors"]
    assert len(errors) == 1
    assert errors[0]["code"] == OrderErrorCode.INVALID.name
