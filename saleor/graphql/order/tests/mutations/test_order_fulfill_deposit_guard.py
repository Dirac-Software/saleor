from decimal import Decimal

import graphene

from .....graphql.tests.utils import get_graphql_content
from .....order.error_codes import OrderErrorCode
from .....payment import ChargeStatus, CustomPaymentChoices
from .....payment.models import Payment

ORDER_FULFILL_MUTATION = """
    mutation fulfillOrder($id: ID!, $input: OrderFulfillInput!) {
        orderFulfill(order: $id, input: $input) {
            errors {
                field
                message
                code
            }
            fulfillments {
                id
            }
        }
    }
"""


def test_order_fulfill_blocks_when_deposit_required_not_validated(
    staff_api_client,
    permission_group_manage_orders,
    order_with_lines,
    warehouse,
    site_settings,
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.deposit_percentage = Decimal("30.00")
    order.save()

    site_settings.fulfillment_auto_approve = False
    site_settings.save()

    line = order.lines.first()
    line_id = graphene.Node.to_global_id("OrderLine", line.id)
    warehouse_id = graphene.Node.to_global_id("Warehouse", warehouse.id)

    response = staff_api_client.post_graphql(
        ORDER_FULFILL_MUTATION,
        {
            "id": graphene.Node.to_global_id("Order", order.id),
            "input": {
                "lines": [
                    {
                        "orderLineId": line_id,
                        "stocks": [{"quantity": 1, "warehouse": warehouse_id}],
                    }
                ]
            },
        },
    )

    content = get_graphql_content(response)
    errors = content["data"]["orderFulfill"]["errors"]
    assert len(errors) == 1
    error_message = errors[0]["message"].lower()
    assert "deposit" in error_message
    assert "threshold" in error_message
    assert errors[0]["code"] == OrderErrorCode.INVALID.name


def test_order_fulfill_succeeds_when_deposit_validated(
    staff_api_client,
    permission_group_manage_orders,
    order_with_lines,
    warehouse,
    site_settings,
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = order_with_lines
    order.deposit_required = True
    order.deposit_percentage = Decimal("30.00")
    order.total_gross_amount = Decimal("1000.00")
    order.save()

    Payment.objects.create(
        order=order,
        gateway=CustomPaymentChoices.XERO,
        psp_reference="XERO-PMT-123",
        total=Decimal("300.00"),
        captured_amount=Decimal("300.00"),
        charge_status=ChargeStatus.FULLY_CHARGED,
        currency=order.currency,
        is_active=True,
    )

    site_settings.fulfillment_auto_approve = False
    site_settings.save()

    line = order.lines.first()
    line_id = graphene.Node.to_global_id("OrderLine", line.id)
    warehouse_id = graphene.Node.to_global_id("Warehouse", warehouse.id)

    response = staff_api_client.post_graphql(
        ORDER_FULFILL_MUTATION,
        {
            "id": graphene.Node.to_global_id("Order", order.id),
            "input": {
                "lines": [
                    {
                        "orderLineId": line_id,
                        "stocks": [{"quantity": 1, "warehouse": warehouse_id}],
                    }
                ]
            },
        },
    )

    content = get_graphql_content(response)
    errors = content["data"]["orderFulfill"]["errors"]
    assert not errors
    assert len(content["data"]["orderFulfill"]["fulfillments"]) == 1
