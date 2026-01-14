"""Tests for checkoutAddPack mutation."""

import graphene

from .....checkout.error_codes import CheckoutErrorCode
from .....checkout.fetch import fetch_checkout_lines
from .....checkout.utils import calculate_checkout_quantity
from ....core.utils import to_global_id_or_none
from ....tests.utils import get_graphql_content

MUTATION_CHECKOUT_ADD_PACK = """
mutation checkoutAddPack($id: ID!, $productId: ID!, $packSize: Int!) {
  checkoutAddPack(id: $id, productId: $productId, packSize: $packSize) {
    checkout {
      id
      lines {
        quantity
        variant {
          id
        }
        metadata {
          key
          value
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


def test_checkout_add_pack_basic(
    user_api_client, checkout, product_with_two_variants, channel_USD
):
    # given
    product = product_with_two_variants
    checkout_id = to_global_id_or_none(checkout)
    product_id = graphene.Node.to_global_id("Product", product.pk)

    # Set stock for variants
    variants = list(product.variants.all())
    for stock in variants[0].stocks.all():
        stock.quantity = 10
        stock.save()
    for stock in variants[1].stocks.all():
        stock.quantity = 20
        stock.save()

    pack_size = 6

    variables = {
        "id": checkout_id,
        "productId": product_id,
        "packSize": pack_size,
    }

    # when
    response = user_api_client.post_graphql(MUTATION_CHECKOUT_ADD_PACK, variables)

    # then
    content = get_graphql_content(response)
    data = content["data"]["checkoutAddPack"]
    assert not data["errors"]

    checkout.refresh_from_db()
    lines, _ = fetch_checkout_lines(checkout)
    total_qty = calculate_checkout_quantity(lines)
    assert total_qty == pack_size


def test_checkout_add_pack_creates_pack_metadata(
    user_api_client, checkout, product_with_two_variants
):
    # given
    product = product_with_two_variants
    checkout_id = to_global_id_or_none(checkout)
    product_id = graphene.Node.to_global_id("Product", product.pk)
    pack_size = 5

    variables = {
        "id": checkout_id,
        "productId": product_id,
        "packSize": pack_size,
    }

    # when
    response = user_api_client.post_graphql(MUTATION_CHECKOUT_ADD_PACK, variables)

    # then
    content = get_graphql_content(response)
    data = content["data"]["checkoutAddPack"]
    assert not data["errors"]

    # Check metadata on lines
    checkout_lines = data["checkout"]["lines"]
    pack_ids = set()

    for line in checkout_lines:
        metadata = {m["key"]: m["value"] for m in line["metadata"]}
        assert "pack_id" in metadata
        assert "pack_size" in metadata
        assert metadata["pack_size"] == str(pack_size)
        assert "is_pack_item" in metadata
        assert metadata["is_pack_item"] == "true"
        pack_ids.add(metadata["pack_id"])

    # All lines should have the same pack_id
    assert len(pack_ids) == 1


def test_checkout_add_pack_invalid_pack_size(
    user_api_client, checkout, product_with_two_variants
):
    # given
    product = product_with_two_variants
    checkout_id = to_global_id_or_none(checkout)
    product_id = graphene.Node.to_global_id("Product", product.pk)
    pack_size = 0  # Invalid

    variables = {
        "id": checkout_id,
        "productId": product_id,
        "packSize": pack_size,
    }

    # when
    response = user_api_client.post_graphql(MUTATION_CHECKOUT_ADD_PACK, variables)

    # then
    content = get_graphql_content(response)
    data = content["data"]["checkoutAddPack"]
    errors = data["errors"]
    assert len(errors) == 1
    assert errors[0]["code"] == CheckoutErrorCode.INVALID.name
    assert errors[0]["field"] == "packSize"


def test_checkout_add_pack_no_variants(user_api_client, checkout, product):
    # given
    product.variants.all().delete()
    checkout_id = to_global_id_or_none(checkout)
    product_id = graphene.Node.to_global_id("Product", product.pk)

    variables = {
        "id": checkout_id,
        "productId": product_id,
        "packSize": 5,
    }

    # when
    response = user_api_client.post_graphql(MUTATION_CHECKOUT_ADD_PACK, variables)

    # then
    content = get_graphql_content(response)
    data = content["data"]["checkoutAddPack"]
    errors = data["errors"]
    assert len(errors) == 1
    assert errors[0]["code"] == CheckoutErrorCode.PRODUCT_NOT_PUBLISHED.name


def test_checkout_add_pack_proportional_allocation(
    user_api_client, checkout, product_with_two_variants
):
    # given
    product = product_with_two_variants
    variants = list(product.variants.all())
    checkout_id = to_global_id_or_none(checkout)
    product_id = graphene.Node.to_global_id("Product", product.pk)

    # Set unequal stock: 10 and 40
    for stock in variants[0].stocks.all():
        stock.quantity = 10
        stock.save()
    for stock in variants[1].stocks.all():
        stock.quantity = 40
        stock.save()

    pack_size = 10

    variables = {
        "id": checkout_id,
        "productId": product_id,
        "packSize": pack_size,
    }

    # when
    response = user_api_client.post_graphql(MUTATION_CHECKOUT_ADD_PACK, variables)

    # then
    content = get_graphql_content(response)
    data = content["data"]["checkoutAddPack"]
    assert not data["errors"]

    # Verify allocation is proportional
    checkout_lines = data["checkout"]["lines"]
    variant_quantities = {}
    for line in checkout_lines:
        variant_id = line["variant"]["id"]
        variant_quantities[variant_id] = line["quantity"]

    # With Hamilton's method, should allocate more to variant with more stock
    variant_0_id = graphene.Node.to_global_id("ProductVariant", variants[0].pk)
    variant_1_id = graphene.Node.to_global_id("ProductVariant", variants[1].pk)

    # Expected: 2 for variant 0, 8 for variant 1
    assert variant_quantities[variant_0_id] == 2
    assert variant_quantities[variant_1_id] == 8


def test_checkout_add_pack_insufficient_stock(
    user_api_client, checkout, product_with_two_variants
):
    # given
    product = product_with_two_variants
    variants = list(product.variants.all())
    checkout_id = to_global_id_or_none(checkout)
    product_id = graphene.Node.to_global_id("Product", product.pk)

    # Set very limited stock
    for stock in variants[0].stocks.all():
        stock.quantity = 1
        stock.save()
    for stock in variants[1].stocks.all():
        stock.quantity = 2
        stock.save()

    pack_size = 3  # Total available

    variables = {
        "id": checkout_id,
        "productId": product_id,
        "packSize": pack_size,
    }

    # when
    response = user_api_client.post_graphql(MUTATION_CHECKOUT_ADD_PACK, variables)

    # then
    content = get_graphql_content(response)
    data = content["data"]["checkoutAddPack"]
    assert not data["errors"]

    # Should allocate only what's available
    checkout.refresh_from_db()
    lines, _ = fetch_checkout_lines(checkout)
    total_qty = calculate_checkout_quantity(lines)
    assert total_qty == 3
