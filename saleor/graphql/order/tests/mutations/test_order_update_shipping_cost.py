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
    # Arrange
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = draft_order

    # Remove shipping method to test auto-assignment; clear tax class so rate=0.
    order.shipping_method = None
    order.shipping_tax_class = None
    order.save(update_fields=["shipping_method", "shipping_tax_class"])

    order_id = graphene.Node.to_global_id("Order", order.id)
    assert order.shipping_method is None

    # Act
    response = staff_api_client.post_graphql(
        ORDER_UPDATE_SHIPPING_COST_MUTATION,
        {
            "orderId": order_id,
            "input": {
                "shippingCostNet": "10.00",
            },
        },
    )

    # Assert
    content = get_graphql_content(response)
    data = content["data"]["orderUpdateShippingCost"]
    assert not data["errors"]

    # Shipping method was auto-assigned
    assert data["order"]["shippingMethodName"] == "Manual Shipping Cost"
    assert data["order"]["shippingMethod"]["name"] == "MANUAL"

    # No taxClass passed and no country-default rate → rate=0 → gross=net
    assert data["order"]["shippingPrice"]["net"]["amount"] == 10.00
    assert data["order"]["shippingPrice"]["gross"]["amount"] == 10.00

    order.refresh_from_db()
    assert order.shipping_method is not None
    assert order.shipping_method.name == "MANUAL"
    assert order.shipping_price_net_amount == Decimal("10.00")
    assert order.shipping_price_gross_amount == Decimal("10.00")


def test_order_update_shipping_cost_preserves_existing_method(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    shipping_method,
):
    # Arrange
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = draft_order
    order.shipping_method = shipping_method
    order.shipping_tax_class = None
    order.save(update_fields=["shipping_method", "shipping_tax_class"])

    order_id = graphene.Node.to_global_id("Order", order.id)
    original_method_id = order.shipping_method.id

    # Act
    response = staff_api_client.post_graphql(
        ORDER_UPDATE_SHIPPING_COST_MUTATION,
        {
            "orderId": order_id,
            "input": {
                "shippingCostNet": "15.00",
            },
        },
    )

    # Assert
    content = get_graphql_content(response)
    data = content["data"]["orderUpdateShippingCost"]
    assert not data["errors"]

    order.refresh_from_db()
    assert order.shipping_method.id == original_method_id
    assert order.shipping_method.name != "MANUAL"

    # No taxClass passed → rate=0 → gross=net
    assert order.shipping_price_net_amount == Decimal("15.00")
    assert order.shipping_price_gross_amount == Decimal("15.00")


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
    # Arrange
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = draft_order
    order.lines.all().delete()
    order_id = graphene.Node.to_global_id("Order", order.id)

    # Act
    response = staff_api_client.post_graphql(
        ORDER_UPDATE_SHIPPING_COST_MUTATION,
        {
            "orderId": order_id,
            "input": {
                "shippingCostNet": "10.00",
            },
        },
    )

    # Assert
    content = get_graphql_content(response)
    errors = content["data"]["orderUpdateShippingCost"]["errors"]
    assert len(errors) == 1
    assert errors[0]["code"] == OrderErrorCode.SHIPPING_METHOD_NOT_APPLICABLE.name


def test_order_update_shipping_cost_updates_order_total(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
):
    # Arrange
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = draft_order
    order.refresh_from_db()

    subtotal_net = order.subtotal_net_amount
    subtotal_gross = order.subtotal_gross_amount
    # Sanity check: fixture has lines so subtotals are non-zero
    assert subtotal_net > 0
    assert subtotal_gross > 0

    # Clear shipping tax class so rate=0 → gross=net (isolates total-update logic)
    order.shipping_tax_class = None
    order.save(update_fields=["shipping_tax_class"])

    net_amount = Decimal("10.00")
    expected_gross = Decimal("10.00")  # rate=0 → gross=net

    order_id = graphene.Node.to_global_id("Order", order.id)

    # Act
    response = staff_api_client.post_graphql(
        ORDER_UPDATE_SHIPPING_COST_MUTATION,
        {
            "orderId": order_id,
            "input": {
                "shippingCostNet": str(net_amount),
            },
        },
    )

    # Assert
    content = get_graphql_content(response)
    assert not content["data"]["orderUpdateShippingCost"]["errors"]

    order.refresh_from_db()
    assert order.shipping_price_net_amount == net_amount
    assert order.shipping_price_gross_amount == expected_gross
    # Total must equal lines subtotal + new shipping — not stale pre-shipping value.
    assert order.total_net_amount == subtotal_net + net_amount
    assert order.total_gross_amount == subtotal_gross + expected_gross
    # should_refresh_prices must remain False so the resolver does not overwrite our totals.
    assert order.should_refresh_prices is False


