"""Tests for getPackAllocation query."""

import graphene

from ....warehouse.models import Stock
from ...core.utils import to_global_id_or_none
from ...tests.utils import get_graphql_content

QUERY_GET_PACK_ALLOCATION_WITH_ORDER = """
query getPackAllocation(
  $productId: ID!
  $packSize: Int!
  $channelSlug: String!
  $orderId: ID
) {
  getPackAllocation(
    productId: $productId
    packSize: $packSize
    channelSlug: $channelSlug
    orderId: $orderId
  ) {
    allocation {
      variant {
        id
      }
      quantity
    }
    canAdd
    packQuantity
  }
}
"""

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
    effectiveMinimum
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


def test_get_pack_allocation_with_effective_minimum_below_available(
    user_api_client, product_with_two_variants, channel_USD
):
    """Test that effective minimum is returned when available < minimum order quantity."""
    # given
    product = product_with_two_variants
    variants = list(product.variants.all())
    product_id = graphene.Node.to_global_id("Product", product.pk)

    # Set stock: 15 items total
    for stock in variants[0].stocks.all():
        stock.quantity = 5
        stock.save()
    for stock in variants[1].stocks.all():
        stock.quantity = 10
        stock.save()

    # Set minimum order quantity to 20 (higher than available)
    from ....attribute.models import Attribute, AttributeValue
    from ....attribute.models.product import AssignedProductAttributeValue

    attribute, _ = Attribute.objects.get_or_create(
        slug="minimum-order-quantity",
        defaults={
            "name": "Minimum Order Quantity",
            "type": "PRODUCT_TYPE",
            "input_type": "DROPDOWN",
        },
    )
    attribute.product_types.add(product.product_type)

    value, _ = AttributeValue.objects.get_or_create(
        attribute=attribute, name="20", defaults={"slug": "20"}
    )

    AssignedProductAttributeValue.objects.create(product=product, value=value)

    # Request pack of 15 (all available)
    variables = {
        "productId": product_id,
        "packSize": 15,
        "channelSlug": channel_USD.slug,
    }

    # when
    response = user_api_client.post_graphql(QUERY_GET_PACK_ALLOCATION, variables)

    # then
    content = get_graphql_content(response)
    data = content["data"]["getPackAllocation"]

    assert data["canAdd"] is True
    assert data["minimumRequired"] == 20
    assert data["effectiveMinimum"] == 15  # min(20, 15)
    assert data["shortfall"] == 0
    assert data["message"] is None


def test_get_pack_allocation_with_effective_minimum_fails_below(
    user_api_client, product_with_two_variants, channel_USD
):
    """Test that validation fails when pack size < effective minimum."""
    # given
    product = product_with_two_variants
    variants = list(product.variants.all())
    product_id = graphene.Node.to_global_id("Product", product.pk)

    # Set stock: 15 items total
    for stock in variants[0].stocks.all():
        stock.quantity = 5
        stock.save()
    for stock in variants[1].stocks.all():
        stock.quantity = 10
        stock.save()

    # Set minimum order quantity to 20 (higher than available)
    from ....attribute.models import Attribute, AttributeValue
    from ....attribute.models.product import AssignedProductAttributeValue

    attribute, _ = Attribute.objects.get_or_create(
        slug="minimum-order-quantity",
        defaults={
            "name": "Minimum Order Quantity",
            "type": "PRODUCT_TYPE",
            "input_type": "DROPDOWN",
        },
    )
    attribute.product_types.add(product.product_type)

    value, _ = AttributeValue.objects.get_or_create(
        attribute=attribute, name="20", defaults={"slug": "20"}
    )

    AssignedProductAttributeValue.objects.create(product=product, value=value)

    # Request pack of 10 (less than effective minimum of 15)
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

    assert data["canAdd"] is False
    assert data["minimumRequired"] == 20
    assert data["effectiveMinimum"] == 15  # min(20, 15)
    assert data["shortfall"] == 5
    assert "Add 5 more items" in data["message"]
    assert "minimum order of 15" in data["message"]


def test_get_pack_allocation_with_minimum_sufficient_stock(
    user_api_client, product_with_two_variants, channel_USD
):
    """Test that minimum validation applies normally when stock is sufficient."""
    # given
    product = product_with_two_variants
    variants = list(product.variants.all())
    product_id = graphene.Node.to_global_id("Product", product.pk)

    # Set stock: 100 items total (more than minimum)
    for stock in variants[0].stocks.all():
        stock.quantity = 40
        stock.save()
    for stock in variants[1].stocks.all():
        stock.quantity = 60
        stock.save()

    # Set minimum order quantity to 20
    from ....attribute.models import Attribute, AttributeValue
    from ....attribute.models.product import AssignedProductAttributeValue

    attribute, _ = Attribute.objects.get_or_create(
        slug="minimum-order-quantity",
        defaults={
            "name": "Minimum Order Quantity",
            "type": "PRODUCT_TYPE",
            "input_type": "DROPDOWN",
        },
    )
    attribute.product_types.add(product.product_type)

    value, _ = AttributeValue.objects.get_or_create(
        attribute=attribute, name="20", defaults={"slug": "20"}
    )

    AssignedProductAttributeValue.objects.create(product=product, value=value)

    # Request pack of 10 (less than minimum of 20)
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

    assert data["canAdd"] is False
    assert data["minimumRequired"] == 20
    assert data["effectiveMinimum"] == 20  # min(20, 100)
    assert data["shortfall"] == 10
    assert "Add 10 more items" in data["message"]
    assert "minimum order of 20" in data["message"]


