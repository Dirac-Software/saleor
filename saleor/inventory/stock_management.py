"""Stock management utilities for purchase orders and inventory tracking."""

from uuid import UUID

from django.db import transaction
from django.utils import timezone

from ..core.exceptions import InsufficientStock, InsufficientStockData
from ..warehouse.models import Stock
from . import PurchaseOrderItemStatus
from .events import (
    adjustment_created_event,
    adjustment_processed_event,
    purchase_order_item_confirmed_event,
)
from .exceptions import (
    AdjustmentAffectsConfirmedOrders,
    AdjustmentAffectsFulfilledOrders,
    AdjustmentAffectsPaidOrders,
    AdjustmentAlreadyProcessed,
    AllocationInvariantViolation,
    InvalidPurchaseOrderItemStatus,
    ReceiptLineNotInProgress,
    ReceiptNotInProgress,
)
from .models import PurchaseOrderItem, PurchaseOrderItemAdjustment


@transaction.atomic
def confirm_purchase_order_item(
    purchase_order_item: PurchaseOrderItem, user=None, app=None
):
    """Confirm purchase order item and move stock from supplier to owned warehouse.

    This is THE ONLY WAY stock enters owned warehouses. When a POI is confirmed,
    we move stock from the supplier (non-owned) warehouse to our owned warehouse.

    The function:
    1. Moves physical stock from source to destination
    2. Tries to attach existing allocations (if any) to the stock
    3. Creates AllocationSources to link allocations to this POI (batch tracking)
    4. Logs the confirmation event for audit trail

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

    destination, created = (
        Stock.objects.select_for_update()
        .select_related("warehouse")
        .get_or_create(
            warehouse=purchase_order_item.order.destination_warehouse,
            product_variant=purchase_order_item.product_variant,
            defaults={"quantity": 0, "quantity_allocated": 0},
        )
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
        else:
            # INVARIANT VIOLATION: Non-owned warehouses should only have allocations
            # for UNCONFIRMED orders. Once confirmed, allocations must be in owned
            # warehouses with AllocationSources.
            raise AllocationInvariantViolation(
                warehouse_name=source.warehouse.name,
                order_number=str(order.number),
                order_status=order.status,
            )

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

            # Create fulfillments per warehouse with WAITING_FOR_APPROVAL status
            from collections import defaultdict

            from django.contrib.sites.models import Site

            from ..order.actions import (
                OrderFulfillmentLineInfo,
                create_fulfillments,
                order_confirmed,
            )
            from ..plugins.manager import get_plugins_manager

            manager = get_plugins_manager(allow_replica=False)

            # Send order confirmed email
            def send_order_confirmation():
                order_confirmed(
                    order,
                    user,
                    app,
                    manager,
                    send_confirmation_email=True,
                )

            transaction.on_commit(send_order_confirmation)

            # Get allocations and group by warehouse
            allocations = Allocation.objects.filter(
                order_line__order=order
            ).select_related("stock__warehouse", "order_line")

            warehouse_groups = defaultdict(list)
            for allocation in allocations:
                warehouse_pk = allocation.stock.warehouse_id
                warehouse_groups[warehouse_pk].append(allocation)

            # Build fulfillment_lines_for_warehouses dict
            fulfillment_lines_for_warehouses: dict[
                UUID, list[OrderFulfillmentLineInfo]
            ] = {}
            for warehouse_pk, allocations_list in warehouse_groups.items():
                lines: list[OrderFulfillmentLineInfo] = []
                for allocation in allocations_list:
                    lines.append(
                        OrderFulfillmentLineInfo(
                            order_line=allocation.order_line,
                            quantity=allocation.quantity_allocated,
                        )
                    )
                fulfillment_lines_for_warehouses[warehouse_pk] = lines

            # Create fulfillments
            site_settings = Site.objects.get_current().settings

            create_fulfillments(
                user=user,
                app=app,
                order=order,
                fulfillment_lines_for_warehouses=fulfillment_lines_for_warehouses,
                manager=manager,
                site_settings=site_settings,
                notify_customer=False,
                auto_approved=False,
                tracking_url="",
            )

    # Log event for audit trail
    purchase_order_item_confirmed_event(
        purchase_order_item=purchase_order_item,
        user=user,
        app=app,
    )

    return source


# TODO: this needs work on refunding orders
@transaction.atomic
def process_adjustment(
    adjustment: PurchaseOrderItemAdjustment,
    user=None,
    app=None,
    manager=None,
):
    """Process a PurchaseOrderItemAdjustment and update stock.

    Handles inventory discrepancies by adjusting stock and POI quantities.
    For negative adjustments, deallocates from unpaid orders if stock is allocated.

    Args:
        adjustment: PurchaseOrderItemAdjustment instance to process
        user: User processing the adjustment (optional)
        app: App processing the adjustment (optional)
        manager: PluginsManager for webhooks (optional)

    For positive adjustments (gains):
        - Increases stock.quantity
        - Increases POI.quantity_received
        - Makes stock available for new allocations

    For negative adjustments (losses):
        - Decreases stock.quantity
        - Decreases POI.quantity_received
        - Deallocates from affected unpaid orders
        - Unconfirms orders that lose all their sources

    Raises:
        AdjustmentAlreadyProcessed: If adjustment already processed
        AdjustmentAffectsFulfilledOrders: If affects UNFULFILLED orders (locked, not editable)
        AdjustmentAffectsPaidOrders: If negative adjustment affects fully paid orders
        InsufficientStock: If loss exceeds total physical stock in warehouse

    UNFULFILLED orders require manual resolution (cannot be edited via standard flow) -
    we will kick this back for now

    """
    from ..order import OrderStatus
    from ..warehouse.management import can_confirm_order, deallocate_sources
    from ..warehouse.models import AllocationSource

    if adjustment.processed_at is not None:
        raise AdjustmentAlreadyProcessed(adjustment)

    poi = adjustment.purchase_order_item
    quantity_change = adjustment.quantity_change

    # Get stock with lock
    stock = Stock.objects.select_for_update().get(
        warehouse=poi.order.destination_warehouse,
        product_variant=poi.product_variant,
    )

    # Handle positive adjustment (gain)
    if quantity_change > 0:
        stock.quantity += quantity_change
        stock.save(update_fields=["quantity"])

        # Note: quantity_received is not modified - it represents what was physically received
        # Adjustments affect available_quantity which includes processed adjustments

    # Handle negative adjustment (loss)
    elif quantity_change < 0:
        loss = abs(quantity_change)

        # stock.quantity is live stock so if we don't have enough live stock how the
        # hell did we lose more than we ever had?
        if stock.quantity < loss:
            raise InsufficientStock(
                [
                    InsufficientStockData(
                        available_quantity=stock.quantity,
                        variant=stock.product_variant,
                        warehouse_pk=stock.warehouse.pk,
                    )
                ]
            )

        # Find allocations sourced from this POI batch
        affected_sources = (
            AllocationSource.objects.select_for_update()
            .select_related("allocation__order_line__order")
            .filter(purchase_order_item=poi)
        )

        # Check order statuses and payment
        unfulfilled_orders_affected = []
        paid_orders_affected = []
        unconfirmed_sources = []

        for source in affected_sources:
            order = source.allocation.order_line.order

            # Check if UNFULFILLED (locked, can't edit)
            if order.status == OrderStatus.UNFULFILLED:
                unfulfilled_orders_affected.append(order.number)

            # Check if fully paid
            elif order.is_fully_paid():
                paid_orders_affected.append(order.number)

            # UNCONFIRMED and not fully paid - we can handle this
            else:
                unconfirmed_sources.append(source)

        # Reject if UNFULFILLED orders are affected
        # These are locked and cannot be edited automatically
        if unfulfilled_orders_affected:
            raise AdjustmentAffectsFulfilledOrders(
                adjustment, unfulfilled_orders_affected
            )

        # Reject if fully paid orders are affected
        # TODO: Implement refund workflow
        if paid_orders_affected:
            raise AdjustmentAffectsPaidOrders(adjustment, paid_orders_affected)

        # Deallocate from UNCONFIRMED, unpaid orders
        # Track remaining loss to distribute across affected allocations
        remaining_loss = loss
        orders_to_check = set()
        for source in unconfirmed_sources:
            if remaining_loss <= 0:
                break

            allocation = source.allocation
            order = allocation.order_line.order
            quantity_to_deallocate = min(source.quantity, remaining_loss)

            # Deallocate sources (removes AllocationSource, restores POI.quantity_allocated)
            deallocate_sources(allocation, quantity_to_deallocate)

            # Track unconfirmed orders for status check
            if order.status == OrderStatus.UNCONFIRMED:
                orders_to_check.add(order)

            # Reduce allocation quantity
            allocation.quantity_allocated -= quantity_to_deallocate
            if allocation.quantity_allocated == 0:
                allocation.delete()
            else:
                allocation.save(update_fields=["quantity_allocated"])

            # Update stock quantity_allocated
            stock.quantity_allocated -= quantity_to_deallocate

            # Track remaining loss to distribute
            remaining_loss -= quantity_to_deallocate

        # Decrease physical stock
        stock.quantity -= loss
        stock.save(update_fields=["quantity", "quantity_allocated"])

        # Note: quantity_received is not modified - it represents what was physically received
        # The adjustment is tracked separately and affects available_quantity

        # Transition UNCONFIRMED orders back to DRAFT if they lost all their sources
        # UNCONFIRMED is a transient state waiting for all allocations to have sources
        # If stock adjustment removes sources, order must go back to DRAFT
        for order in orders_to_check:
            if not can_confirm_order(order):
                order.status = OrderStatus.DRAFT
                order.save(update_fields=["status", "updated_at"])

    # Mark adjustment as processed
    adjustment.processed_at = timezone.now()
    adjustment.save(update_fields=["processed_at"])

    # Log event for audit trail
    adjustment_processed_event(
        adjustment=adjustment,
        user=user,
        app=app,
    )

    return adjustment


# Receipt workflow functions


@transaction.atomic
def start_receipt(shipment, user=None):
    """Create a new Receipt for receiving an inbound shipment.

    Args:
        shipment: Shipment being received
        user: User starting the receipt (warehouse staff)

    Returns:
        Receipt instance

    Raises:
        ValueError: If shipment already has a receipt or is already received

    """
    from ..shipping import ShipmentType
    from .models import Receipt

    if shipment.shipment_type != ShipmentType.INBOUND:
        raise ValueError(
            f"Cannot start receipt for {shipment.shipment_type} shipment. "
            "Only inbound shipments can be received."
        )

    if shipment.arrived_at is not None:
        raise ValueError(f"Shipment {shipment.id} already marked as received")

    if hasattr(shipment, "receipt"):
        existing = shipment.receipt
        if existing.status == "in_progress":
            return existing
        raise ValueError(f"Shipment {shipment.id} already has a receipt")

    receipt = Receipt.objects.create(
        shipment=shipment,
        created_by=user,
    )

    return receipt


@transaction.atomic
def receive_item(receipt, product_variant, quantity, user=None, notes=""):
    """Add a received item to a receipt.

    Scans/records an item during receiving. Updates POI.quantity_received
    and creates a ReceiptLine for audit trail.

    Args:
        receipt: Receipt to add item to
        product_variant: ProductVariant being received
        quantity: Quantity received
        user: User who scanned/recorded the item
        notes: Optional notes about this specific item

    Returns:
        ReceiptLine instance

    Raises:
        ReceiptNotInProgress: If receipt is not in progress
        ValueError: If variant not in shipment

    """
    from . import ReceiptStatus
    from .models import ReceiptLine

    if receipt.status != ReceiptStatus.IN_PROGRESS:
        raise ReceiptNotInProgress(receipt)

    try:
        poi = PurchaseOrderItem.objects.select_for_update().get(
            shipment=receipt.shipment,
            product_variant=product_variant,
        )
    except PurchaseOrderItem.DoesNotExist:
        raise ValueError(
            f"Product variant {product_variant.sku} not found in "
            f"shipment {receipt.shipment.id}"
        ) from None

    # Create receipt line (quantity_received will be automatically calculated from receipt lines)
    receipt_line = ReceiptLine.objects.create(
        receipt=receipt,
        purchase_order_item=poi,
        quantity_received=quantity,
        received_by=user,
        notes=notes,
    )

    return receipt_line


@transaction.atomic
def complete_receipt(receipt, user=None, manager=None):
    """Complete a receipt and process any discrepancies.

    Finalizes the receiving process:
    1. Compares quantity_received vs quantity_ordered for each POI
    2. Creates PurchaseOrderItemAdjustments for discrepancies
    3. Processes adjustments (updates stock, handles allocations)
    4. Marks shipment as arrived
    5. Updates POI status to RECEIVED
    6. Marks receipt as completed

    If adjustments affect confirmed orders, creates the adjustments but
    doesn't process them immediately. Instead, emails staff to manually
    process them.

    Args:
        receipt: Receipt to complete
        user: User completing the receipt
        manager: PluginsManager for sending notifications (optional)

    Returns:
        dict with summary: {
            'receipt': Receipt,
            'adjustments_created': [PurchaseOrderItemAdjustment, ...],
            'adjustments_pending': [PurchaseOrderItemAdjustment, ...],
            'items_received': int,
            'discrepancies': int,
        }

    Raises:
        ValueError: If receipt is not in progress

    """
    from ..core.notify import AdminNotifyEvent, NotifyHandler
    from . import (
        PurchaseOrderItemAdjustmentReason,
        PurchaseOrderItemStatus,
        ReceiptStatus,
    )
    from .models import PurchaseOrderItemAdjustment

    # Validate receipt status
    if receipt.status != ReceiptStatus.IN_PROGRESS:
        raise ValueError(f"Receipt {receipt.id} is not in progress")

    shipment = receipt.shipment
    pois = PurchaseOrderItem.objects.select_for_update().filter(shipment=shipment)

    adjustments_created = []
    adjustments_pending = []
    discrepancies = 0

    # Check each POI for discrepancies
    for poi in pois:
        discrepancy = poi.quantity_received - poi.quantity_ordered

        if discrepancy != 0:
            discrepancies += 1

            # Determine reason and whether it affects payable
            if discrepancy < 0:
                # Delivery short - supplier should credit us
                reason = PurchaseOrderItemAdjustmentReason.DELIVERY_SHORT
                affects_payable = True
            else:
                # Received more than ordered - rare but possible
                reason = PurchaseOrderItemAdjustmentReason.CYCLE_COUNT_POSITIVE
                affects_payable = False

            # Create adjustment
            adjustment = PurchaseOrderItemAdjustment.objects.create(
                purchase_order_item=poi,
                quantity_change=discrepancy,
                reason=reason,
                affects_payable=affects_payable,
                notes=f"Auto-created during receipt completion (Receipt #{receipt.id})",
                created_by=user,
            )

            # Log adjustment creation for audit trail
            adjustment_created_event(
                adjustment=adjustment,
                user=user,
            )

            # Try to process the adjustment immediately
            try:
                process_adjustment(adjustment, user=user)
                adjustments_created.append(adjustment)
            except AdjustmentAffectsConfirmedOrders:
                # Can't auto-process - affects confirmed orders
                # Leave unprocessed and notify staff
                adjustments_pending.append(adjustment)

        # Update POI status to RECEIVED
        poi.status = PurchaseOrderItemStatus.RECEIVED
        poi.save(update_fields=["status", "updated_at"])

    # Mark shipment as arrived
    if shipment.arrived_at is None:
        shipment.arrived_at = timezone.now()
        shipment.save(update_fields=["arrived_at"])

    # Complete the receipt
    receipt.status = ReceiptStatus.COMPLETED
    receipt.completed_at = timezone.now()
    receipt.completed_by = user
    receipt.save(update_fields=["status", "completed_at", "completed_by"])

    # If there are pending adjustments, notify staff
    if adjustments_pending and manager:

        def generate_payload():
            return {
                "receipt_id": receipt.id,
                "shipment_id": shipment.id,
                "count": len(adjustments_pending),
                "adjustments": [
                    {
                        "id": adj.id,
                        "poi_id": adj.purchase_order_item.id,
                        "variant_sku": adj.purchase_order_item.product_variant.sku,
                        "quantity_change": adj.quantity_change,
                        "reason": adj.get_reason_display(),
                    }
                    for adj in adjustments_pending
                ],
            }

        handler = NotifyHandler(generate_payload)
        manager.notify(
            AdminNotifyEvent.PENDING_ADJUSTMENTS,
            payload_func=handler.payload,
        )

    return {
        "receipt": receipt,
        "adjustments_created": adjustments_created,
        "adjustments_pending": adjustments_pending,
        "items_received": len(pois),
        "discrepancies": discrepancies,
    }


@transaction.atomic
def delete_receipt(receipt):
    """Delete a draft receipt and revert any quantity updates.

    Only allows deleting receipts that are still IN_PROGRESS.
    Reverts POI.quantity_received for all items in the receipt.

    Args:
        receipt: Receipt to delete

    Raises:
        ReceiptNotInProgress: If receipt is already completed

    """
    from . import ReceiptStatus

    # Only allow deleting in-progress receipts
    if receipt.status != ReceiptStatus.IN_PROGRESS:
        raise ReceiptNotInProgress(receipt)

    # Delete the receipt (cascade will delete lines, quantity_received auto-recalculates)
    receipt.delete()


@transaction.atomic
def delete_receipt_line(receipt_line):
    """Delete a receipt line and revert quantity update.

    Use when an item was scanned by mistake during receiving.
    Only works if receipt is still IN_PROGRESS.

    Args:
        receipt_line: ReceiptLine to delete

    Raises:
        ReceiptLineNotInProgress: If receipt is not in progress

    """
    from . import ReceiptStatus

    # Only allow deleting lines from in-progress receipts
    if receipt_line.receipt.status != ReceiptStatus.IN_PROGRESS:
        raise ReceiptLineNotInProgress(receipt_line)

    # Delete the line (quantity_received auto-recalculates from remaining lines)
    receipt_line.delete()
