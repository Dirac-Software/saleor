"""Tests for receipt queries."""

import graphene

from ....tests.utils import assert_no_permission, get_graphql_content

QUERY_RECEIPT = """
query Receipt($id: ID!) {
    receipt(id: $id) {
        id
        status
        shipment {
            id
            trackingNumber
        }
        lines {
            id
            quantityReceived
            purchaseOrderItem {
                id
            }
        }
        createdAt
        completedAt
    }
}
"""

QUERY_RECEIPTS = """
query Receipts {
    receipts(first: 10) {
        edges {
            node {
                id
                status
                shipment {
                    trackingNumber
                }
                createdAt
            }
        }
    }
}
"""


def test_query_receipt(staff_api_client, permission_manage_products, receipt):
    """Test querying a single receipt."""
    # given
    variables = {"id": graphene.Node.to_global_id("Receipt", receipt.pk)}

    # when
    response = staff_api_client.post_graphql(
        QUERY_RECEIPT,
        variables,
        permissions=[permission_manage_products],
    )

    # then
    content = get_graphql_content(response)
    receipt_data = content["data"]["receipt"]
    assert receipt_data["id"] == variables["id"]
    assert receipt_data["status"] == receipt.status.upper()
    assert receipt_data["shipment"]["id"] is not None


def test_query_receipts(staff_api_client, permission_manage_products, receipt):
    """Test querying list of receipts."""
    # when
    response = staff_api_client.post_graphql(
        QUERY_RECEIPTS,
        permissions=[permission_manage_products],
    )

    # then
    content = get_graphql_content(response)
    edges = content["data"]["receipts"]["edges"]
    assert len(edges) > 0
    assert edges[0]["node"]["status"] is not None


def test_query_receipt_requires_permission(staff_api_client, receipt):
    """Test that querying receipts requires MANAGE_PRODUCTS permission."""
    # given
    variables = {"id": graphene.Node.to_global_id("Receipt", receipt.pk)}

    # when
    response = staff_api_client.post_graphql(QUERY_RECEIPT, variables)

    # then
    assert_no_permission(response)
