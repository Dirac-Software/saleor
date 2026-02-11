"""Tests for shipment queries."""

import graphene

from ....tests.utils import assert_no_permission, get_graphql_content

QUERY_SHIPMENT = """
query Shipment($id: ID!) {
    shipment(id: $id) {
        id
        carrier
        trackingUrl
        source {
            id
        }
        destination {
            id
        }
        purchaseOrderItems {
            id
        }
        receipt {
            id
            status
        }
        arrivedAt
    }
}
"""

QUERY_SHIPMENTS = """
query Shipments {
    shipments(first: 10) {
        edges {
            node {
                id
                carrier
                trackingUrl
                arrivedAt
            }
        }
    }
}
"""


def test_query_shipment(staff_api_client, permission_manage_shipping, shipment):
    """Test querying a single shipment."""
    # given
    variables = {"id": graphene.Node.to_global_id("Shipment", shipment.pk)}

    # when
    response = staff_api_client.post_graphql(
        QUERY_SHIPMENT,
        variables,
        permissions=[permission_manage_shipping],
    )

    # then
    content = get_graphql_content(response)
    shipment_data = content["data"]["shipment"]
    assert shipment_data["id"] == variables["id"]
    assert shipment_data["source"]["id"] is not None
    assert shipment_data["destination"]["id"] is not None


def test_query_shipments(staff_api_client, permission_manage_shipping, shipment):
    """Test querying list of shipments."""
    # when
    response = staff_api_client.post_graphql(
        QUERY_SHIPMENTS,
        permissions=[permission_manage_shipping],
    )

    # then
    content = get_graphql_content(response)
    edges = content["data"]["shipments"]["edges"]
    assert len(edges) > 0
    assert edges[0]["node"]["id"] is not None


def test_query_shipment_requires_permission(staff_api_client, shipment):
    """Test that querying shipments requires MANAGE_SHIPPING permission."""
    # given
    variables = {"id": graphene.Node.to_global_id("Shipment", shipment.pk)}

    # when
    response = staff_api_client.post_graphql(QUERY_SHIPMENT, variables)

    # then
    assert_no_permission(response)


def test_query_shipment_purchase_order_items_with_purchase_order(
    staff_api_client, permission_manage_shipping, purchase_order_item
):
    """Test querying purchase order items with their parent purchase order.

    This tests the fix for the bug where PurchaseOrderItem.purchaseOrder
    was returning null because there was no resolver mapping the GraphQL
    field to the database 'order' field.
    """
    shipment = purchase_order_item.shipment
    purchase_order = purchase_order_item.order

    query = """
    query ShipmentDetails($id: ID!) {
        shipment(id: $id) {
            purchaseOrderItems {
                id
                purchaseOrder {
                    id
                }
            }
        }
    }
    """

    variables = {"id": graphene.Node.to_global_id("Shipment", shipment.pk)}

    response = staff_api_client.post_graphql(
        query,
        variables,
        permissions=[permission_manage_shipping],
    )

    content = get_graphql_content(response)
    shipment_data = content["data"]["shipment"]

    assert len(shipment_data["purchaseOrderItems"]) == 1
    po_item = shipment_data["purchaseOrderItems"][0]
    assert po_item["purchaseOrder"] is not None
    assert po_item["purchaseOrder"]["id"] == graphene.Node.to_global_id(
        "PurchaseOrder", purchase_order.pk
    )
