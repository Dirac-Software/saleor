from saleor.graphql.tests.utils import get_graphql_content

ORDER_UPDATE_SHIPPING_COST_MUTATION = """
mutation OrderUpdateShippingCost($id: ID!, $input: OrderUpdateShippingCostInput!) {
  orderUpdateShippingCost(id: $id, input: $input) {
    errors {
      message
      field
      code
    }
    order {
      id
      shippingPrice {
        ...BaseTaxedMoney
      }
      total {
        ...BaseTaxedMoney
      }
    }
  }
}

fragment BaseTaxedMoney on TaxedMoney {
  gross {
    amount
  }
  net {
    amount
  }
  tax {
    amount
  }
  currency
}
"""


def order_update_shipping_cost(
    api_client,
    id,
    input,
):
    variables = {"id": id, "input": input}

    response = api_client.post_graphql(
        ORDER_UPDATE_SHIPPING_COST_MUTATION,
        variables=variables,
    )
    content = get_graphql_content(response)
    data = content["data"]["orderUpdateShippingCost"]
    order_id = data["order"]["id"]
    errors = data["errors"]

    assert errors == []
    assert order_id == id

    return data
