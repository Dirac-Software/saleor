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

    This is THE WAY stock enters owned warehouses. When a POI is confirmed,
    we move stock from the supplier (non-owned) warehouse to our owned warehouse.

    The function:
    1. Moves physical stock from source to destination
    2. Tries to attach existing allocations (if any) to the stock
    3. Creates AllocationSources to link allocations to this POI (batch tracking)

    This enforces the invariant:
        Stock.quantity (owned) == sum(POI.quantity_ordered - POI.quantity_allocated)
    """

    from ..warehouse.management import allocate_sources
    from ..warehouse.models import Allocation

    if purchase_order_item.status != PurchaseOrderItemStatus.DRAFT:
        raise InvalidPurchaseOrderItemStatus(
            purchase_order_item, PurchaseOrderItemStatus.DRAFT
        )

    # Get source and destination
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
        raise ValueError("Insufficient stock at source")

    # Get allocations to potentially move (FIFO by order line creation time)
    allocations = (
        source.allocations.select_for_update()
        .select_related("order_line")
        .order_by("order_line__created_at")
    )

    # Pre-move all stock to destination as unallocated
    if source.quantity_allocated > quantity:
        # We will have some leftover allocations
        source.quantity_allocated -= quantity
    else:
        source.quantity = quantity - source.quantity_allocated
        source.quantity_allocated = 0

    destination.quantity += quantity

    # Try to convert destination.quantity to destination.quantity_allocated
    # by moving allocations and creating AllocationSources
    for allocation in allocations:
        if destination.quantity > allocation.quantity_allocated:
            # Move entire allocation
            allocation.stock = destination
            allocation.save(update_fields=["stock"])

            # Link to POI via AllocationSources
            allocate_sources(allocation)
            destination.quantity_allocated += allocation.quantity_allocated
            destination.quantity -= allocation.quantity_allocated
        else:
            # Split allocation: partial to destination, rest stays at source
            move_quantity = destination.quantity

            # Create new allocation at destination for the moved portion
            moved_allocation = Allocation.objects.create(
                order_line=allocation.order_line,
                stock=destination,
                quantity_allocated=move_quantity,
            )

            try:
                # Try to allocate sources for the moved portion
                allocate_sources(moved_allocation)

                # Update quantities
                destination.quantity_allocated += move_quantity
                destination.quantity -= move_quantity

                # Reduce original allocation (stays at source)
                allocation.quantity_allocated -= move_quantity
                allocation.save(update_fields=["quantity_allocated"])

                # No more room at destination, stop moving allocations
                break

            except InsufficientStock:
                # Couldn't track at owned warehouse, delete the split allocation
                moved_allocation.delete()
                break

    # Save both stocks
    source.save(update_fields=["quantity", "quantity_allocated"])
    destination.save(update_fields=["quantity", "quantity_allocated"])

    # Update POI status
    purchase_order_item.status = PurchaseOrderItemStatus.CONFIRMED
    purchase_order_item.confirmed_at = timezone.now()
    purchase_order_item.save(update_fields=["status", "confirmed_at"])

    return source


@transaction.atomic
def receive_purchase_order_item(
    purchase_order_item: PurchaseOrderItem, actual_quantity: int
):
    """Record receipt of goods from shipment arrival.

    Updates quantity_received and handles shortages if actual < ordered.

    TODO: Implement shortage handling:
    - Decrease Stock.quantity by shortage amount
    - Cancel AllocationSources (LIFO) and update PurchaseOrderItem.quantity_allocated
    - Decrease Stock.quantity_allocated
    - Return affected order_line IDs for customer notification
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
