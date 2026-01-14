"""Tests for pack allocation utilities."""

from ..pack_utils import get_pack_for_product


def test_get_pack_for_product_basic_allocation(product_with_two_variants, channel_USD):
    # given
    product = product_with_two_variants
    variants = list(product.variants.all())

    # Set stock levels: 10 for first variant, 20 for second
    for stock in variants[0].stocks.all():
        stock.quantity = 10
        stock.save()
    for stock in variants[1].stocks.all():
        stock.quantity = 20
        stock.save()

    pack_size = 9  # Request 9 items

    # when
    result = get_pack_for_product(product, pack_size, channel_USD)

    # then
    assert len(result) == 2  # Should have 2 variants
    total_qty = sum(qty for _, qty in result)
    assert total_qty == 9  # Total should be 9

    # With Hamilton's method: 10/30 = 0.33, 20/30 = 0.67
    # 9 * 0.33 = 3, 9 * 0.67 = 6
    variant_allocations = dict(result)
    assert variant_allocations[variants[0]] == 3
    assert variant_allocations[variants[1]] == 6


def test_get_pack_for_product_returns_list_of_tuples(
    product_with_two_variants, channel_USD
):
    # given
    product = product_with_two_variants
    pack_size = 5

    # when
    result = get_pack_for_product(product, pack_size, channel_USD)

    # then
    assert isinstance(result, list)
    for item in result:
        assert isinstance(item, tuple)
        assert len(item) == 2
        variant, qty = item
        assert variant.product == product
        assert isinstance(qty, int)
        assert qty > 0


def test_get_pack_for_product_no_stock(product, channel_USD):
    # given
    product.variants.all().delete()
    pack_size = 5

    # when
    result = get_pack_for_product(product, pack_size, channel_USD)

    # then
    assert result == []


def test_get_pack_for_product_limited_stock(product_with_two_variants, channel_USD):
    # given
    product = product_with_two_variants
    variants = list(product.variants.all())

    # Set very limited stock
    for stock in variants[0].stocks.all():
        stock.quantity = 2
        stock.save()
    for stock in variants[1].stocks.all():
        stock.quantity = 3
        stock.save()

    pack_size = 10  # Request more than available

    # when
    result = get_pack_for_product(product, pack_size, channel_USD)

    # then
    total_qty = sum(qty for _, qty in result)
    assert total_qty == 5  # Should only allocate what's available


def test_get_pack_for_product_proportional_allocation(
    product_with_two_variants, channel_USD
):
    # given
    product = product_with_two_variants
    variants = list(product.variants.all())

    # Set stock: 5 and 40 (total 45)
    for stock in variants[0].stocks.all():
        stock.quantity = 5
        stock.save()
    for stock in variants[1].stocks.all():
        stock.quantity = 40
        stock.save()

    pack_size = 10

    # when
    result = get_pack_for_product(product, pack_size, channel_USD)

    # then
    variant_allocations = dict(result)

    # Hamilton's method: 5/45 ≈ 0.111, 40/45 ≈ 0.889
    # 10 * 0.111 = 1.11 → 1, 10 * 0.889 = 8.89 → 8
    # Remainders: 0.11, 0.89 → give 1 to second variant
    # Result: 1, 9
    assert variant_allocations[variants[0]] == 1
    assert variant_allocations[variants[1]] == 9
