import graphene

from .....product.error_codes import ProductErrorCode
from .....warehouse.models import Stock, Warehouse
from ....tests.utils import get_graphql_content

VARIANT_STOCKS_DELETE_MUTATION = """
    mutation ProductVariantStocksDelete($variantId: ID!, $warehouseIds: [ID!]!){
        productVariantStocksDelete(
            variantId: $variantId, warehouseIds: $warehouseIds
        ){
            productVariant{
                stocks{
                    id
                    quantity
                    warehouse{
                        slug
                    }
                }
            }
            errors{
                field
                code
                message
            }
        }
    }
"""


def test_product_variant_stocks_delete_mutation(
    staff_api_client, variant, warehouse, permission_manage_products
):
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.pk)
    second_warehouse = Warehouse.objects.get(pk=warehouse.pk)
    second_warehouse.slug = "second warehouse"
    second_warehouse.pk = None
    second_warehouse.save()

    Stock.objects.bulk_create(
        [
            Stock(product_variant=variant, warehouse=warehouse, quantity=10),
            Stock(product_variant=variant, warehouse=second_warehouse, quantity=140),
        ]
    )
    stocks_count = variant.stocks.count()

    warehouse_ids = [graphene.Node.to_global_id("Warehouse", second_warehouse.id)]

    variables = {"variantId": variant_id, "warehouseIds": warehouse_ids}
    response = staff_api_client.post_graphql(
        VARIANT_STOCKS_DELETE_MUTATION,
        variables,
        permissions=[permission_manage_products],
    )
    content = get_graphql_content(response)
    data = content["data"]["productVariantStocksDelete"]

    variant.refresh_from_db()
    assert not data["errors"]
    assert (
        len(data["productVariant"]["stocks"])
        == variant.stocks.count()
        == stocks_count - 1
    )
    assert data["productVariant"]["stocks"][0]["quantity"] == 10
    assert data["productVariant"]["stocks"][0]["warehouse"]["slug"] == warehouse.slug


def test_product_variant_stocks_delete_mutation_invalid_warehouse_id(
    staff_api_client, variant, warehouse, permission_manage_products
):
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.pk)
    second_warehouse = Warehouse.objects.get(pk=warehouse.pk)
    second_warehouse.slug = "second warehouse"
    second_warehouse.pk = None
    second_warehouse.save()

    Stock.objects.bulk_create(
        [Stock(product_variant=variant, warehouse=warehouse, quantity=10)]
    )
    stocks_count = variant.stocks.count()

    warehouse_ids = [graphene.Node.to_global_id("Warehouse", second_warehouse.id)]

    variables = {"variantId": variant_id, "warehouseIds": warehouse_ids}
    response = staff_api_client.post_graphql(
        VARIANT_STOCKS_DELETE_MUTATION,
        variables,
        permissions=[permission_manage_products],
    )
    content = get_graphql_content(response)
    data = content["data"]["productVariantStocksDelete"]

    variant.refresh_from_db()
    assert not data["errors"]
    assert (
        len(data["productVariant"]["stocks"]) == variant.stocks.count() == stocks_count
    )
    assert data["productVariant"]["stocks"][0]["quantity"] == 10
    assert data["productVariant"]["stocks"][0]["warehouse"]["slug"] == warehouse.slug


def test_product_variant_stocks_delete_mutation_invalid_object_type_of_warehouse_id(
    staff_api_client, variant, warehouse, permission_manage_products
):
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.pk)

    Stock.objects.bulk_create(
        [Stock(product_variant=variant, warehouse=warehouse, quantity=10)]
    )

    warehouse_ids = [graphene.Node.to_global_id("Product", warehouse.id)]

    variables = {"variantId": variant_id, "warehouseIds": warehouse_ids}
    response = staff_api_client.post_graphql(
        VARIANT_STOCKS_DELETE_MUTATION,
        variables,
        permissions=[permission_manage_products],
    )
    content = get_graphql_content(response)
    data = content["data"]["productVariantStocksDelete"]

    errors = data["errors"]
    assert len(errors) == 1
    assert errors[0]["code"] == ProductErrorCode.GRAPHQL_ERROR.name
    assert errors[0]["field"] == "warehouseIds"


VARIANT_UPDATE_AND_STOCKS_REMOVE_MUTATION = """
  fragment ProductVariant on ProductVariant {
    stocks {
      id
    }
  }

  mutation VariantUpdate($removeStocks: [ID!]!, $id: ID!) {
    productVariantUpdate(id: $id, input: {}) {
      productVariant {
        ...ProductVariant
      }
    }
    productVariantStocksDelete(variantId: $id, warehouseIds: $removeStocks) {
      productVariant {
        ...ProductVariant
      }
    }
  }
"""


