from decimal import Decimal

import graphene
import pytest

from .....order.calculations import fetch_order_prices_if_expired
from ....tests.utils import get_graphql_content

ORDER_LINE_UPDATE_WITH_PRICE_NET_GROSS_MUTATION = """
    mutation OrderLineUpdate(
        $lineId: ID!
        $quantity: Int!
        $priceNet: PositiveDecimal
        $priceGross: PositiveDecimal
    ) {
        orderLineUpdate(
            id: $lineId
            input: {
                quantity: $quantity
                priceNet: $priceNet
                priceGross: $priceGross
            }
        ) {
            errors {
                field
                message
                code
            }
            orderLine {
                id
                quantity
                unitPrice {
                    net {
                        amount
                    }
                    gross {
                        amount
                    }
                }
                undiscountedUnitPrice {
                    net {
                        amount
                    }
                    gross {
                        amount
                    }
                }
                totalPrice {
                    net {
                        amount
                    }
                    gross {
                        amount
                    }
                }
            }
            order {
                id
                shouldRefreshPrices
            }
        }
    }
"""


def test_set_price_net_only_calculates_gross(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    tax_configuration_flat_rates,
):
    """When only priceNet provided, tax system should calculate gross based on tax rate."""
    # given
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    line = draft_order.lines.first()
    line_id = graphene.Node.to_global_id("OrderLine", line.id)

    price_net = Decimal("100.00")
    expected_tax_rate = Decimal("1.23")  # From tax_configuration_flat_rates
    expected_gross = price_net * expected_tax_rate

    variables = {
        "lineId": line_id,
        "quantity": line.quantity,
        "priceNet": str(price_net),
        "priceGross": None,
    }

    # when
    response = staff_api_client.post_graphql(
        ORDER_LINE_UPDATE_WITH_PRICE_NET_GROSS_MUTATION, variables
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["orderLineUpdate"]

    assert not data["errors"]
    assert data["order"]["shouldRefreshPrices"] is True

    # Refresh prices to trigger tax calculation
    from .....plugins.manager import get_plugins_manager

    line.refresh_from_db()
    draft_order.refresh_from_db()
    manager = get_plugins_manager(allow_replica=False)
    fetch_order_prices_if_expired(
        draft_order,
        manager,
        list(draft_order.lines.all()),
    )
    line.refresh_from_db()

    assert line.unit_price_net_amount == price_net
    assert line.unit_price_gross_amount == pytest.approx(expected_gross)
    assert line.base_unit_price_amount == price_net


def test_set_price_gross_only_returns_error(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
):
    """When only priceGross provided without priceNet, should return error."""
    # given
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    line = draft_order.lines.first()
    line_id = graphene.Node.to_global_id("OrderLine", line.id)

    price_gross = Decimal("123.00")

    variables = {
        "lineId": line_id,
        "quantity": line.quantity,
        "priceNet": None,
        "priceGross": str(price_gross),
    }

    # when
    response = staff_api_client.post_graphql(
        ORDER_LINE_UPDATE_WITH_PRICE_NET_GROSS_MUTATION, variables
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["orderLineUpdate"]

    assert data["errors"]
    assert data["errors"][0]["field"] == "priceGross"
    assert data["errors"][0]["code"] == "REQUIRED"


def test_set_both_prices_no_recalculation(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
):
    """When both priceNet and priceGross provided, store as-is without recalculation."""
    # given
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    line = draft_order.lines.first()
    line_id = graphene.Node.to_global_id("OrderLine", line.id)

    price_net = Decimal("100.00")
    price_gross = Decimal("110.00")  # Implies 10% tax, not 23%

    variables = {
        "lineId": line_id,
        "quantity": line.quantity,
        "priceNet": str(price_net),
        "priceGross": str(price_gross),
    }

    # when
    response = staff_api_client.post_graphql(
        ORDER_LINE_UPDATE_WITH_PRICE_NET_GROSS_MUTATION, variables
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["orderLineUpdate"]

    assert not data["errors"]
    assert data["order"]["shouldRefreshPrices"] is False

    line.refresh_from_db()
    assert line.unit_price_net_amount == price_net
    assert line.unit_price_gross_amount == price_gross
    assert line.base_unit_price_amount == price_net


@pytest.mark.parametrize(
    ("price_net", "price_gross"),
    [
        (Decimal("100.00"), None),  # Only net
        (Decimal("100.00"), Decimal("110.00")),  # Both
    ],
)
def test_base_unit_price_always_equals_net(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    price_net,
    price_gross,
):
    """Verify base_unit_price = net in both valid cases."""
    # given
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    line = draft_order.lines.first()
    line_id = graphene.Node.to_global_id("OrderLine", line.id)

    variables = {
        "lineId": line_id,
        "quantity": line.quantity,
        "priceNet": str(price_net),
        "priceGross": str(price_gross) if price_gross else None,
    }

    # when
    response = staff_api_client.post_graphql(
        ORDER_LINE_UPDATE_WITH_PRICE_NET_GROSS_MUTATION, variables
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["orderLineUpdate"]
    assert not data["errors"]

    line.refresh_from_db()
    # Base should always equal net
    assert line.base_unit_price_amount == price_net


@pytest.mark.parametrize(
    ("price_net", "price_gross", "expected_refresh"),
    [
        (Decimal("100.00"), None, True),  # Only net → refresh
        (Decimal("100.00"), Decimal("110.00"), False),  # Both → no refresh
    ],
)
def test_should_refresh_behavior(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    price_net,
    price_gross,
    expected_refresh,
):
    """Verify should_refresh_prices is set correctly based on which prices are provided."""
    # given
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    line = draft_order.lines.first()
    line_id = graphene.Node.to_global_id("OrderLine", line.id)

    variables = {
        "lineId": line_id,
        "quantity": line.quantity,
        "priceNet": str(price_net),
        "priceGross": str(price_gross) if price_gross else None,
    }

    # when
    response = staff_api_client.post_graphql(
        ORDER_LINE_UPDATE_WITH_PRICE_NET_GROSS_MUTATION, variables
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["orderLineUpdate"]

    assert not data["errors"]
    assert data["order"]["shouldRefreshPrices"] is expected_refresh


def test_price_net_with_standard_tax_rate(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    tax_configuration_flat_rates,
):
    """Set net=100, verify gross=123 with 23% tax rate."""
    # given
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    line = draft_order.lines.first()
    line_id = graphene.Node.to_global_id("OrderLine", line.id)

    price_net = Decimal("100.00")
    expected_gross = Decimal("123.00")  # 100 * 1.23

    variables = {
        "lineId": line_id,
        "quantity": line.quantity,
        "priceNet": str(price_net),
    }

    # when
    response = staff_api_client.post_graphql(
        ORDER_LINE_UPDATE_WITH_PRICE_NET_GROSS_MUTATION, variables
    )

    # then
    content = get_graphql_content(response)
    assert not content["data"]["orderLineUpdate"]["errors"]

    # Trigger tax calculation
    from .....plugins.manager import get_plugins_manager

    line.refresh_from_db()
    draft_order.refresh_from_db()
    manager = get_plugins_manager(allow_replica=False)
    fetch_order_prices_if_expired(
        draft_order,
        manager,
        list(draft_order.lines.all()),
    )
    line.refresh_from_db()

    assert line.unit_price_net_amount == price_net
    assert line.unit_price_gross_amount == expected_gross


def test_price_net_with_tax_exemption(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
):
    """Set net=100 with tax_exemption=True, verify gross=100."""
    # given
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    draft_order.tax_exemption = True
    draft_order.save(update_fields=["tax_exemption"])

    line = draft_order.lines.first()
    line_id = graphene.Node.to_global_id("OrderLine", line.id)

    price_net = Decimal("100.00")

    variables = {
        "lineId": line_id,
        "quantity": line.quantity,
        "priceNet": str(price_net),
    }

    # when
    response = staff_api_client.post_graphql(
        ORDER_LINE_UPDATE_WITH_PRICE_NET_GROSS_MUTATION, variables
    )

    # then
    content = get_graphql_content(response)
    assert not content["data"]["orderLineUpdate"]["errors"]

    # Trigger tax calculation
    from .....plugins.manager import get_plugins_manager

    line.refresh_from_db()
    draft_order.refresh_from_db()
    manager = get_plugins_manager(allow_replica=False)
    fetch_order_prices_if_expired(
        draft_order,
        manager,
        list(draft_order.lines.all()),
    )
    line.refresh_from_db()

    # With tax exemption, gross should equal net
    assert line.unit_price_net_amount == price_net
    assert line.unit_price_gross_amount == price_net


def test_manual_override_mismatched_tax_rate(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
):
    """Set net=100, gross=110 (10% implied) when standard rate is 23%, verify both stored as-is."""
    # given
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    line = draft_order.lines.first()
    line_id = graphene.Node.to_global_id("OrderLine", line.id)

    price_net = Decimal("100.00")
    price_gross = Decimal("110.00")  # 10% tax, not 23%

    variables = {
        "lineId": line_id,
        "quantity": line.quantity,
        "priceNet": str(price_net),
        "priceGross": str(price_gross),
    }

    # when
    response = staff_api_client.post_graphql(
        ORDER_LINE_UPDATE_WITH_PRICE_NET_GROSS_MUTATION, variables
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["orderLineUpdate"]

    assert not data["errors"]

    line.refresh_from_db()
    # Both values should be stored exactly as provided
    assert line.unit_price_net_amount == price_net
    assert line.unit_price_gross_amount == price_gross
    # No recalculation should happen
    assert data["order"]["shouldRefreshPrices"] is False


def test_all_price_fields_updated_correctly(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
):
    """Verify unit_price, undiscounted_unit_price, base_unit_price fields all set correctly."""
    # given
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    line = draft_order.lines.first()
    line_id = graphene.Node.to_global_id("OrderLine", line.id)

    price_net = Decimal("100.00")
    price_gross = Decimal("110.00")

    variables = {
        "lineId": line_id,
        "quantity": line.quantity,
        "priceNet": str(price_net),
        "priceGross": str(price_gross),
    }

    # when
    response = staff_api_client.post_graphql(
        ORDER_LINE_UPDATE_WITH_PRICE_NET_GROSS_MUTATION, variables
    )

    # then
    content = get_graphql_content(response)
    assert not content["data"]["orderLineUpdate"]["errors"]

    line.refresh_from_db()

    # Primary price fields
    assert line.unit_price_net_amount == price_net
    assert line.unit_price_gross_amount == price_gross

    # Base price (always net)
    assert line.base_unit_price_amount == price_net

    # Undiscounted prices (same as prices for manual override)
    assert line.undiscounted_unit_price_net_amount == price_net
    assert line.undiscounted_unit_price_gross_amount == price_gross
    assert line.undiscounted_base_unit_price_amount == price_net


def test_total_price_calculated_from_unit_price(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
):
    """Verify total_price = unit_price × quantity after update."""
    # given
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    line = draft_order.lines.first()
    line_id = graphene.Node.to_global_id("OrderLine", line.id)

    quantity = 3
    price_net = Decimal("100.00")
    price_gross = Decimal("110.00")

    expected_total_net = price_net * quantity
    expected_total_gross = price_gross * quantity

    variables = {
        "lineId": line_id,
        "quantity": quantity,
        "priceNet": str(price_net),
        "priceGross": str(price_gross),
    }

    # when
    response = staff_api_client.post_graphql(
        ORDER_LINE_UPDATE_WITH_PRICE_NET_GROSS_MUTATION, variables
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["orderLineUpdate"]

    assert not data["errors"]
    assert (
        Decimal(data["orderLine"]["totalPrice"]["net"]["amount"]) == expected_total_net
    )
    assert (
        Decimal(data["orderLine"]["totalPrice"]["gross"]["amount"])
        == expected_total_gross
    )

    line.refresh_from_db()
    assert line.total_price_net_amount == expected_total_net
    assert line.total_price_gross_amount == expected_total_gross
