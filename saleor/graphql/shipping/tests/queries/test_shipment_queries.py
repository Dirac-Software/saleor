"""Tests for shipment queries."""

import graphene

from ....tests.utils import assert_no_permission, get_graphql_content

QUERY_SHIPMENT = """
query Shipment($id: ID!) {
    shipment(id: $id) {
        id
        carrier
        trackingNumber
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
                trackingNumber
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
