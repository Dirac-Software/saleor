"""Tax decision tree behaviour tests.

The purpose of this file is:
1. Test tax invariants are correct
2. Provide documentation on how taxes should be collected

Prerequisites to our approach:
1.If we export, we can always get valid export evidence
2. We always have the VAT number of our customers (we shouldn't change behaviour
depending on VAT number value).

Tax Class:
We can only offer DDP shipping where we are VAT registered in some country. For us, we
only add TaxClassCountry values where we are VAT registered.

The Ops team have full control over:
1. Which tax classes exist.
2. The correct tax class country mappings + mapping to Xero tax code.
The idea is that by splitting on conflict, we can have a tax class where each tax class
country is correct. Take childrens footwear, reduced rate in UK, but perhaps not
everywhere. If we have a case where in country X blue childrens footwear is zero-rated,
otherwise full rate, the ops team split childrens footwear into blue childrens footwear +
childrens footwear everywhere and then we still have a correct TaxClass. Ultimately if
we do this forever I expect we will end up recreating HS codes. The key knowledge for
the SWE here is that it is not our job!

The decision tree on tax is actually very easy given these prerequisites and tax class
setup.

1. If goods stay in UK use TaxClassCountry=UK tax rate.
2. If goods leave UK and are not DDP, use TaxClassCountry=UK Zero-Rated tax rate
(requires software changing the tax code).
3. If goods leave UK and are DDP, use TaxClassCountry=Shipping Country tax rate.

We simply raise an error if TaxClassCountry doesn't exist.

Shipping Tax Class:
TaxClass is Standard Rate UNLESS all goods are zero rate.
1. If we are shipping outside of UK non-DDP TaxClassCountry=GB zero rate (requires
software changing the tax code).
2. If we are shipping outside of UK DDP TaxClassCountry=Shipping Country, Standard rate

We require behaviour tests on this decision tree.
"""

from decimal import Decimal

import pytest

from ...shipping import IncoTerm
from .. import TaxCalculationStrategy
from ..calculations.order import update_order_prices_with_flat_rates
from ..models import TaxClass


def _enable_flat_rates(order, prices_entered_with_tax):
    tc = order.channel.tax_configuration
    tc.country_exceptions.all().delete()
    tc.prices_entered_with_tax = prices_entered_with_tax
    tc.tax_calculation_strategy = TaxCalculationStrategy.FLAT_RATES
    tc.save()


def test_channel_country_delivery_uses_channel_rate(order_with_lines_untaxed):
    """Rule 1 + shipping: goods stay in channel country → TaxClassCountry[channel] rate for lines and shipping.

    Channel country rate set to 20%.
    """
    # given
    order = order_with_lines_untaxed
    channel_country = order.channel.default_country.code
    order.shipping_address.country = channel_country
    order.shipping_address.save()
    order.save()
    lines = list(order.lines.all())
    for line in lines:
        if line.tax_class:
            line.tax_class.country_rates.update_or_create(
                country=channel_country, defaults={"rate": 20}
            )
    if order.shipping_tax_class:
        order.shipping_tax_class.country_rates.update_or_create(
            country=channel_country, defaults={"rate": 20}
        )
    _enable_flat_rates(order, prices_entered_with_tax=False)

    # when
    update_order_prices_with_flat_rates(order, lines, prices_entered_with_tax=False)

    # then
    for line in lines:
        if line.tax_class:
            assert line.tax_rate == Decimal("0.2000")
    assert order.shipping_tax_rate == Decimal("0.2000")


@pytest.mark.parametrize("inco_term", [IncoTerm.EXW, IncoTerm.FCA, IncoTerm.DAP])
def test_non_channel_country_non_ddp_switches_to_zero_rated_export_tax_class(
    order_with_lines_untaxed, site_settings, inco_term
):
    """Rule 2 + shipping rule 1: goods leave channel country, not DDP → zero-rated export.

    Lines and shipping switch to zero_rated_export_tax_class (configured in SiteSettings).
    The zero-rated tax class carries the correct Xero tax code; we never mutate rates.
    Destination (PL=23%) differs from channel country to make failures visible.
    """
    # given
    order = order_with_lines_untaxed
    channel_country = order.channel.default_country.code
    order.inco_term = inco_term
    order.save()
    zero_rated_export_tax_class = TaxClass.objects.create(name="Zero Rated Export")
    zero_rated_export_tax_class.country_rates.create(country=channel_country, rate=0)
    site_settings.zero_rated_export_tax_class = zero_rated_export_tax_class
    site_settings.save()
    lines = list(order.lines.all())
    _enable_flat_rates(order, prices_entered_with_tax=False)

    # when
    update_order_prices_with_flat_rates(order, lines, prices_entered_with_tax=False)

    # then
    for line in lines:
        if line.variant:
            assert line.tax_class == zero_rated_export_tax_class, (
                f"{inco_term}: line tax_class got {line.tax_class}"
            )
            assert line.tax_rate == Decimal(0), f"{inco_term}: line got {line.tax_rate}"
    assert order.shipping_tax_class == zero_rated_export_tax_class, (
        f"{inco_term}: shipping tax_class got {order.shipping_tax_class}"
    )
    assert order.shipping_tax_rate == Decimal(0), (
        f"{inco_term}: shipping got {order.shipping_tax_rate}"
    )


