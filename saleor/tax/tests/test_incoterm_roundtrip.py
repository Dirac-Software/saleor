"""Tests for incoterm switching round-trips in flat-rate tax calculation.

The DDP→non-DDP→DDP round-trip is a known fragile path:
  - non-DDP export sets line.tax_class = zero_rated_export_tax_class in memory
  - fetch_order_prices_if_expired persists tax_class_id to the DB via bulk_update
  - switching back to DDP re-fetches lines from DB; they now have tax_class_id pointing
    at zero_rated_export_tax_class, which has no destination-country rate → ValueError
"""

from decimal import Decimal

import pytest

from ...order.models import OrderLine
from ...shipping import IncoTerm
from .. import TaxCalculationStrategy
from ..calculations.order import update_order_prices_with_flat_rates
from ..models import TaxClass


def _enable_flat_rates(order, prices_entered_with_tax=False):
    tc = order.channel.tax_configuration
    tc.country_exceptions.all().delete()
    tc.prices_entered_with_tax = prices_entered_with_tax
    tc.tax_calculation_strategy = TaxCalculationStrategy.FLAT_RATES
    tc.save()


@pytest.fixture
def roundtrip_order(order_with_lines_untaxed, site_settings):
    """Order configured for DDP→DAP→DDP tests.

    - channel default_country != shipping address country (export scenario)
    - lines have a tax class with a destination-country rate (23%)
    - site_settings has zero_rated_export_tax_class with channel-country rate (0%)
    """
    order = order_with_lines_untaxed
    channel_country = order.channel.default_country.code
    destination_country = order.shipping_address.country.code
    assert channel_country != destination_country, (
        "Fixture requires channel country != shipping country for export scenario"
    )

    lines = list(order.lines.all())
    for line in lines:
        if line.tax_class:
            line.tax_class.country_rates.update_or_create(
                country=destination_country, defaults={"rate": 23}
            )
    if order.shipping_tax_class:
        order.shipping_tax_class.country_rates.update_or_create(
            country=destination_country, defaults={"rate": 23}
        )

    zero_class = TaxClass.objects.create(name="Zero Rated Export")
    zero_class.country_rates.create(country=channel_country, rate=0)
    site_settings.zero_rated_export_tax_class = zero_class
    site_settings.save()

    order.inco_term = IncoTerm.DDP
    order.save()
    _enable_flat_rates(order, prices_entered_with_tax=False)

    return order, lines, zero_class


def test_ddp_to_dap_to_ddp_restores_destination_rate_on_lines(roundtrip_order):
    """DDP → DAP → DDP: line tax rates must return to destination rate (23%).

    This test currently FAILS because:
      1. DAP overwrites line.tax_class with zero_rated_export_tax_class
      2. That is persisted to DB (simulated by bulk_update below)
      3. On the second DDP call, zero_rated_export_tax_class has no destination
         country rate → ValueError (or wrong 0% before the raise-on-missing fix)
    """
    order, lines, zero_class = roundtrip_order

    # Step 1: DDP — expect destination rate on all lines
    update_order_prices_with_flat_rates(order, lines, prices_entered_with_tax=False)
    for line in lines:
        if line.variant:
            assert line.tax_rate == Decimal("0.2300"), (
                f"DDP step: expected 23% on line {line.pk}, got {line.tax_rate}"
            )

    # Step 2: DAP — non-DDP export → zero-rated export tax class
    order.inco_term = IncoTerm.DAP
    order.save()
    update_order_prices_with_flat_rates(order, lines, prices_entered_with_tax=False)
    for line in lines:
        if line.variant:
            assert line.tax_class == zero_class
            assert line.tax_rate == Decimal(0)

    # Simulate what fetch_order_prices_if_expired does: persist tax_class_id to DB
    OrderLine.objects.bulk_update(lines, ["tax_class_id"])

    # Re-fetch lines as the next request would see them
    lines = list(order.lines.prefetch_related("variant__product").all())
    for line in lines:
        if line.variant:
            assert line.tax_class_id == zero_class.pk, (
                "Sanity: DB must have zero_class persisted after DAP"
            )

    # Step 3: DDP again — must restore destination rate (not stay at 0%)
    order.inco_term = IncoTerm.DDP
    order.save()

    # This currently raises ValueError: No TaxClassCountryRate for country 'PL'
    # on tax_class_id=<zero_class.pk>  (zero_class only has channel-country rate)
    update_order_prices_with_flat_rates(order, lines, prices_entered_with_tax=False)

    for line in lines:
        if line.variant:
            assert line.tax_rate == Decimal("0.2300"), (
                f"After DDP→DAP→DDP, expected 23% but got {line.tax_rate}. "
                f"line.tax_class={line.tax_class}"
            )


def test_ddp_to_dap_to_ddp_restores_destination_rate_on_shipping(roundtrip_order):
    """DDP → DAP → DDP: shipping tax rate must return to destination rate (23%).

    Same bug path as for lines: shipping_tax_class_id is overwritten with
    zero_rated_export_tax_class when switching to DAP, then can't find destination rate.
    """
    order, lines, zero_class = roundtrip_order

    # Step 1: DDP baseline
    update_order_prices_with_flat_rates(order, lines, prices_entered_with_tax=False)
    assert order.shipping_tax_rate == Decimal("0.2300")

    # Step 2: DAP — shipping gets zero_rated_export_tax_class
    order.inco_term = IncoTerm.DAP
    order.save()
    update_order_prices_with_flat_rates(order, lines, prices_entered_with_tax=False)
    assert order.shipping_tax_class == zero_class
    assert order.shipping_tax_rate == Decimal(0)

    # Simulate persistence of shipping_tax_class_id (fetch_order_prices_if_expired saves it)
    order.save(update_fields=["shipping_tax_class_id"])

    # Reload order
    order.refresh_from_db()
    assert order.shipping_tax_class_id == zero_class.pk

    # Step 3: DDP again — shipping must restore destination rate
    order.inco_term = IncoTerm.DDP
    order.save()

    update_order_prices_with_flat_rates(order, lines, prices_entered_with_tax=False)
    assert order.shipping_tax_rate == Decimal("0.2300"), (
        f"After DDP→DAP→DDP, shipping expected 23% but got {order.shipping_tax_rate}. "
        f"shipping_tax_class={order.shipping_tax_class}"
    )
