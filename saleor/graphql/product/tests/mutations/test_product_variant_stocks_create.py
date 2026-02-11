import graphene

from .....warehouse.error_codes import StockErrorCode
from .....warehouse.models import Stock, Warehouse
from ....tests.utils import get_graphql_content

VARIANT_STOCKS_CREATE_MUTATION = """
    mutation ProductVariantStocksCreate($variantId: ID!, $stocks: [StockInput!]!){
        productVariantStocksCreate(variantId: $variantId, stocks: $stocks){
            productVariant{
                id
                stocks {
                    quantity
                    quantityAllocated
                    id
                    warehouse{
                        slug
                    }
                }
            }
            errors{
                code
                field
                message
                index
            }
        }
    }
"""


def test_variant_stocks_create(
    staff_api_client, variant, warehouse, permission_manage_products
):
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.pk)
    second_warehouse = Warehouse.objects.get(pk=warehouse.pk)
    second_warehouse.slug = "second warehouse"
    second_warehouse.pk = None
    second_warehouse.save()

    stocks = [
        {
            "warehouse": graphene.Node.to_global_id("Warehouse", warehouse.id),
            "quantity": 20,
        },
        {
            "warehouse": graphene.Node.to_global_id("Warehouse", second_warehouse.id),
            "quantity": 100,
        },
    ]
    variables = {"variantId": variant_id, "stocks": stocks}
    response = staff_api_client.post_graphql(
        VARIANT_STOCKS_CREATE_MUTATION,
        variables,
        permissions=[permission_manage_products],
    )
    content = get_graphql_content(response)
    data = content["data"]["productVariantStocksCreate"]

    expected_result = [
        {
            "quantity": stocks[0]["quantity"],
            "quantityAllocated": 0,
            "warehouse": {"slug": warehouse.slug},
        },
        {
            "quantity": stocks[1]["quantity"],
            "quantityAllocated": 0,
            "warehouse": {"slug": second_warehouse.slug},
        },
    ]
    assert not data["errors"]
    assert len(data["productVariant"]["stocks"]) == len(stocks)
    result = []
    for stock in data["productVariant"]["stocks"]:
        stock.pop("id")
        result.append(stock)
    for res in result:
        assert res in expected_result


def test_variant_stocks_create_empty_stock_input(
    staff_api_client, variant, permission_manage_products
):
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.pk)

    variables = {"variantId": variant_id, "stocks": []}
    response = staff_api_client.post_graphql(
        VARIANT_STOCKS_CREATE_MUTATION,
        variables,
        permissions=[permission_manage_products],
    )
    content = get_graphql_content(response)
    data = content["data"]["productVariantStocksCreate"]

    assert not data["errors"]
    assert len(data["productVariant"]["stocks"]) == variant.stocks.count()
    assert data["productVariant"]["id"] == variant_id


def test_variant_stocks_create_stock_already_exists(
    staff_api_client, variant, warehouse, permission_manage_products
):
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.pk)
    second_warehouse = Warehouse.objects.get(pk=warehouse.pk)
    second_warehouse.slug = "second warehouse"
    second_warehouse.pk = None
    second_warehouse.save()

    Stock.objects.create(product_variant=variant, warehouse=warehouse, quantity=10)

    stocks = [
        {
            "warehouse": graphene.Node.to_global_id("Warehouse", warehouse.id),
            "quantity": 20,
        },
        {
            "warehouse": graphene.Node.to_global_id("Warehouse", second_warehouse.id),
            "quantity": 100,
        },
    ]
    variables = {"variantId": variant_id, "stocks": stocks}
    response = staff_api_client.post_graphql(
        VARIANT_STOCKS_CREATE_MUTATION,
        variables,
        permissions=[permission_manage_products],
    )
    content = get_graphql_content(response)
    data = content["data"]["productVariantStocksCreate"]
    errors = data["errors"]

    assert errors
    assert errors[0]["code"] == StockErrorCode.UNIQUE.name
    assert errors[0]["field"] == "warehouse"
    assert errors[0]["index"] == 0


def test_variant_stocks_create_stock_duplicated_warehouse(
    staff_api_client, variant, warehouse, permission_manage_products
):
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.pk)
    second_warehouse = Warehouse.objects.get(pk=warehouse.pk)
    second_warehouse.slug = "second warehouse"
    second_warehouse.pk = None
    second_warehouse.save()

    second_warehouse_id = graphene.Node.to_global_id("Warehouse", second_warehouse.id)

    stocks = [
        {
            "warehouse": graphene.Node.to_global_id("Warehouse", warehouse.id),
            "quantity": 20,
        },
        {"warehouse": second_warehouse_id, "quantity": 100},
        {"warehouse": second_warehouse_id, "quantity": 120},
    ]
    variables = {"variantId": variant_id, "stocks": stocks}
    response = staff_api_client.post_graphql(
        VARIANT_STOCKS_CREATE_MUTATION,
        variables,
        permissions=[permission_manage_products],
    )
    content = get_graphql_content(response)
    data = content["data"]["productVariantStocksCreate"]
    errors = data["errors"]

    assert errors
    assert errors[0]["code"] == StockErrorCode.UNIQUE.name
    assert errors[0]["field"] == "warehouse"
    assert errors[0]["index"] == 2


