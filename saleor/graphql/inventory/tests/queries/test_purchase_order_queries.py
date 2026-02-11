"""Tests for purchase order and receipt queries."""

import graphene

from ....tests.utils import assert_no_permission, get_graphql_content

QUERY_PURCHASE_ORDER = """
query PurchaseOrder($id: ID!) {
    purchaseOrder(id: $id) {
        id
        supplierWarehouse {
            id
            name
        }
        destinationWarehouse {
            id
            name
        }
        items {
            id
            productVariant {
                id
            }
            quantityOrdered
            status
        }
    }
}
"""

QUERY_PURCHASE_ORDERS = """
query PurchaseOrders {
    purchaseOrders(first: 10) {
        edges {
            node {
                id
                supplierWarehouse {
                    name
                }
                destinationWarehouse {
                    name
                }
            }
        }
    }
}
"""


def test_query_purchase_order(
    staff_api_client, permission_manage_purchase_orders, purchase_order
):
    """Test querying a single purchase order."""
    # given
    variables = {"id": graphene.Node.to_global_id("PurchaseOrder", purchase_order.pk)}

    # when
    response = staff_api_client.post_graphql(
        QUERY_PURCHASE_ORDER,
        variables,
        permissions=[permission_manage_purchase_orders],
    )

    # then
    content = get_graphql_content(response)
    po_data = content["data"]["purchaseOrder"]
    assert po_data["id"] == variables["id"]
    assert po_data["supplierWarehouse"]["name"] == purchase_order.source_warehouse.name
    assert (
        po_data["destinationWarehouse"]["name"]
        == purchase_order.destination_warehouse.name
    )


def test_query_purchase_orders(
    staff_api_client, permission_manage_purchase_orders, purchase_order
):
    """Test querying list of purchase orders."""
    # when
    response = staff_api_client.post_graphql(
        QUERY_PURCHASE_ORDERS,
        permissions=[permission_manage_purchase_orders],
    )

    # then
    content = get_graphql_content(response)
    edges = content["data"]["purchaseOrders"]["edges"]
    assert len(edges) > 0
    assert edges[0]["node"]["supplierWarehouse"]["name"] is not None


def test_query_purchase_order_requires_permission(staff_api_client, purchase_order):
    """Test that querying purchase orders requires MANAGE_PRODUCTS permission."""
    # given
    variables = {"id": graphene.Node.to_global_id("PurchaseOrder", purchase_order.pk)}

    # when
    response = staff_api_client.post_graphql(QUERY_PURCHASE_ORDER, variables)

    # then
    assert_no_permission(response)