def test_get_pack_allocation_prevents_insufficient_remaining_stock(
    user_api_client, product_with_two_variants, channel_USD
):
    """Test that ordering must not leave stock below MOQ threshold.

    When available=11 and MOQ=10, ordering 10 would leave 1 item (below MOQ).
    Customer must order all 11 items.
    """
    # given
    product = product_with_two_variants
    variants = list(product.variants.all())
    product_id = graphene.Node.to_global_id("Product", product.pk)

    # Set stock: 11 items total
    for stock in variants[0].stocks.all():
        stock.quantity = 5
        stock.save()
    for stock in variants[1].stocks.all():
        stock.quantity = 6
        stock.save()

    # Set minimum order quantity to 10
    from ....attribute.models import Attribute, AttributeValue
    from ....attribute.models.product import AssignedProductAttributeValue

    attribute, _ = Attribute.objects.get_or_create(
        slug="minimum-order-quantity",
        defaults={
            "name": "Minimum Order Quantity",
            "type": "PRODUCT_TYPE",
            "input_type": "DROPDOWN",
        },
    )
    attribute.product_types.add(product.product_type)

    value, _ = AttributeValue.objects.get_or_create(
        attribute=attribute, name="10", defaults={"slug": "10"}
    )

    AssignedProductAttributeValue.objects.create(product=product, value=value)

    # Request pack of 10 (would leave 1 item, below MOQ)
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

    assert data["canAdd"] is False
    assert data["minimumRequired"] == 10
    assert data["effectiveMinimum"] == 11  # Must take all 11
    assert data["shortfall"] == 1
    assert "Cannot leave less than 10 items remaining" in data["message"]
    assert "Add 1 more to order all 11 available" in data["message"]


def test_get_pack_allocation_allows_order_leaving_sufficient_stock(
    user_api_client, product_with_two_variants, channel_USD
):
    """Test that ordering is allowed if it leaves at least MOQ remaining.

    When available=38 and MOQ=10, ordering 28 leaves 10 items (meets MOQ threshold).
    """
    # given
    product = product_with_two_variants
    variants = list(product.variants.all())
    product_id = graphene.Node.to_global_id("Product", product.pk)

    # Set stock: 38 items total
    for stock in variants[0].stocks.all():
        stock.quantity = 18
        stock.save()
    for stock in variants[1].stocks.all():
        stock.quantity = 20
        stock.save()

    # Set minimum order quantity to 10
    from ....attribute.models import Attribute, AttributeValue
    from ....attribute.models.product import AssignedProductAttributeValue

    attribute, _ = Attribute.objects.get_or_create(
        slug="minimum-order-quantity",
        defaults={
            "name": "Minimum Order Quantity",
            "type": "PRODUCT_TYPE",
            "input_type": "DROPDOWN",
        },
    )
    attribute.product_types.add(product.product_type)

    value, _ = AttributeValue.objects.get_or_create(
        attribute=attribute, name="10", defaults={"slug": "10"}
    )

    AssignedProductAttributeValue.objects.create(product=product, value=value)

    # Request pack of 28 (leaves exactly 10 items)
    variables = {
        "productId": product_id,
        "packSize": 28,
        "channelSlug": channel_USD.slug,
    }

    # when
    response = user_api_client.post_graphql(QUERY_GET_PACK_ALLOCATION, variables)

    # then
    content = get_graphql_content(response)
    data = content["data"]["getPackAllocation"]

    assert data["canAdd"] is True
    assert data["minimumRequired"] == 10
    assert data["effectiveMinimum"] == 10  # Normal MOQ applies
    assert data["shortfall"] == 0
    assert data["message"] is None


