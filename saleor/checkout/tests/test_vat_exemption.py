import pytest

from ..complete_checkout import create_order_from_checkout
from ..fetch import fetch_checkout_info, fetch_checkout_lines


@pytest.mark.django_db
def test_order_auto_tax_exemption_with_vat_number(
    checkout_with_item, customer_user, address, plugins_manager
):
    checkout = checkout_with_item
    checkout.user = customer_user
    checkout.billing_address = address
    checkout.billing_address.metadata = {"vat_number": "DE123456789"}
    checkout.billing_address.save()
    checkout.shipping_address = address
    checkout.email = "test@example.com"
    checkout.save()

    lines, _ = fetch_checkout_lines(checkout)
    checkout_info = fetch_checkout_info(checkout, lines, plugins_manager)

    order = create_order_from_checkout(
        checkout_info=checkout_info,
        manager=plugins_manager,
        user=customer_user,
        app=None,
    )

    assert order.tax_exemption is True


@pytest.mark.django_db
def test_order_no_tax_exemption_without_vat_number(
    checkout_with_item, customer_user, address, plugins_manager
):
    checkout = checkout_with_item
    checkout.user = customer_user
    checkout.billing_address = address
    checkout.billing_address.metadata = {}
    checkout.billing_address.save()
    checkout.shipping_address = address
    checkout.email = "test@example.com"
    checkout.save()

    lines, _ = fetch_checkout_lines(checkout)
    checkout_info = fetch_checkout_info(checkout, lines, plugins_manager)

    order = create_order_from_checkout(
        checkout_info=checkout_info,
        manager=plugins_manager,
        user=customer_user,
        app=None,
    )

    assert order.tax_exemption is False
