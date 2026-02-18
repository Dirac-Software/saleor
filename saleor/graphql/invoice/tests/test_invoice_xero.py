import graphene

from ....invoice import InvoiceType
from ....invoice.models import Invoice
from ....order.error_codes import OrderErrorCode
from ...tests.utils import get_graphql_content

INVOICE_CREATE_FINAL_MUTATION = """
    mutation InvoiceCreateFinal(
        $fulfillmentId: ID!
        $xeroInvoiceId: String!
        $invoiceNumber: String
    ) {
        invoiceCreateFinal(
            fulfillmentId: $fulfillmentId
            xeroInvoiceId: $xeroInvoiceId
            invoiceNumber: $invoiceNumber
        ) {
            invoice {
                id
                xeroInvoiceId
                type
                number
                fulfillment {
                    id
                }
                order {
                    id
                }
            }
            errors {
                field
                code
                message
            }
        }
    }
"""

INVOICE_BY_XERO_ID_QUERY = """
    query InvoiceByXeroId($xeroInvoiceId: String!) {
        invoiceByXeroId(xeroInvoiceId: $xeroInvoiceId) {
            id
            xeroInvoiceId
            type
            fulfillment {
                id
            }
            order {
                id
            }
        }
    }
"""


def test_invoice_create_final(
    staff_api_client, permission_group_manage_orders, fulfillment
):
    """Test creating a final invoice record for a fulfillment."""
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    fulfillment_id = graphene.Node.to_global_id("Fulfillment", fulfillment.id)
    xero_invoice_id = "INV-XERO-123"
    invoice_number = "INV-001"

    response = staff_api_client.post_graphql(
        INVOICE_CREATE_FINAL_MUTATION,
        {
            "fulfillmentId": fulfillment_id,
            "xeroInvoiceId": xero_invoice_id,
            "invoiceNumber": invoice_number,
        },
    )

    content = get_graphql_content(response)
    data = content["data"]["invoiceCreateFinal"]
    assert not data["errors"]
    assert data["invoice"]["xeroInvoiceId"] == xero_invoice_id
    assert data["invoice"]["type"] == "FINAL"
    assert data["invoice"]["number"] == invoice_number
    assert data["invoice"]["fulfillment"]["id"] == fulfillment_id

    # Verify record created
    invoice = Invoice.objects.get(xero_invoice_id=xero_invoice_id)
    assert invoice.fulfillment == fulfillment
    assert invoice.order == fulfillment.order
    assert invoice.type == InvoiceType.FINAL


def test_invoice_create_final_duplicate_xero_id(
    staff_api_client, permission_group_manage_orders, fulfillment
):
    """Test error when creating invoice with duplicate Xero ID."""
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    fulfillment_id = graphene.Node.to_global_id("Fulfillment", fulfillment.id)
    xero_invoice_id = "INV-XERO-123"

    # Create first invoice
    Invoice.objects.create(
        order=fulfillment.order,
        fulfillment=fulfillment,
        xero_invoice_id=xero_invoice_id,
        type=InvoiceType.FINAL,
    )

    # Try to create duplicate
    response = staff_api_client.post_graphql(
        INVOICE_CREATE_FINAL_MUTATION,
        {
            "fulfillmentId": fulfillment_id,
            "xeroInvoiceId": xero_invoice_id,
        },
    )

    content = get_graphql_content(response)
    errors = content["data"]["invoiceCreateFinal"]["errors"]
    assert len(errors) == 1
    assert "already exists" in errors[0]["message"]
    assert errors[0]["code"] == OrderErrorCode.UNIQUE.name


def test_invoice_by_xero_id(
    staff_api_client, permission_group_manage_orders, fulfillment
):
    """Test querying invoice by Xero invoice ID."""
    permission_group_manage_orders.user_set.add(staff_api_client.user)
    xero_invoice_id = "INV-XERO-456"

    # Create invoice
    Invoice.objects.create(
        order=fulfillment.order,
        fulfillment=fulfillment,
        xero_invoice_id=xero_invoice_id,
        type=InvoiceType.FINAL,
    )

    response = staff_api_client.post_graphql(
        INVOICE_BY_XERO_ID_QUERY,
        {"xeroInvoiceId": xero_invoice_id},
    )

    content = get_graphql_content(response)
    data = content["data"]["invoiceByXeroId"]
    assert data["xeroInvoiceId"] == xero_invoice_id
    assert data["type"] == "FINAL"
    assert data["fulfillment"]["id"] == graphene.Node.to_global_id(
        "Fulfillment", fulfillment.id
    )
    assert data["order"]["id"] == graphene.Node.to_global_id(
        "Order", fulfillment.order.id
    )


def test_invoice_by_xero_id_not_found(staff_api_client, permission_group_manage_orders):
    """Test querying non-existent invoice returns None."""
    permission_group_manage_orders.user_set.add(staff_api_client.user)

    response = staff_api_client.post_graphql(
        INVOICE_BY_XERO_ID_QUERY,
        {"xeroInvoiceId": "NON-EXISTENT"},
    )

    content = get_graphql_content(response)
    assert content["data"]["invoiceByXeroId"] is None


def test_invoice_by_xero_id_no_permission(staff_api_client):
    """Test querying invoice without permission returns None."""
    response = staff_api_client.post_graphql(
        INVOICE_BY_XERO_ID_QUERY,
        {"xeroInvoiceId": "INV-123"},
    )

    content = get_graphql_content(response)
    assert content["data"]["invoiceByXeroId"] is None
