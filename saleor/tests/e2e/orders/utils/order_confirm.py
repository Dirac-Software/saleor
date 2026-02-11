from ...utils import get_graphql_content

ORDER_CONFIRM_MUTATION = """
mutation OrderConfirm($id: ID!) {
  orderConfirm(id: $id) {
    errors {
      field
      message
      code
    }
    order {
      id
      status
      total {
        gross {
          amount
        }
        net {
          amount
        }
        tax {
          amount
        }
      }
      subtotal {
        gross {
          amount
        }
        net {
          amount
        }
        tax {
          amount
        }
      }
      shippingPrice {
        gross {
          amount
        }
        tax {
          amount
        }
      }
    }
  }
}
"""


def order_confirm(api_client, order_id):
    variables = {"id": order_id}
    response = api_client.post_graphql(ORDER_CONFIRM_MUTATION, variables)
    content = get_graphql_content(response)
    data = content["data"]["orderConfirm"]
    assert data["errors"] == [], f"Order confirm failed with errors: {data['errors']}"
    return data