def test_non_channel_country_ddp_uses_destination_rate(order_with_lines_untaxed):
    """Rule 3 + shipping rule 2: goods leave channel country, DDP → destination rate.

    TaxClassCountry[destination] rate for lines and shipping.
    Destination = PL, PL=23% on default_tax_class.
    """
    # given
    order = order_with_lines_untaxed
    order.inco_term = IncoTerm.DDP
    order.save()
    destination_country = order.shipping_address.country.code
    lines = list(order.lines.all())
    original_tax_classes = {line.pk: line.tax_class for line in lines}
    original_shipping_tax_class = order.shipping_tax_class
    if order.shipping_tax_class:
        order.shipping_tax_class.country_rates.update_or_create(
            country=destination_country, defaults={"rate": 23}
        )
    _enable_flat_rates(order, prices_entered_with_tax=False)

    # when
    update_order_prices_with_flat_rates(order, lines, prices_entered_with_tax=False)

    # then
    for line in lines:
        if line.tax_class:
            assert line.tax_class == original_tax_classes[line.pk]
            assert line.tax_rate == Decimal("0.2300")
    assert order.shipping_tax_class == original_shipping_tax_class
    assert order.shipping_tax_rate == Decimal("0.2300")


def test_non_channel_country_non_ddp_raises_if_zero_rated_export_not_configured(
    order_with_lines_untaxed, site_settings
):
    """Non-DDP export with no zero_rated_export_tax_class in SiteSettings must raise.

    Ops must configure the tax class before exports can be processed.
    """
    # given
    order = order_with_lines_untaxed
    order.inco_term = IncoTerm.EXW
    order.save()
    assert site_settings.zero_rated_export_tax_class is None
    lines = list(order.lines.all())
    _enable_flat_rates(order, prices_entered_with_tax=False)

    # when / then
    with pytest.raises(
        ValueError, match="zero_rated_export_tax_class is not configured"
    ):
        update_order_prices_with_flat_rates(order, lines, prices_entered_with_tax=False)


def test_missing_shipping_tax_class_country_raises_error(order_with_lines_untaxed):
    """Missing TaxClassCountry on shipping must raise, consistent with lines.

    DDP to PL: lines have PL rate and won't raise; shipping has a distinct tax class
    with no PL rate, so only shipping is missing the rate.
    FAILING: get_shipping_tax_rate_for_order silently falls back to default_tax_rate=0.
    """
    # given
    order = order_with_lines_untaxed
    order.inco_term = IncoTerm.DDP
    order.save()
    destination_country = order.shipping_address.country.code
    lines = list(order.lines.all())
    # Give lines a distinct tax class with PL rate so they don't raise
    line_tax_class = TaxClass.objects.create(name="Line Tax Class")
    line_tax_class.country_rates.create(country=destination_country, rate=23)
    for line in lines:
        line.tax_class = line_tax_class
        line.tax_class_id = line_tax_class.pk
    # Shipping gets a separate tax class with no PL rate
    shipping_only_tax_class = TaxClass.objects.create(name="Shipping Tax Class")
    order.shipping_tax_class = shipping_only_tax_class
    _enable_flat_rates(order, prices_entered_with_tax=False)

    # when / then
    with pytest.raises(ValueError, match="No TaxClassCountryRate"):
        update_order_prices_with_flat_rates(order, lines, prices_entered_with_tax=False)


def test_missing_tax_class_country_raises_error(order_with_lines_untaxed):
    """Missing TaxClassCountry must raise, never silently charge 0.

    DDP to PL with no PL rate on the tax class.
    """
    # given
    order = order_with_lines_untaxed
    order.inco_term = IncoTerm.DDP
    order.save()
    lines = list(order.lines.all())
    for line in lines:
        if line.tax_class:
            line.tax_class.country_rates.all().delete()
    _enable_flat_rates(order, prices_entered_with_tax=False)

    # when / then
    with pytest.raises(ValueError, match="No TaxClassCountryRate"):
        update_order_prices_with_flat_rates(order, lines, prices_entered_with_tax=False)
