"""Stock management utilities for purchase orders and inventory tracking."""

from django.db import transaction
from django.utils import timezone

from ..core.exceptions import InsufficientStock
from ..warehouse.models import Stock
from . import PurchaseOrderItemStatus
from .exceptions import InvalidPurchaseOrderItemStatus
from .models import PurchaseOrderItem


@transaction.atomic
def confirm_purchase_order_item(purchase_order_item: PurchaseOrderItem):
    """Confirm purchase order item and move stock from supplier to owned warehouse.

    This is THE ONLY WAY stock enters owned warehouses. When a POI is confirmed,
    we move stock from the supplier (non-owned) warehouse to our owned warehouse.

    The function:
    1. Moves physical stock from source to destination
    2. Tries to attach existing allocations (if any) to the stock
    3. Creates AllocationSources to link allocations to this POI (batch tracking)

    See `saleor/warehouse/tests/test_stock_invariants.py` for description of how Stock
    must be updated and relevant tests
    """

    from ..warehouse.management import allocate_sources
    from ..warehouse.models import Allocation

    if purchase_order_item.status != PurchaseOrderItemStatus.DRAFT:
        raise InvalidPurchaseOrderItemStatus(
            purchase_order_item, PurchaseOrderItemStatus.DRAFT
        )

    # Get source and destination (locked via select_for_update)
    source = (
        Stock.objects.select_for_update()
        .select_related("warehouse")
        .get(
            warehouse=purchase_order_item.order.source_warehouse,
            product_variant=purchase_order_item.product_variant,
        )
    )

    destination, created = Stock.objects.select_for_update().get_or_create(
        warehouse=purchase_order_item.order.destination_warehouse,
        product_variant=purchase_order_item.product_variant,
        defaults={"quantity": 0, "quantity_allocated": 0},
    )

    quantity = purchase_order_item.quantity_ordered

    # Validation
    if source.warehouse.is_owned:
        raise ValueError("Source warehouse must be non-owned")
    if not destination.warehouse.is_owned:
        raise ValueError("Destination warehouse must be owned")
    if quantity > source.quantity + source.quantity_allocated:
        raise ValueError(
            f"Insufficient stock at source: need {quantity}, "
            f"have {source.quantity + source.quantity_allocated} "
            f"(quantity={source.quantity}, allocated={source.quantity_allocated})"
        )

    # Get allocations to potentially move (FIFO by order line creation time)
    allocations = (
        source.allocations.select_for_update()
        .select_related("order_line")
        .order_by("order_line__created_at")
    )

    # Collect orders to check for auto-confirmation (before deleting any allocations)
    from ..order import OrderStatus
    from ..warehouse.management import can_confirm_order

    orders_to_check = set()
    for allocation in allocations:
        order = allocation.order_line.order
        if order.status == OrderStatus.UNCONFIRMED:
            orders_to_check.add(order)

    # Move physical stock from source to destination
    # Note: Stock locks ensure these moves are isolated
    # Physical stock moves based on POI quantity; allocation tracking happens in loop below
    if quantity <= source.quantity:
        # Sufficient unallocated stock
        source.quantity -= quantity
    else:
        # Taking more than unallocated - remainder comes from allocated pool
        # Don't update quantity_allocated here; that happens when allocations move in loop
        source.quantity = 0

    destination.quantity += quantity

    # Confirm POI status before moving allocations
    # allocate_sources() needs POI to be CONFIRMED to find it
    purchase_order_item.status = PurchaseOrderItemStatus.CONFIRMED
    purchase_order_item.confirmed_at = timezone.now()
    purchase_order_item.save(update_fields=["status", "confirmed_at"])

    # Move allocations from source to destination and create AllocationSources
    for allocation in allocations:
        available = destination.quantity - destination.quantity_allocated
        if available >= allocation.quantity_allocated:
            # Move entire allocation to an owned warehouse
            allocation.stock = destination
            allocation.save(update_fields=["stock"])

            try:
                allocate_sources(allocation)
            except InsufficientStock:
                # Invariant violation - allocation moved but can't create sources
                # Transaction will rollback everything
                raise

            # Update quantity_allocated (quantity already moved)
            destination.quantity_allocated += allocation.quantity_allocated
            source.quantity_allocated -= allocation.quantity_allocated
        else:
            # Split allocation: partial to destination, rest stays at source
            available = destination.quantity - destination.quantity_allocated
            move_quantity = min(available, allocation.quantity_allocated)

            # Create new allocation at destination for the moved portion
            moved_allocation = Allocation.objects.create(
                order_line=allocation.order_line,
                stock=destination,
                quantity_allocated=move_quantity,
            )

            try:
                allocate_sources(moved_allocation)
            except InsufficientStock:
                # again this would break the stock invariant so raise it
                raise

            # Update quantity_allocated (quantity already moved)
            destination.quantity_allocated += move_quantity
            source.quantity_allocated -= move_quantity

            # Reduce original allocation (stays at source)
            allocation.quantity_allocated -= move_quantity
            if allocation.quantity_allocated == 0:
                allocation.delete()
            else:
                allocation.save(update_fields=["quantity_allocated"])

            # No more room at destination, stop moving allocations
            break

    source.save(update_fields=["quantity", "quantity_allocated"])
    destination.save(update_fields=["quantity", "quantity_allocated"])

    # Auto-confirm orders that now have all allocations with sources
    for order in orders_to_check:
        if can_confirm_order(order):
            order.status = OrderStatus.UNFULFILLED
            order.save(update_fields=["status", "updated_at"])

    return source


@transaction.atomic
def receive_purchase_order_item(
    purchase_order_item: PurchaseOrderItem, actual_quantity: int
):
    """Record receipt of goods from shipment arrival.

    Updates quantity_received and handles shortages if actual < ordered.

    Shortage handling is a pain because we may have confirmed some orders!
    The possible outcomes:
    1. the good: we can handle the shortage by simply removing from unused quantity (unlikely as
    we are dropshippers and
    we dont tend to buy unpromised stock)
    2. the bad: we have to unconfirm some orders that have not been paid for due to
    shortage.
    3. the ugly: we have to refund some orders that have been paid due to the shortage


    """
    if purchase_order_item.status != PurchaseOrderItemStatus.CONFIRMED:
        raise InvalidPurchaseOrderItemStatus(
            purchase_order_item, PurchaseOrderItemStatus.CONFIRMED
        )

    purchase_order_item.quantity_received = actual_quantity
    purchase_order_item.status = PurchaseOrderItemStatus.RECEIVED
    purchase_order_item.save(update_fields=["quantity_received", "status"])

    # TODO: Handle shortage if actual_quantity < purchase_order_item.quantity_ordered
    if actual_quantity < purchase_order_item.quantity_ordered:
        # shortage = purchase_order_item.quantity_ordered - actual_quantity
        # _handle_shortage(purchase_order_item, shortage)
        pass
