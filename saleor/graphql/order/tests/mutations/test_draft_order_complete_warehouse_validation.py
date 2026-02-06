"""Tests for DraftOrderComplete warehouse ownership validation.

These tests ensure that draft orders are only auto-confirmed when allocations
are in owned warehouses with proper AllocationSources tracking.
"""

import graphene
import pytest

from .....order import OrderStatus
from .....warehouse.models import Allocation, AllocationSource, Stock
from ....tests.utils import get_graphql_content

DRAFT_ORDER_COMPLETE_MUTATION = """
    mutation draftComplete($id: ID!) {
        draftOrderComplete(id: $id) {
            errors {
                field
                code
                message
            }
            order {
                id
                status
            }
        }
    }
"""


@pytest.mark.django_db
@pytest.mark.count_queries(autouse=False)
def test_draft_order_complete_blocked_with_nonowned_warehouse(
    staff_api_client,
    draft_order,
    permission_group_manage_orders,
    nonowned_warehouse,
    channel_USD,
):
    """Draft order should NOT auto-confirm when allocations are in non-owned warehouse.

    Given:
    - A draft order with products
    - Channel has automatically_confirm_all_new_orders = True
    - Allocations are in a non-owned (supplier) warehouse

    When: Completing the draft order

    Then:
    - Order should transition to UNCONFIRMED (not UNFULFILLED)
    - No error should be raised (this is expected behavior)
    - Order must be manually confirmed later when stock arrives
    """
    # given
    order = draft_order
    channel = order.channel
    channel.automatically_confirm_all_new_orders = True
    channel.save(update_fields=["automatically_confirm_all_new_orders"])

    order_line = order.lines.first()
    variant = order_line.variant

    # Create stock in NON-OWNED warehouse
    stock = Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=100,
    )

    # Manually create allocation in non-owned warehouse (no AllocationSources)
    allocation = Allocation.objects.create(
        order_line=order_line,
        stock=stock,
        quantity_allocated=order_line.quantity,
    )

    # Update stock to reflect allocation
    stock.quantity_allocated = order_line.quantity
    stock.save(update_fields=["quantity_allocated"])

    # Verify allocation exists but in non-owned warehouse
    assert allocation.stock.warehouse.is_owned is False
    assert not AllocationSource.objects.filter(allocation=allocation).exists()

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order_id = graphene.Node.to_global_id("Order", order.id)

    # when
    response = staff_api_client.post_graphql(
        DRAFT_ORDER_COMPLETE_MUTATION,
        {"id": order_id},
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["draftOrderComplete"]

    # Should complete without errors
    assert not data["errors"]

    # But order should be UNCONFIRMED (not auto-confirmed)
    order.refresh_from_db()
    assert order.status == OrderStatus.UNCONFIRMED
    assert data["order"]["status"] == OrderStatus.UNCONFIRMED.upper()


@pytest.mark.django_db
@pytest.mark.count_queries(autouse=False)
def test_draft_order_complete_auto_confirms_with_owned_warehouse(
    staff_api_client,
    draft_order,
    permission_group_manage_orders,
    owned_warehouse,
    channel_USD,
    purchase_order_item,
):
    """Draft order SHOULD auto-confirm when allocations are in owned warehouse with sources.

    Given:
    - A draft order with products
    - Channel has automatically_confirm_all_new_orders = True
    - Allocations are in an owned warehouse
    - AllocationSources properly link allocations to PurchaseOrderItems

    When: Completing the draft order

    Then:
    - Order should transition to UNFULFILLED (auto-confirmed)
    - No manual confirmation step needed
    """
    # given
    order = draft_order
    channel = order.channel
    channel.automatically_confirm_all_new_orders = True
    channel.save(update_fields=["automatically_confirm_all_new_orders"])

    order_line = order.lines.first()
    variant = order_line.variant

    # Update POI to match the variant in the order
    purchase_order_item.product_variant = variant
    purchase_order_item.order.destination_warehouse = owned_warehouse
    purchase_order_item.order.save(update_fields=["destination_warehouse"])
    purchase_order_item.quantity_ordered = order_line.quantity * 2  # Buffer
    purchase_order_item.save(update_fields=["product_variant", "quantity_ordered"])

    # Create stock in OWNED warehouse
    stock, _ = Stock.objects.get_or_create(
        warehouse=owned_warehouse,
        product_variant=variant,
        defaults={"quantity": 1000, "quantity_allocated": 0},
    )

    # Manually create allocation in owned warehouse
    allocation = Allocation.objects.create(
        order_line=order_line,
        stock=stock,
        quantity_allocated=order_line.quantity,
    )

    # Create AllocationSource linking to POI
    AllocationSource.objects.create(
        allocation=allocation,
        purchase_order_item=purchase_order_item,
        quantity=order_line.quantity,
    )

    # Update stock to reflect allocation
    stock.quantity_allocated = order_line.quantity
    stock.save(update_fields=["quantity_allocated"])

    # Verify allocation exists in owned warehouse with sources
    assert allocation.stock.warehouse.is_owned is True
    allocation_sources = AllocationSource.objects.filter(allocation=allocation)
    assert allocation_sources.exists()
    total_sourced = sum(src.quantity for src in allocation_sources)
    assert total_sourced == allocation.quantity_allocated

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order_id = graphene.Node.to_global_id("Order", order.id)

    # when
    response = staff_api_client.post_graphql(
        DRAFT_ORDER_COMPLETE_MUTATION,
        {"id": order_id},
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["draftOrderComplete"]

    # Should complete without errors
    assert not data["errors"]

    # Order should be UNFULFILLED (auto-confirmed)
    order.refresh_from_db()
    assert order.status == OrderStatus.UNFULFILLED
    assert data["order"]["status"] == OrderStatus.UNFULFILLED.upper()


@pytest.mark.django_db
@pytest.mark.count_queries(autouse=False)
def test_draft_order_complete_respects_channel_setting_disabled(
    staff_api_client,
    draft_order,
    permission_group_manage_orders,
    owned_warehouse,
    channel_USD,
    purchase_order_item,
):
    """Draft order should NOT auto-confirm when channel setting is disabled.

    Given:
    - A draft order with products
    - Channel has automatically_confirm_all_new_orders = False
    - Allocations ARE in owned warehouse (would normally allow confirmation)

    When: Completing the draft order

    Then:
    - Order should transition to UNCONFIRMED
    - Respects the channel setting even though technically confirmable
    """
    # given
    order = draft_order
    channel = order.channel
    channel.automatically_confirm_all_new_orders = False
    channel.save(update_fields=["automatically_confirm_all_new_orders"])

    order_line = order.lines.first()
    variant = order_line.variant

    # Set up properly with owned warehouse
    purchase_order_item.product_variant = variant
    purchase_order_item.order.destination_warehouse = owned_warehouse
    purchase_order_item.order.save(update_fields=["destination_warehouse"])
    purchase_order_item.quantity_ordered = order_line.quantity * 2
    purchase_order_item.save(update_fields=["product_variant", "quantity_ordered"])

    stock, _ = Stock.objects.get_or_create(
        warehouse=owned_warehouse,
        product_variant=variant,
        defaults={"quantity": 1000},
    )

    # Manually create allocation with sources
    allocation = Allocation.objects.create(
        order_line=order_line,
        stock=stock,
        quantity_allocated=order_line.quantity,
    )

    AllocationSource.objects.create(
        allocation=allocation,
        purchase_order_item=purchase_order_item,
        quantity=order_line.quantity,
    )

    stock.quantity_allocated = order_line.quantity
    stock.save(update_fields=["quantity_allocated"])

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order_id = graphene.Node.to_global_id("Order", order.id)

    # when
    response = staff_api_client.post_graphql(
        DRAFT_ORDER_COMPLETE_MUTATION,
        {"id": order_id},
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["draftOrderComplete"]

    assert not data["errors"]

    # Order should be UNCONFIRMED (channel setting disabled)
    order.refresh_from_db()
    assert order.status == OrderStatus.UNCONFIRMED
    assert data["order"]["status"] == OrderStatus.UNCONFIRMED.upper()


@pytest.mark.django_db
@pytest.mark.count_queries(autouse=False)
def test_draft_order_complete_mixed_owned_nonowned_blocked(
    staff_api_client,
    draft_order,
    permission_group_manage_orders,
    owned_warehouse,
    nonowned_warehouse,
    channel_USD,
    purchase_order_item,
    product,
):
    """Draft order should NOT auto-confirm when ANY allocation is in non-owned warehouse.

    Given:
    - A draft order with multiple line items
    - Some allocations in owned warehouse (with sources)
    - At least one allocation in non-owned warehouse
    - Channel has automatically_confirm_all_new_orders = True

    When: Completing the draft order

    Then:
    - Order should transition to UNCONFIRMED
    - Even one non-owned allocation blocks auto-confirmation
    """
    # given
    order = draft_order
    channel = order.channel
    channel.automatically_confirm_all_new_orders = True
    channel.save(update_fields=["automatically_confirm_all_new_orders"])

    # Use the first 2 lines from the draft order (draft_order has multiple lines by default)
    lines = list(order.lines.all()[:2])
    assert len(lines) >= 2, "Need at least 2 lines for this test"

    # First line: allocate in OWNED warehouse (good)
    line1 = lines[0]
    variant1 = line1.variant
    purchase_order_item.product_variant = variant1
    purchase_order_item.order.destination_warehouse = owned_warehouse
    purchase_order_item.order.save()
    purchase_order_item.quantity_ordered = line1.quantity * 2
    purchase_order_item.save(update_fields=["product_variant", "quantity_ordered"])

    stock1 = Stock.objects.create(
        warehouse=owned_warehouse,
        product_variant=variant1,
        quantity=1000,
    )

    alloc1 = Allocation.objects.create(
        order_line=line1,
        stock=stock1,
        quantity_allocated=line1.quantity,
    )

    AllocationSource.objects.create(
        allocation=alloc1,
        purchase_order_item=purchase_order_item,
        quantity=line1.quantity,
    )

    stock1.quantity_allocated = line1.quantity
    stock1.save(update_fields=["quantity_allocated"])

    # Second line: allocate in NON-OWNED warehouse (blocks confirmation)
    line2 = lines[1]
    variant2 = line2.variant

    stock2 = Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant2,
        quantity=1000,
    )

    alloc2 = Allocation.objects.create(
        order_line=line2,
        stock=stock2,
        quantity_allocated=line2.quantity,
    )

    stock2.quantity_allocated = line2.quantity
    stock2.save(update_fields=["quantity_allocated"])

    # Verify setup: one owned, one non-owned
    assert alloc1.stock.warehouse.is_owned is True
    assert alloc2.stock.warehouse.is_owned is False
    assert AllocationSource.objects.filter(allocation=alloc1).exists()
    assert not AllocationSource.objects.filter(allocation=alloc2).exists()

    permission_group_manage_orders.user_set.add(staff_api_client.user)
    order_id = graphene.Node.to_global_id("Order", order.id)

    # when
    response = staff_api_client.post_graphql(
        DRAFT_ORDER_COMPLETE_MUTATION,
        {"id": order_id},
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["draftOrderComplete"]

    assert not data["errors"]

    # Order should be UNCONFIRMED (blocked by non-owned allocation)
    order.refresh_from_db()
    assert order.status == OrderStatus.UNCONFIRMED
    assert data["order"]["status"] == OrderStatus.UNCONFIRMED.upper()