def test_get_pack_allocation_prevents_insufficient_remaining_stock_large(
    user_api_client, product_with_two_variants, channel_USD
):
    """Test that ordering 29 from 38 available fails (leaves 9, below MOQ of 10).

    When available=38 and MOQ=10, ordering 29 would leave 9 items (below MOQ).
    Customer must order all 38 items.
    """
    # given
    product = product_with_two_variants
    variants = list(product.variants.all())
    product_id = graphene.Node.to_global_id("Product", product.pk)

    # Set stock: 38 items total
    for stock in variants[0].stocks.all():
        stock.quantity = 18
        stock.save()
    for stock in variants[1].stocks.all():
        stock.quantity = 20
        stock.save()

    # Set minimum order quantity to 10
    from ....attribute.models import Attribute, AttributeValue
    from ....attribute.models.product import AssignedProductAttributeValue

    attribute, _ = Attribute.objects.get_or_create(
        slug="minimum-order-quantity",
        defaults={
            "name": "Minimum Order Quantity",
            "type": "PRODUCT_TYPE",
            "input_type": "DROPDOWN",
        },
    )
    attribute.product_types.add(product.product_type)

    value, _ = AttributeValue.objects.get_or_create(
        attribute=attribute, name="10", defaults={"slug": "10"}
    )

    AssignedProductAttributeValue.objects.create(product=product, value=value)

    # Request pack of 29 (would leave 9 items, below MOQ)
    variables = {
        "productId": product_id,
        "packSize": 29,
        "channelSlug": channel_USD.slug,
    }

    # when
    response = user_api_client.post_graphql(QUERY_GET_PACK_ALLOCATION, variables)

    # then
    content = get_graphql_content(response)
    data = content["data"]["getPackAllocation"]

    assert data["canAdd"] is False
    assert data["minimumRequired"] == 10
    assert data["effectiveMinimum"] == 38  # Must take all 38
    assert data["shortfall"] == 9
    assert "Cannot leave less than 10 items remaining" in data["message"]
    assert "Add 9 more to order all 38 available" in data["message"]


def test_get_pack_allocation_with_order_respects_allowed_warehouses(
    staff_api_client,
    permission_group_manage_orders,
    product_with_two_variants,
    draft_order,
    warehouse,
    address,
    shipping_zone,
    channel_USD,
):
    """Pack allocation only considers stock from order.allowed_warehouses when set."""
    # given
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    product = product_with_two_variants
    variants = list(product.variants.all())
    product_id = graphene.Node.to_global_id("Product", product.pk)

    restricted_warehouse = warehouse
    excluded_warehouse = type(warehouse).objects.create(
        address=address,
        name="Excluded Warehouse",
        slug="excluded-warehouse",
        email="excluded@example.com",
    )
    excluded_warehouse.shipping_zones.add(shipping_zone)
    excluded_warehouse.channels.add(channel_USD)

    for stock in variants[0].stocks.filter(warehouse=restricted_warehouse):
        stock.quantity = 6
        stock.save()
    for stock in variants[1].stocks.filter(warehouse=restricted_warehouse):
        stock.quantity = 4
        stock.save()

    Stock.objects.create(
        warehouse=excluded_warehouse, product_variant=variants[0], quantity=100
    )
    Stock.objects.create(
        warehouse=excluded_warehouse, product_variant=variants[1], quantity=100
    )

    draft_order.allowed_warehouses.set([restricted_warehouse])
    order_id = graphene.Node.to_global_id("Order", draft_order.pk)

    variables = {
        "productId": product_id,
        "packSize": 20,
        "channelSlug": channel_USD.slug,
        "orderId": order_id,
    }

    # when
    response = staff_api_client.post_graphql(
        QUERY_GET_PACK_ALLOCATION_WITH_ORDER, variables
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["getPackAllocation"]

    # Only 10 units available in the restricted warehouse, not 210
    assert data["packQuantity"] == 10
    total = sum(alloc["quantity"] for alloc in data["allocation"])
    assert total == 10


def test_get_pack_allocation_with_order_empty_allowed_warehouses_uses_all(
    staff_api_client,
    permission_group_manage_orders,
    product_with_two_variants,
    draft_order,
    warehouse,
    address,
    shipping_zone,
    channel_USD,
):
    """Pack allocation uses all warehouses when order.allowed_warehouses is empty."""
    # given
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    product = product_with_two_variants
    variants = list(product.variants.all())
    product_id = graphene.Node.to_global_id("Product", product.pk)

    second_warehouse = type(warehouse).objects.create(
        address=address,
        name="Second Warehouse",
        slug="second-warehouse-pack",
        email="second@example.com",
    )
    second_warehouse.shipping_zones.add(shipping_zone)
    second_warehouse.channels.add(channel_USD)

    # Use distinct quantities per warehouse to avoid Sum(distinct=True) deduplication
    for stock in variants[0].stocks.filter(warehouse=warehouse):
        stock.quantity = 6
        stock.save()
    for stock in variants[1].stocks.filter(warehouse=warehouse):
        stock.quantity = 4
        stock.save()

    Stock.objects.create(
        warehouse=second_warehouse, product_variant=variants[0], quantity=7
    )
    Stock.objects.create(
        warehouse=second_warehouse, product_variant=variants[1], quantity=3
    )

    assert draft_order.allowed_warehouses.count() == 0
    order_id = graphene.Node.to_global_id("Order", draft_order.pk)

    variables = {
        "productId": product_id,
        "packSize": 30,
        "channelSlug": channel_USD.slug,
        "orderId": order_id,
    }

    # when
    response = staff_api_client.post_graphql(
        QUERY_GET_PACK_ALLOCATION_WITH_ORDER, variables
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["getPackAllocation"]

    # 20 total across both warehouses (6+7=13 for v0, 4+3=7 for v1), capped at pack size of 20
    assert data["packQuantity"] == 20
    total = sum(alloc["quantity"] for alloc in data["allocation"])
    assert total == 20
