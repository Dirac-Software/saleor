import graphene
import pytest

from .....inventory import PurchaseOrderItemStatus
from .....inventory.error_codes import PurchaseOrderErrorCode
from .....inventory.models import PurchaseOrder, PurchaseOrderItem
from ....tests.utils import assert_no_permission, get_graphql_content

MUTATION_DELETE_PURCHASE_ORDER = """
mutation deletePurchaseOrder($id: ID!) {
    deletePurchaseOrder(id: $id) {
        purchaseOrder {
            id
        }
        purchaseOrderErrors {
            field
            code
            message
        }
    }
}
"""


def test_delete_draft_purchase_order_success(
    staff_api_client, permission_manage_purchase_orders, draft_purchase_order
):
    # given
    purchase_order = draft_purchase_order
    po_id = graphene.Node.to_global_id("PurchaseOrder", purchase_order.id)
    item_count = purchase_order.items.count()

    assert item_count > 0

    # when
    response = staff_api_client.post_graphql(
        MUTATION_DELETE_PURCHASE_ORDER,
        variables={"id": po_id},
        permissions=[permission_manage_purchase_orders],
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["deletePurchaseOrder"]

    assert not data["purchaseOrderErrors"]
    assert data["purchaseOrder"]["id"] == po_id
    assert not PurchaseOrder.objects.filter(id=purchase_order.id).exists()
    assert not PurchaseOrderItem.objects.filter(order=purchase_order).exists()


def test_delete_purchase_order_with_confirmed_items_fails(
    staff_api_client,
    permission_manage_purchase_orders,
    draft_purchase_order,
):
    # given
    purchase_order = draft_purchase_order
    item = purchase_order.items.first()
    item.status = PurchaseOrderItemStatus.CONFIRMED
    item.save()

    po_id = graphene.Node.to_global_id("PurchaseOrder", purchase_order.id)

    # when
    response = staff_api_client.post_graphql(
        MUTATION_DELETE_PURCHASE_ORDER,
        variables={"id": po_id},
        permissions=[permission_manage_purchase_orders],
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["deletePurchaseOrder"]

    assert len(data["purchaseOrderErrors"]) == 1
    error = data["purchaseOrderErrors"][0]
    assert error["code"] == PurchaseOrderErrorCode.INVALID.name
    assert error["field"] == "id"
    assert "confirmed" in error["message"].lower()
    assert PurchaseOrder.objects.filter(id=purchase_order.id).exists()


def test_delete_purchase_order_with_received_items_fails(
    staff_api_client,
    permission_manage_purchase_orders,
    draft_purchase_order,
):
    # given
    purchase_order = draft_purchase_order
    item = purchase_order.items.first()
    item.status = PurchaseOrderItemStatus.RECEIVED
    item.save()

    po_id = graphene.Node.to_global_id("PurchaseOrder", purchase_order.id)

    # when
    response = staff_api_client.post_graphql(
        MUTATION_DELETE_PURCHASE_ORDER,
        variables={"id": po_id},
        permissions=[permission_manage_purchase_orders],
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["deletePurchaseOrder"]

    assert len(data["purchaseOrderErrors"]) == 1
    error = data["purchaseOrderErrors"][0]
    assert error["code"] == PurchaseOrderErrorCode.INVALID.name
    assert "received" in error["message"].lower()
    assert PurchaseOrder.objects.filter(id=purchase_order.id).exists()


def test_delete_purchase_order_not_found(
    staff_api_client, permission_manage_purchase_orders
):
    # given
    non_existent_id = graphene.Node.to_global_id("PurchaseOrder", 99999)

    # when
    response = staff_api_client.post_graphql(
        MUTATION_DELETE_PURCHASE_ORDER,
        variables={"id": non_existent_id},
        permissions=[permission_manage_purchase_orders],
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["deletePurchaseOrder"]

    assert len(data["purchaseOrderErrors"]) == 1
    error = data["purchaseOrderErrors"][0]
    assert error["code"] == PurchaseOrderErrorCode.NOT_FOUND.name
    assert error["field"] == "id"


def test_delete_purchase_order_no_permission(
    staff_api_client, draft_purchase_order
):
    # given
    po_id = graphene.Node.to_global_id("PurchaseOrder", draft_purchase_order.id)

    # when
    response = staff_api_client.post_graphql(
        MUTATION_DELETE_PURCHASE_ORDER,
        variables={"id": po_id},
    )

    # then
    assert_no_permission(response)
    assert PurchaseOrder.objects.filter(id=draft_purchase_order.id).exists()
