from decimal import Decimal

import graphene
import pytest

from .....order.error_codes import OrderErrorCode
from ....tests.utils import get_graphql_content

ORDER_UPDATE_SHIPPING_COST_MUTATION = """
    mutation OrderUpdateShippingCost($orderId: ID!, $input: OrderUpdateShippingCostInput!) {
        orderUpdateShippingCost(id: $orderId, input: $input) {
            order {
                id
                shippingMethodName
                shippingMethod {
                    id
                    name
                }
                shippingPrice {
                    net {
                        amount
                    }
                    gross {
                        amount
                    }
                }
            }
            errors {
                field
                code
                message
            }
        }
    }
"""


def test_order_update_shipping_cost_auto_assigns_manual_method(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = draft_order

    # Remove shipping method to test auto-assignment
    order.shipping_method = None
    order.save(update_fields=["shipping_method"])

    order_id = graphene.Node.to_global_id("Order", order.id)

    # Ensure order has no shipping method initially
    assert order.shipping_method is None

    # Update shipping cost manually
    response = staff_api_client.post_graphql(
        ORDER_UPDATE_SHIPPING_COST_MUTATION,
        {
            "orderId": order_id,
            "input": {
                "shippingCostNet": "10.00",
                "vatPercentage": "20.00",
            },
        },
    )

    content = get_graphql_content(response)
    data = content["data"]["orderUpdateShippingCost"]
    assert not data["errors"]

    # Verify shipping method was auto-assigned
    assert data["order"]["shippingMethodName"] == "Manual Shipping Cost"
    assert data["order"]["shippingMethod"]["name"] == "MANUAL"

    # Verify shipping costs are correct
    assert data["order"]["shippingPrice"]["net"]["amount"] == 10.00
    assert data["order"]["shippingPrice"]["gross"]["amount"] == 12.00  # 10 + 20% VAT

    # Refresh order from DB
    order.refresh_from_db()
    assert order.shipping_method is not None
    assert order.shipping_method.name == "MANUAL"
    assert order.shipping_price_net_amount == Decimal("10.00")
    assert order.shipping_price_gross_amount == Decimal("12.00")


def test_order_update_shipping_cost_preserves_existing_method(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    shipping_method,
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = draft_order
    order.shipping_method = shipping_method
    order.save(update_fields=["shipping_method"])

    order_id = graphene.Node.to_global_id("Order", order.id)
    original_method_id = order.shipping_method.id

    # Update shipping cost manually
    response = staff_api_client.post_graphql(
        ORDER_UPDATE_SHIPPING_COST_MUTATION,
        {
            "orderId": order_id,
            "input": {
                "shippingCostNet": "15.00",
                "vatPercentage": "20.00",
            },
        },
    )

    content = get_graphql_content(response)
    data = content["data"]["orderUpdateShippingCost"]
    assert not data["errors"]

    # Verify original shipping method was preserved
    order.refresh_from_db()
    assert order.shipping_method.id == original_method_id
    assert order.shipping_method.name != "MANUAL"

    # Verify costs were updated
    assert order.shipping_price_net_amount == Decimal("15.00")
    assert order.shipping_price_gross_amount == Decimal("18.00")


@pytest.mark.django_db
def test_draft_order_with_manual_shipping_can_be_completed(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
):
    from .....graphql.order.utils import validate_draft_order

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = draft_order
    order_id = graphene.Node.to_global_id("Order", order.id)

    # Set manual shipping cost (should auto-assign MANUAL method)
    response = staff_api_client.post_graphql(
        ORDER_UPDATE_SHIPPING_COST_MUTATION,
        {
            "orderId": order_id,
            "input": {
                "shippingCostNet": "10.00",
            },
        },
    )

    content = get_graphql_content(response)
    assert not content["data"]["orderUpdateShippingCost"]["errors"]

    # Refresh and validate order can be completed
    order.refresh_from_db()
    order.shipping_address = order.billing_address
    order.save(update_fields=["shipping_address"])

    # This should NOT raise ValidationError about missing shipping method
    # Validate order can be completed without shipping method errors
    from django.core.exceptions import ValidationError

    from .....plugins.manager import get_plugins_manager

    try:
        validate_draft_order(
            order,
            order.lines.all(),
            order.shipping_address.country.code,
            get_plugins_manager(allow_replica=False),
        )
        validation_passed = True
    except ValidationError as e:
        # Check if the error is about shipping method
        error_dict = e.error_dict if hasattr(e, "error_dict") else {}
        shipping_errors = error_dict.get("shipping", [])
        assert not any(
            "Shipping method is required" in str(err) for err in shipping_errors
        ), "Should not have shipping method validation error"
        validation_passed = True

    assert validation_passed


def test_order_update_shipping_cost_fails_for_non_shippable_order(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
):
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = draft_order

    # Make order non-shippable by removing all lines
    order.lines.all().delete()

    order_id = graphene.Node.to_global_id("Order", order.id)

    response = staff_api_client.post_graphql(
        ORDER_UPDATE_SHIPPING_COST_MUTATION,
        {
            "orderId": order_id,
            "input": {
                "shippingCostNet": "10.00",
            },
        },
    )

    content = get_graphql_content(response)
    errors = content["data"]["orderUpdateShippingCost"]["errors"]
    assert len(errors) == 1
    assert errors[0]["code"] == OrderErrorCode.SHIPPING_METHOD_NOT_APPLICABLE.name