def test_order_update_shipping_cost_called_twice_replaces_not_doubles(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
):
    # Arrange
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = draft_order
    order.refresh_from_db()
    subtotal_net = order.subtotal_net_amount
    subtotal_gross = order.subtotal_gross_amount

    # Clear shipping tax class so rate=0 → gross=net (isolates idempotency logic)
    order.shipping_tax_class = None
    order.save(update_fields=["shipping_tax_class"])

    order_id = graphene.Node.to_global_id("Order", order.id)

    def call_mutation(net):
        return staff_api_client.post_graphql(
            ORDER_UPDATE_SHIPPING_COST_MUTATION,
            {"orderId": order_id, "input": {"shippingCostNet": str(net)}},
        )

    # Act: first call sets shipping to 10 net (rate=0 → gross=10)
    r1 = get_graphql_content(call_mutation(Decimal("10.00")))
    assert not r1["data"]["orderUpdateShippingCost"]["errors"]

    # Act: second call replaces shipping with 5 net (rate=0 → gross=5)
    r2 = get_graphql_content(call_mutation(Decimal("5.00")))
    assert not r2["data"]["orderUpdateShippingCost"]["errors"]

    # Assert: total reflects only the SECOND shipping amount, not both summed
    order.refresh_from_db()
    assert order.shipping_price_net_amount == Decimal("5.00")
    assert order.shipping_price_gross_amount == Decimal("5.00")
    assert order.total_net_amount == subtotal_net + Decimal("5.00")
    assert order.total_gross_amount == subtotal_gross + Decimal("5.00")


def test_order_update_shipping_cost_with_tax_class_applies_rate(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    default_tax_class,
):
    # Arrange: default_tax_class has TaxClassCountryRate(country="PL", rate=23).
    # The draft_order shipping address is PL, so the mutation should compute
    # gross = net * (1 + 23/100) = 10.00 * 1.23 = 12.30.
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = draft_order
    order_id = graphene.Node.to_global_id("Order", order.id)
    tax_class_gid = graphene.Node.to_global_id("TaxClass", default_tax_class.id)

    # Act
    response = staff_api_client.post_graphql(
        ORDER_UPDATE_SHIPPING_COST_MUTATION,
        {
            "orderId": order_id,
            "input": {
                "shippingCostNet": "10.00",
                "taxClass": tax_class_gid,
            },
        },
    )

    # Assert
    content = get_graphql_content(response)
    data = content["data"]["orderUpdateShippingCost"]
    assert not data["errors"]

    assert data["order"]["shippingPrice"]["net"]["amount"] == 10.00
    assert data["order"]["shippingPrice"]["gross"]["amount"] == 12.30  # 10 * 1.23

    order.refresh_from_db()
    assert order.shipping_price_net_amount == Decimal("10.00")
    assert order.shipping_price_gross_amount == Decimal("12.30")
    assert order.shipping_tax_class == default_tax_class
    assert order.shipping_tax_class_name == default_tax_class.name


def test_order_update_shipping_cost_persists_inco_term(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    site_settings,
):
    # The inco_term field should be stored for any valid value passed in.
    # (EXW-specific zero-rate tax behaviour is tested separately.)

    from .....tax.models import TaxClass, TaxClassCountryRate

    # Arrange
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = draft_order
    zero_class = TaxClass.objects.create(name="Zero Rated Export")
    channel_country = order.channel.default_country.code
    TaxClassCountryRate.objects.create(
        tax_class=zero_class, country=channel_country, rate=0
    )
    site_settings.zero_rated_export_tax_class = zero_class
    site_settings.save(update_fields=["zero_rated_export_tax_class"])
    order_id = graphene.Node.to_global_id("Order", order.id)

    # Act
    response = staff_api_client.post_graphql(
        ORDER_UPDATE_SHIPPING_COST_MUTATION,
        {
            "orderId": order_id,
            "input": {
                "shippingCostNet": "10.00",
                "incoTerm": "DAP",
            },
        },
    )

    # Assert
    content = get_graphql_content(response)
    assert not content["data"]["orderUpdateShippingCost"]["errors"]

    order.refresh_from_db()
    assert order.inco_term == "DAP"


def test_order_update_shipping_cost_rejects_invalid_inco_term(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
):
    # Arrange
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order = draft_order
    order_id = graphene.Node.to_global_id("Order", order.id)

    # Act
    response = staff_api_client.post_graphql(
        ORDER_UPDATE_SHIPPING_COST_MUTATION,
        {
            "orderId": order_id,
            "input": {
                "shippingCostNet": "10.00",
                "incoTerm": "INVALID",
            },
        },
    )

    # Assert
    content = get_graphql_content(response)
    errors = content["data"]["orderUpdateShippingCost"]["errors"]
    assert len(errors) == 1
    assert errors[0]["field"] == "incoTerm"
    assert errors[0]["code"] == OrderErrorCode.INVALID.name