def test_variant_stocks_create_stock_duplicated_warehouse_and_warehouse_already_exists(
    staff_api_client, variant, warehouse, permission_manage_products
):
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.pk)
    second_warehouse = Warehouse.objects.get(pk=warehouse.pk)
    second_warehouse.slug = "second warehouse"
    second_warehouse.pk = None
    second_warehouse.save()

    second_warehouse_id = graphene.Node.to_global_id("Warehouse", second_warehouse.id)
    Stock.objects.create(
        product_variant=variant, warehouse=second_warehouse, quantity=10
    )

    stocks = [
        {
            "warehouse": graphene.Node.to_global_id("Warehouse", warehouse.id),
            "quantity": 20,
        },
        {"warehouse": second_warehouse_id, "quantity": 100},
        {"warehouse": second_warehouse_id, "quantity": 120},
    ]

    variables = {"variantId": variant_id, "stocks": stocks}
    response = staff_api_client.post_graphql(
        VARIANT_STOCKS_CREATE_MUTATION,
        variables,
        permissions=[permission_manage_products],
    )
    content = get_graphql_content(response)
    data = content["data"]["productVariantStocksCreate"]
    errors = data["errors"]

    assert len(errors) == 3
    assert {error["code"] for error in errors} == {
        StockErrorCode.UNIQUE.name,
    }
    assert {error["field"] for error in errors} == {
        "warehouse",
    }
    assert {error["index"] for error in errors} == {1, 2}


VARIANT_UPDATE_AND_STOCKS_CREATE_MUTATION = """
  fragment ProductVariant on ProductVariant {
    id
    name
    stocks {
      quantity
      warehouse {
        id
        name
      }
    }
  }

  mutation VariantUpdate($id: ID!, $stocks: [StockInput!]!) {
    productVariantUpdate(id: $id, input: {}) {
      productVariant {
        ...ProductVariant
      }
    }
    productVariantStocksCreate(variantId: $id, stocks: $stocks) {
      productVariant {
        ...ProductVariant
      }
    }
  }
"""


def test_invalidate_stocks_dataloader_on_create_stocks(
    staff_api_client, variant_with_many_stocks, permission_manage_products
):
    # given
    variant = variant_with_many_stocks
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.pk)
    warehouse_ids = [
        graphene.Node.to_global_id("Warehouse", stock.warehouse.id)
        for stock in variant_with_many_stocks.stocks.all()
    ]
    variant.stocks.all().delete()
    variables = {
        "id": variant_id,
        "stocks": [
            {"warehouse": warehouse_id, "quantity": 10}
            for warehouse_id in warehouse_ids
        ],
    }

    # when
    response = staff_api_client.post_graphql(
        VARIANT_UPDATE_AND_STOCKS_CREATE_MUTATION,
        variables=variables,
        permissions=(permission_manage_products,),
    )
    content = get_graphql_content(response)

    # then
    variant_data = content["data"]["productVariantUpdate"]["productVariant"]
    create_stocks_data = content["data"]["productVariantStocksCreate"]["productVariant"]

    # no stocks are present after the first mutation
    assert variant_data["stocks"] == []

    # stocks are returned in the second mutation, after dataloader invalidation
    assert len(create_stocks_data["stocks"]) == len(warehouse_ids)


def test_variant_stocks_create_owned_warehouse_rejected(
    staff_api_client, variant, warehouse, permission_manage_products
):
    # given - owned warehouse should not allow stock creation
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.pk)

    # Make the warehouse owned
    warehouse.is_owned = True
    warehouse.save()

    stocks = [
        {
            "warehouse": graphene.Node.to_global_id("Warehouse", warehouse.id),
            "quantity": 20,
        },
    ]
    variables = {"variantId": variant_id, "stocks": stocks}

    # when
    response = staff_api_client.post_graphql(
        VARIANT_STOCKS_CREATE_MUTATION,
        variables,
        permissions=[permission_manage_products],
    )
    content = get_graphql_content(response)
    data = content["data"]["productVariantStocksCreate"]

    # then
    assert data["errors"]
    assert data["errors"][0]["code"] == StockErrorCode.OWNED_WAREHOUSE.name
    assert "owned warehouse" in data["errors"][0]["message"].lower()
    # Stock should not have been created
    assert not Stock.objects.filter(
        product_variant=variant, warehouse=warehouse
    ).exists()


def test_variant_stocks_create_mixed_owned_and_non_owned_warehouses(
    staff_api_client, variant, warehouse, permission_manage_products
):
    # given - mix of owned and non-owned warehouses
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.pk)

    # Create a second warehouse and make it owned
    owned_warehouse = Warehouse.objects.get(pk=warehouse.pk)
    owned_warehouse.slug = "owned-warehouse"
    owned_warehouse.pk = None
    owned_warehouse.is_owned = True
    owned_warehouse.save()

    # First warehouse is non-owned (default)
    non_owned_warehouse = warehouse

    stocks = [
        {
            "warehouse": graphene.Node.to_global_id(
                "Warehouse", non_owned_warehouse.id
            ),
            "quantity": 20,
        },
        {
            "warehouse": graphene.Node.to_global_id("Warehouse", owned_warehouse.id),
            "quantity": 100,
        },
    ]
    variables = {"variantId": variant_id, "stocks": stocks}

    # when
    response = staff_api_client.post_graphql(
        VARIANT_STOCKS_CREATE_MUTATION,
        variables,
        permissions=[permission_manage_products],
    )
    content = get_graphql_content(response)
    data = content["data"]["productVariantStocksCreate"]
    errors = data["errors"]

    # then
    assert errors
    # Should have error for the owned warehouse
    owned_warehouse_errors = [e for e in errors if e["index"] == 1]
    assert len(owned_warehouse_errors) == 1
    assert owned_warehouse_errors[0]["code"] == StockErrorCode.OWNED_WAREHOUSE.name

    # Stock should have been created for non-owned warehouse
    assert Stock.objects.filter(
        product_variant=variant, warehouse=non_owned_warehouse
    ).exists()

    # Stock should NOT have been created for owned warehouse
    assert not Stock.objects.filter(
        product_variant=variant, warehouse=owned_warehouse
    ).exists()
