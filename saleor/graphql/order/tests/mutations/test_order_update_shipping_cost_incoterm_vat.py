"""Tests for incoterm-driven VAT country selection in orderUpdateShippingCost.

Design rules:
  EXW / FCA  → rate looked up using dispatch country (channel.default_country)
  DAP        → always 0% (zero-rated export; customer is importer of record)
  DDP        → rate looked up using destination country (order shipping address)

The draft_order fixture has:
  - channel.default_country = "US"  (dispatch country)
  - shipping address country  = "PL" (destination country)

The tax class created in each test has:
  - US rate:  10%
  - PL rate:  23%

This makes the two countries unambiguously distinguishable in assertions:
  EXW/FCA  → gross = net * 1.10
  DDP      → gross = net * 1.23
  DAP      → gross = net * 1.00
"""

from decimal import Decimal

import graphene
import pytest

from .....tax.models import TaxClass, TaxClassCountryRate
from ....tests.utils import get_graphql_content

ORDER_UPDATE_SHIPPING_COST_MUTATION = """
    mutation OrderUpdateShippingCost($orderId: ID!, $input: OrderUpdateShippingCostInput!) {
        orderUpdateShippingCost(id: $orderId, input: $input) {
            order {
                shippingPrice {
                    net { amount }
                    gross { amount }
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

NET = Decimal("10.00")


@pytest.fixture
def two_country_tax_class(db):
    """Create a TaxClass with US=10% and PL=23%, making dispatch vs destination distinguishable."""
    tc = TaxClass.objects.create(name="Two-Country Test Class")
    TaxClassCountryRate.objects.bulk_create(
        [
            TaxClassCountryRate(tax_class=tc, country="US", rate=10),
            TaxClassCountryRate(tax_class=tc, country="PL", rate=23),
        ]
    )
    return tc


def _call(staff_api_client, order_id, tax_class_gid, inco_term=None):
    variables = {
        "orderId": order_id,
        "input": {
            "shippingCostNet": str(NET),
            "taxClass": tax_class_gid,
        },
    }
    if inco_term:
        variables["input"]["incoTerm"] = inco_term
    return get_graphql_content(
        staff_api_client.post_graphql(ORDER_UPDATE_SHIPPING_COST_MUTATION, variables)
    )


def test_dap_incoterm_always_zero_rates_shipping(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    two_country_tax_class,
):
    """DAP: we are the exporter; rate must be 0% regardless of tax class or country."""
    # Arrange
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order_id = graphene.Node.to_global_id("Order", draft_order.id)
    tax_class_gid = graphene.Node.to_global_id("TaxClass", two_country_tax_class.id)

    # Act
    data = _call(staff_api_client, order_id, tax_class_gid, inco_term="DAP")

    # Assert
    assert not data["data"]["orderUpdateShippingCost"]["errors"]
    shipping = data["data"]["orderUpdateShippingCost"]["order"]["shippingPrice"]
    assert shipping["net"]["amount"] == float(NET)
    assert shipping["gross"]["amount"] == float(NET)  # 0% VAT

    draft_order.refresh_from_db()
    assert draft_order.shipping_price_gross_amount == NET


def test_ddp_incoterm_uses_destination_country_rate(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    two_country_tax_class,
):
    """DDP: we are the importer; rate from destination country (PL = 23%)."""
    # Arrange
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order_id = graphene.Node.to_global_id("Order", draft_order.id)
    tax_class_gid = graphene.Node.to_global_id("TaxClass", two_country_tax_class.id)

    # Act
    data = _call(staff_api_client, order_id, tax_class_gid, inco_term="DDP")

    # Assert – PL rate (23%) → gross = 10.00 * 1.23 = 12.30
    assert not data["data"]["orderUpdateShippingCost"]["errors"]
    shipping = data["data"]["orderUpdateShippingCost"]["order"]["shippingPrice"]
    assert shipping["gross"]["amount"] == pytest.approx(12.30, abs=0.01)

    draft_order.refresh_from_db()
    assert draft_order.shipping_price_gross_amount == Decimal("12.30")


def test_exw_incoterm_uses_dispatch_country_rate(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    two_country_tax_class,
):
    """EXW: sale recognised at origin; rate from dispatch country (channel US = 10%)."""
    # Arrange
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order_id = graphene.Node.to_global_id("Order", draft_order.id)
    tax_class_gid = graphene.Node.to_global_id("TaxClass", two_country_tax_class.id)

    # Act
    data = _call(staff_api_client, order_id, tax_class_gid, inco_term="EXW")

    # Assert – US rate (10%) → gross = 10.00 * 1.10 = 11.00, NOT PL 12.30
    assert not data["data"]["orderUpdateShippingCost"]["errors"]
    shipping = data["data"]["orderUpdateShippingCost"]["order"]["shippingPrice"]
    assert shipping["gross"]["amount"] == pytest.approx(11.00, abs=0.01)

    draft_order.refresh_from_db()
    assert draft_order.shipping_price_gross_amount == Decimal("11.00")


def test_fca_incoterm_uses_dispatch_country_rate(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    two_country_tax_class,
):
    """FCA: goods handed to carrier at origin; rate from dispatch country (channel US = 10%)."""
    # Arrange
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order_id = graphene.Node.to_global_id("Order", draft_order.id)
    tax_class_gid = graphene.Node.to_global_id("TaxClass", two_country_tax_class.id)

    # Act
    data = _call(staff_api_client, order_id, tax_class_gid, inco_term="FCA")

    # Assert – US rate (10%) → gross = 10.00 * 1.10 = 11.00, NOT PL 12.30
    assert not data["data"]["orderUpdateShippingCost"]["errors"]
    shipping = data["data"]["orderUpdateShippingCost"]["order"]["shippingPrice"]
    assert shipping["gross"]["amount"] == pytest.approx(11.00, abs=0.01)

    draft_order.refresh_from_db()
    assert draft_order.shipping_price_gross_amount == Decimal("11.00")


def test_no_incoterm_uses_destination_country_rate(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    two_country_tax_class,
):
    """No incoterm: default behaviour uses destination country (PL = 23%)."""
    # Arrange
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order_id = graphene.Node.to_global_id("Order", draft_order.id)
    tax_class_gid = graphene.Node.to_global_id("TaxClass", two_country_tax_class.id)

    # Act – no incoTerm key in input
    data = _call(staff_api_client, order_id, tax_class_gid, inco_term=None)

    # Assert – PL rate (23%) → gross = 12.30
    assert not data["data"]["orderUpdateShippingCost"]["errors"]
    shipping = data["data"]["orderUpdateShippingCost"]["order"]["shippingPrice"]
    assert shipping["gross"]["amount"] == pytest.approx(12.30, abs=0.01)

    draft_order.refresh_from_db()
    assert draft_order.shipping_price_gross_amount == Decimal("12.30")
