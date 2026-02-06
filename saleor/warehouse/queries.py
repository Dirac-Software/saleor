"""Warehouse-related query utilities for filtering orders by inventory status."""

from django.db.models import Exists, F, OuterRef, Q, QuerySet, Subquery, Sum

from .models import Allocation, AllocationSource


def filter_orders_with_inventory_ready(orders_qs: QuerySet) -> QuerySet:
    """Filter orders to only those where all inventory has arrived in owned warehouses.

    Returns orders where:
    - All allocations are in owned warehouses (is_owned=True)
    - All allocations have AllocationSources (inventory received from POs)
    - AllocationSources quantities match allocation quantities
    - All PurchaseOrderItems have been physically received (quantity_received > 0)
    - All PurchaseOrderItemAdjustments have been processed (processed_at IS NOT NULL)

    Args:
        orders_qs: QuerySet of Order objects to filter

    Returns:
        Filtered QuerySet of orders that are ready to fulfill from inventory perspective

    Example:
        # Find unconfirmed orders with inventory ready
        ready_orders = filter_orders_with_inventory_ready(
            Order.objects.filter(status=OrderStatus.UNCONFIRMED)
        )

        # Find unfulfilled orders with inventory ready
        ready_orders = filter_orders_with_inventory_ready(
            Order.objects.filter(status=OrderStatus.UNFULFILLED)
        )
    """
    # Subquery: Orders with allocation violations
    orders_with_violations = (
        Allocation.objects.filter(order_line__order_id=OuterRef("id"))
        .annotate(total_sourced=Sum("allocation_sources__quantity"))
        .filter(
            Q(stock__warehouse__is_owned=False)  # Non-owned warehouse
            | Q(total_sourced__isnull=True)  # No sources (not received)
            | ~Q(total_sourced=F("quantity_allocated"))  # Quantity mismatch
            # Check if any AllocationSource has POI with no receipts or quantity_received = 0
            | Exists(
                AllocationSource.objects.filter(
                    allocation_id=OuterRef('id')
                ).annotate(
                    poi_received=Sum('purchase_order_item__receipt_lines__quantity_received')
                ).filter(
                    Q(poi_received__isnull=True) | Q(poi_received__lte=0)
                )
            )
            # Check if any AllocationSource has POI with unprocessed adjustments
            | Exists(
                AllocationSource.objects.filter(
                    allocation_id=OuterRef('id'),
                    purchase_order_item__adjustments__processed_at__isnull=True
                )
            )
        )
        .values("order_line__order_id")
        .distinct()
    )

    # Subquery: Orders with at least one allocation
    orders_with_allocations = (
        Allocation.objects.filter(order_line__order_id=OuterRef("id"))
        .values("order_line__order_id")
        .distinct()
    )

    return (
        orders_qs.filter(
            # Must have allocations
            id__in=Subquery(orders_with_allocations)
        ).exclude(
            # Must not have violations
            id__in=Subquery(orders_with_violations)
        )
    )


def get_orders_missing_inventory(orders_qs: QuerySet) -> QuerySet:
    """Filter orders to only those waiting for inventory to arrive.

    Returns orders where:
    - Has allocations in non-owned warehouses, OR
    - Has allocations without AllocationSources (not yet received), OR
    - Has AllocationSources quantity mismatches, OR
    - Has PurchaseOrderItems that haven't been received (quantity_received = 0), OR
    - Has PurchaseOrderItems with unprocessed adjustments (processed_at IS NULL)

    This is the inverse of filter_orders_with_inventory_ready().

    Args:
        orders_qs: QuerySet of Order objects to filter

    Returns:
        Filtered QuerySet of orders waiting for inventory

    Example:
        # Find unconfirmed orders still waiting for inventory
        waiting_orders = get_orders_missing_inventory(
            Order.objects.filter(status=OrderStatus.UNCONFIRMED)
        )
    """
    # Orders with allocation violations
    orders_with_violations = (
        Allocation.objects.filter(order_line__order_id=OuterRef("id"))
        .annotate(total_sourced=Sum("allocation_sources__quantity"))
        .filter(
            Q(stock__warehouse__is_owned=False)
            | Q(total_sourced__isnull=True)
            | ~Q(total_sourced=F("quantity_allocated"))
            # Check if any AllocationSource has POI with no receipts or quantity_received = 0
            | Exists(
                AllocationSource.objects.filter(
                    allocation_id=OuterRef('id')
                ).annotate(
                    poi_received=Sum('purchase_order_item__receipt_lines__quantity_received')
                ).filter(
                    Q(poi_received__isnull=True) | Q(poi_received__lte=0)
                )
            )
            # Check if any AllocationSource has POI with unprocessed adjustments
            | Exists(
                AllocationSource.objects.filter(
                    allocation_id=OuterRef('id'),
                    purchase_order_item__adjustments__processed_at__isnull=True
                )
            )
        )
        .values("order_line__order_id")
        .distinct()
    )

    return orders_qs.filter(id__in=Subquery(orders_with_violations))