def test_invalidate_stocks_dataloader_on_removing_stocks(
    staff_api_client, variant_with_many_stocks, permission_manage_products
):
    # given
    variant = variant_with_many_stocks
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.pk)
    warehouse_ids = [
        graphene.Node.to_global_id("Warehouse", stock.warehouse.id)
        for stock in variant_with_many_stocks.stocks.all()
    ]
    variables = {
        "id": variant_id,
        "removeStocks": warehouse_ids,
    }

    # when
    response = staff_api_client.post_graphql(
        VARIANT_UPDATE_AND_STOCKS_REMOVE_MUTATION,
        variables=variables,
        permissions=(permission_manage_products,),
    )
    content = get_graphql_content(response)

    # then
    variant_data = content["data"]["productVariantUpdate"]["productVariant"]
    remove_stocks_data = content["data"]["productVariantStocksDelete"]["productVariant"]

    # no stocks were removed in the first mutation
    assert len(variant_data["stocks"]) == len(warehouse_ids)

    # stocks are empty in the second mutation
    assert remove_stocks_data["stocks"] == []


def test_product_variant_stocks_delete_owned_warehouse_rejected(
    staff_api_client, variant, warehouse, permission_manage_products
):
    # given - owned warehouse should not allow stock deletion
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.pk)

    # Make the warehouse owned
    warehouse.is_owned = True
    warehouse.save()

    Stock.objects.create(product_variant=variant, warehouse=warehouse, quantity=10)

    warehouse_ids = [graphene.Node.to_global_id("Warehouse", warehouse.id)]

    variables = {"variantId": variant_id, "warehouseIds": warehouse_ids}

    # when
    response = staff_api_client.post_graphql(
        VARIANT_STOCKS_DELETE_MUTATION,
        variables,
        permissions=[permission_manage_products],
    )
    content = get_graphql_content(response)
    data = content["data"]["productVariantStocksDelete"]

    # then
    errors = data["errors"]
    assert errors
    assert errors[0]["code"] == "OWNED_WAREHOUSE"
    assert "owned warehouse" in errors[0]["message"].lower()

    # Stock should not have been deleted
    assert Stock.objects.filter(product_variant=variant, warehouse=warehouse).exists()


def test_product_variant_stocks_delete_mixed_owned_and_non_owned_warehouses(
    staff_api_client, variant, warehouse, permission_manage_products
):
    # given - mix of owned and non-owned warehouses
    # When attempting to delete both owned and non-owned stocks together,
    # the entire operation should fail to prevent accidental data loss
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.pk)

    # Create a second warehouse and make it owned
    owned_warehouse = Warehouse.objects.get(pk=warehouse.pk)
    owned_warehouse.slug = "owned-warehouse"
    owned_warehouse.pk = None
    owned_warehouse.is_owned = True
    owned_warehouse.save()

    # First warehouse is non-owned (default)
    non_owned_warehouse = warehouse

    Stock.objects.bulk_create(
        [
            Stock(product_variant=variant, warehouse=non_owned_warehouse, quantity=10),
            Stock(product_variant=variant, warehouse=owned_warehouse, quantity=140),
        ]
    )
    stocks_count = variant.stocks.count()
    assert stocks_count == 2

    warehouse_ids = [
        graphene.Node.to_global_id("Warehouse", non_owned_warehouse.id),
        graphene.Node.to_global_id("Warehouse", owned_warehouse.id),
    ]

    variables = {"variantId": variant_id, "warehouseIds": warehouse_ids}

    # when
    response = staff_api_client.post_graphql(
        VARIANT_STOCKS_DELETE_MUTATION,
        variables,
        permissions=[permission_manage_products],
    )
    content = get_graphql_content(response)
    data = content["data"]["productVariantStocksDelete"]

    # then
    errors = data["errors"]
    assert errors
    # Should have error for owned warehouse
    assert any("owned warehouse" in e["message"].lower() for e in errors)

    variant.refresh_from_db()

    # IMPORTANT: Entire operation should fail - no stocks should be deleted
    # This prevents accidental deletion of non-owned stocks when owned are included
    assert Stock.objects.filter(
        product_variant=variant, warehouse=non_owned_warehouse
    ).exists()

    assert Stock.objects.filter(
        product_variant=variant, warehouse=owned_warehouse
    ).exists()

    # Both stocks should still exist
    assert variant.stocks.count() == 2
