import graphene
from django.utils import timezone

from .....order.error_codes import OrderErrorCode
from ....tests.utils import assert_no_permission, get_graphql_content

MARK_PROFORMA_PAID_MUTATION = """
    mutation markProformaPaid($id: ID!, $paidAt: DateTime) {
        orderFulfillmentMarkProformaPaid(id: $id, paidAt: $paidAt) {
            fulfillment {
                id
                proformaInvoicePaid
                proformaInvoicePaidAt
            }
            errors {
                field
                code
                message
            }
        }
    }
"""


def test_mark_proforma_paid(
    staff_api_client,
    fulfillment_proforma_awaiting_payment,
    permission_group_manage_orders,
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    fulfillment = fulfillment_proforma_awaiting_payment

    assert fulfillment.proforma_invoice is not None
    assert fulfillment.proforma_invoice_paid is False
    assert fulfillment.proforma_invoice_paid_at is None

    fulfillment_id = graphene.Node.to_global_id("Fulfillment", fulfillment.id)
    variables = {"id": fulfillment_id}

    response = staff_api_client.post_graphql(MARK_PROFORMA_PAID_MUTATION, variables)

    content = get_graphql_content(response)
    data = content["data"]["orderFulfillmentMarkProformaPaid"]
    assert not data["errors"]
    assert data["fulfillment"]["proformaInvoicePaid"] is True
    assert data["fulfillment"]["proformaInvoicePaidAt"] is not None

    fulfillment.refresh_from_db()
    assert fulfillment.proforma_invoice_paid is True
    assert fulfillment.proforma_invoice_paid_at is not None


def test_mark_proforma_paid_with_custom_timestamp(
    staff_api_client,
    fulfillment_proforma_awaiting_payment,
    permission_group_manage_orders,
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    fulfillment = fulfillment_proforma_awaiting_payment

    paid_at = timezone.now() - timezone.timedelta(days=1)
    fulfillment_id = graphene.Node.to_global_id("Fulfillment", fulfillment.id)
    variables = {
        "id": fulfillment_id,
        "paidAt": paid_at.isoformat(),
    }

    response = staff_api_client.post_graphql(MARK_PROFORMA_PAID_MUTATION, variables)

    content = get_graphql_content(response)
    data = content["data"]["orderFulfillmentMarkProformaPaid"]
    assert not data["errors"]

    fulfillment.refresh_from_db()
    assert fulfillment.proforma_invoice_paid is True
    assert fulfillment.proforma_invoice_paid_at is not None


def test_mark_proforma_paid_no_proforma_invoice(
    staff_api_client,
    fulfillment,
    permission_group_manage_orders,
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)

    assert not hasattr(fulfillment, "proforma_invoice")

    fulfillment_id = graphene.Node.to_global_id("Fulfillment", fulfillment.id)
    variables = {"id": fulfillment_id}

    response = staff_api_client.post_graphql(MARK_PROFORMA_PAID_MUTATION, variables)

    content = get_graphql_content(response)
    data = content["data"]["orderFulfillmentMarkProformaPaid"]
    assert len(data["errors"]) == 1
    error = data["errors"][0]
    assert error["field"] == "id"
    assert error["code"] == OrderErrorCode.INVALID.name


def test_mark_proforma_paid_already_paid(
    staff_api_client,
    fulfillment_proforma_paid,
    permission_group_manage_orders,
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    fulfillment = fulfillment_proforma_paid

    assert fulfillment.proforma_invoice_paid is True

    fulfillment_id = graphene.Node.to_global_id("Fulfillment", fulfillment.id)
    variables = {"id": fulfillment_id}

    response = staff_api_client.post_graphql(MARK_PROFORMA_PAID_MUTATION, variables)

    content = get_graphql_content(response)
    data = content["data"]["orderFulfillmentMarkProformaPaid"]
    assert len(data["errors"]) == 1
    error = data["errors"][0]
    assert error["field"] == "id"
    assert error["code"] == OrderErrorCode.INVALID.name


def test_mark_proforma_paid_no_permission(
    staff_api_client,
    fulfillment_proforma_awaiting_payment,
):
    fulfillment = fulfillment_proforma_awaiting_payment
    fulfillment_id = graphene.Node.to_global_id("Fulfillment", fulfillment.id)
    variables = {"id": fulfillment_id}

    response = staff_api_client.post_graphql(MARK_PROFORMA_PAID_MUTATION, variables)

    assert_no_permission(response)


def test_mark_proforma_paid_by_app(
    app_api_client,
    fulfillment_proforma_awaiting_payment,
    permission_manage_orders,
):
    fulfillment = fulfillment_proforma_awaiting_payment
    fulfillment_id = graphene.Node.to_global_id("Fulfillment", fulfillment.id)
    variables = {"id": fulfillment_id}

    response = app_api_client.post_graphql(
        MARK_PROFORMA_PAID_MUTATION,
        variables,
        permissions=[permission_manage_orders],
    )

    content = get_graphql_content(response)
    data = content["data"]["orderFulfillmentMarkProformaPaid"]
    assert not data["errors"]
    assert data["fulfillment"]["proformaInvoicePaid"] is True

    fulfillment.refresh_from_db()
    assert fulfillment.proforma_invoice_paid is True
