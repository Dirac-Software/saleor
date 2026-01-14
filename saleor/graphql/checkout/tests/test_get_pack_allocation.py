"""Tests for getPackAllocation query."""

import graphene

from ...core.utils import to_global_id_or_none
from ...tests.utils import get_graphql_content

QUERY_GET_PACK_ALLOCATION = """
query getPackAllocation(
  $productId: ID!
  $packSize: Int!
  $channelSlug: String!
  $checkoutId: ID
) {
  getPackAllocation(
    productId: $productId
    packSize: $packSize
    channelSlug: $channelSlug
    checkoutId: $checkoutId
  ) {
    allocation {
      variant {
        id
      }
      quantity
    }
    canAdd
    currentQuantity
    packQuantity
    totalQuantity
    minimumRequired
    shortfall
    message
  }
}
"""


def test_get_pack_allocation_basic(
    user_api_client, product_with_two_variants, channel_USD
):
    # given
    product = product_with_two_variants
    variants = list(product.variants.all())
    product_id = graphene.Node.to_global_id("Product", product.pk)

    # Set stock
    for stock in variants[0].stocks.all():
        stock.quantity = 10
        stock.save()
    for stock in variants[1].stocks.all():
        stock.quantity = 20
        stock.save()

    variables = {
        "productId": product_id,
        "packSize": 6,
        "channelSlug": channel_USD.slug,
    }

    # when
    response = user_api_client.post_graphql(QUERY_GET_PACK_ALLOCATION, variables)

    # then
    content = get_graphql_content(response)
    data = content["data"]["getPackAllocation"]

    assert data["canAdd"] is True
    assert data["currentQuantity"] == 0
    assert data["packQuantity"] == 6
    assert data["totalQuantity"] == 6
    assert data["minimumRequired"] is None
    assert data["shortfall"] == 0
    assert data["message"] is None

    allocation = data["allocation"]
    assert len(allocation) == 2
    total_allocated = sum(item["quantity"] for item in allocation)
    assert total_allocated == 6


def test_get_pack_allocation_with_checkout_context(
    user_api_client, checkout_with_item, product_with_two_variants, channel_USD
):
    # given
    checkout = checkout_with_item
    product = product_with_two_variants
    product_id = graphene.Node.to_global_id("Product", product.pk)
    checkout_id = to_global_id_or_none(checkout)

    # Add existing lines to checkout (assuming product_with_two_variants is used)
    # For this test we'll assume checkout_with_item uses a different product
    # So currentQuantity should be 0 for this product

    variables = {
        "productId": product_id,
        "packSize": 5,
        "channelSlug": channel_USD.slug,
        "checkoutId": checkout_id,
    }

    # when
    response = user_api_client.post_graphql(QUERY_GET_PACK_ALLOCATION, variables)

    # then
    content = get_graphql_content(response)
    data = content["data"]["getPackAllocation"]

    assert "currentQuantity" in data
    assert data["packQuantity"] == 5
    assert data["totalQuantity"] == data["currentQuantity"] + 5


def test_get_pack_allocation_limited_stock(
    user_api_client, product_with_two_variants, channel_USD
):
    # given
    product = product_with_two_variants
    variants = list(product.variants.all())
    product_id = graphene.Node.to_global_id("Product", product.pk)

    # Set limited stock
    for stock in variants[0].stocks.all():
        stock.quantity = 2
        stock.save()
    for stock in variants[1].stocks.all():
        stock.quantity = 3
        stock.save()

    variables = {
        "productId": product_id,
        "packSize": 10,  # Request more than available
        "channelSlug": channel_USD.slug,
    }

    # when
    response = user_api_client.post_graphql(QUERY_GET_PACK_ALLOCATION, variables)

    # then
    content = get_graphql_content(response)
    data = content["data"]["getPackAllocation"]

    # Should only allocate available stock
    assert data["packQuantity"] == 5
    allocation = data["allocation"]
    total_allocated = sum(item["quantity"] for item in allocation)
    assert total_allocated == 5


def test_get_pack_allocation_no_stock(user_api_client, product, channel_USD):
    # given
    product.variants.all().delete()
    product_id = graphene.Node.to_global_id("Product", product.pk)

    variables = {
        "productId": product_id,
        "packSize": 5,
        "channelSlug": channel_USD.slug,
    }

    # when
    response = user_api_client.post_graphql(QUERY_GET_PACK_ALLOCATION, variables)

    # then
    content = get_graphql_content(response)
    data = content["data"]["getPackAllocation"]

    assert data["packQuantity"] == 0
    assert data["allocation"] == []


def test_get_pack_allocation_proportional(
    user_api_client, product_with_two_variants, channel_USD
):
    # given
    product = product_with_two_variants
    variants = list(product.variants.all())
    product_id = graphene.Node.to_global_id("Product", product.pk)

    # Set unequal stock: 10 and 40
    for stock in variants[0].stocks.all():
        stock.quantity = 10
        stock.save()
    for stock in variants[1].stocks.all():
        stock.quantity = 40
        stock.save()

    variables = {
        "productId": product_id,
        "packSize": 10,
        "channelSlug": channel_USD.slug,
    }

    # when
    response = user_api_client.post_graphql(QUERY_GET_PACK_ALLOCATION, variables)

    # then
    content = get_graphql_content(response)
    data = content["data"]["getPackAllocation"]

    allocation = data["allocation"]
    variant_quantities = {
        item["variant"]["id"]: item["quantity"] for item in allocation
    }

    variant_0_id = graphene.Node.to_global_id("ProductVariant", variants[0].pk)
    variant_1_id = graphene.Node.to_global_id("ProductVariant", variants[1].pk)

    # Hamilton's method: should allocate proportionally
    # 10/50 and 40/50 -> 2 and 8
    assert variant_quantities[variant_0_id] == 2
    assert variant_quantities[variant_1_id] == 8
