"""Tests for incoterm-driven VAT country selection in orderUpdateShippingCost.

Design rules (post zero-rated export feature):
  EXW / FCA / DAP → non-DDP export to a different country → zero-rated export
                    (zero_rated_export_tax_class used, 0% VAT)
  DDP             → rate looked up using destination country (order shipping address)
  no inco_term    → existing inco_term used (default DDP → destination country rate)

The draft_order fixture has:
  - channel.default_country = "US"  (dispatch country)
  - shipping address country  = "PL" (destination country)
  - inco_term = DDP (model default)

The tax class created in each test has:
  - US rate:  10%
  - PL rate:  23%

For zero-rated exports, site_settings.zero_rated_export_tax_class is configured
with US=0%, making the 0% outcome unambiguous.
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


@pytest.fixture
def zero_rated_export_tax_class(db, site_settings):
    """Configure zero_rated_export_tax_class with US=0% on site_settings."""
    tc = TaxClass.objects.create(name="Zero Rated Export")
    TaxClassCountryRate.objects.create(tax_class=tc, country="US", rate=0)
    site_settings.zero_rated_export_tax_class = tc
    site_settings.save(update_fields=["zero_rated_export_tax_class"])
    return tc


def _set_shipping_country(order, country_code):
    order.shipping_address.country = country_code
    order.shipping_address.save(update_fields=["country"])


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


def test_dap_incoterm_zero_rates_shipping(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    two_country_tax_class,
    zero_rated_export_tax_class,
):
    """DAP: non-DDP export → zero-rated export tax class → 0% VAT."""
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    _set_shipping_country(draft_order, "PL")
    order_id = graphene.Node.to_global_id("Order", draft_order.id)
    tax_class_gid = graphene.Node.to_global_id("TaxClass", two_country_tax_class.id)

    data = _call(staff_api_client, order_id, tax_class_gid, inco_term="DAP")

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
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    _set_shipping_country(draft_order, "PL")
    order_id = graphene.Node.to_global_id("Order", draft_order.id)
    tax_class_gid = graphene.Node.to_global_id("TaxClass", two_country_tax_class.id)

    data = _call(staff_api_client, order_id, tax_class_gid, inco_term="DDP")

    assert not data["data"]["orderUpdateShippingCost"]["errors"]
    shipping = data["data"]["orderUpdateShippingCost"]["order"]["shippingPrice"]
    assert shipping["gross"]["amount"] == pytest.approx(12.30, abs=0.01)

    draft_order.refresh_from_db()
    assert draft_order.shipping_price_gross_amount == Decimal("12.30")


def test_exw_incoterm_zero_rates_shipping(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    two_country_tax_class,
    zero_rated_export_tax_class,
):
    """EXW: non-DDP export to different country → zero-rated export → 0% VAT."""
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    _set_shipping_country(draft_order, "PL")
    order_id = graphene.Node.to_global_id("Order", draft_order.id)
    tax_class_gid = graphene.Node.to_global_id("TaxClass", two_country_tax_class.id)

    data = _call(staff_api_client, order_id, tax_class_gid, inco_term="EXW")

    assert not data["data"]["orderUpdateShippingCost"]["errors"]
    shipping = data["data"]["orderUpdateShippingCost"]["order"]["shippingPrice"]
    assert shipping["gross"]["amount"] == float(NET)  # 0% VAT

    draft_order.refresh_from_db()
    assert draft_order.shipping_price_gross_amount == NET


def test_fca_incoterm_zero_rates_shipping(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    two_country_tax_class,
    zero_rated_export_tax_class,
):
    """FCA: non-DDP export to different country → zero-rated export → 0% VAT."""
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    _set_shipping_country(draft_order, "PL")
    order_id = graphene.Node.to_global_id("Order", draft_order.id)
    tax_class_gid = graphene.Node.to_global_id("TaxClass", two_country_tax_class.id)

    data = _call(staff_api_client, order_id, tax_class_gid, inco_term="FCA")

    assert not data["data"]["orderUpdateShippingCost"]["errors"]
    shipping = data["data"]["orderUpdateShippingCost"]["order"]["shippingPrice"]
    assert shipping["gross"]["amount"] == float(NET)  # 0% VAT

    draft_order.refresh_from_db()
    assert draft_order.shipping_price_gross_amount == NET


def test_no_incoterm_uses_destination_country_rate(
    staff_api_client,
    permission_group_manage_orders,
    draft_order,
    two_country_tax_class,
):
    """No incoterm change: existing DDP inco_term uses destination country (PL = 23%)."""
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    _set_shipping_country(draft_order, "PL")
    order_id = graphene.Node.to_global_id("Order", draft_order.id)
    tax_class_gid = graphene.Node.to_global_id("TaxClass", two_country_tax_class.id)

    data = _call(staff_api_client, order_id, tax_class_gid, inco_term=None)

    assert not data["data"]["orderUpdateShippingCost"]["errors"]
    shipping = data["data"]["orderUpdateShippingCost"]["order"]["shippingPrice"]
    assert shipping["gross"]["amount"] == pytest.approx(12.30, abs=0.01)

    draft_order.refresh_from_db()
    assert draft_order.shipping_price_gross_amount == Decimal("12.30")
