"""Event logging for inventory/purchase order operations."""

from ..account.models import User
from ..app.models import App
from . import PurchaseOrderEvents
from .models import (
    PurchaseOrder,
    PurchaseOrderEvent,
    PurchaseOrderItem,
    PurchaseOrderItemAdjustment,
)


def purchase_order_created_event(
    *,
    purchase_order: PurchaseOrder,
    user: User | None = None,
    app: App | None = None,
) -> PurchaseOrderEvent:
    """Log purchase order creation event.

    Records when a new purchase order is created in the system.
    """
    return PurchaseOrderEvent.objects.create(
        type=PurchaseOrderEvents.CREATED,
        purchase_order=purchase_order,
        user=user,
        app=app,
        parameters={
            "source_warehouse_id": purchase_order.source_warehouse_id,
            "destination_warehouse_id": purchase_order.destination_warehouse_id,
        },
    )


def purchase_order_confirmed_event(
    *,
    purchase_order: PurchaseOrder,
    user: User | None = None,
    app: App | None = None,
) -> PurchaseOrderEvent:
    """Log purchase order confirmation event.

    Records when a purchase order is confirmed with the supplier.
    """
    return PurchaseOrderEvent.objects.create(
        type=PurchaseOrderEvents.CONFIRMED,
        purchase_order=purchase_order,
        user=user,
        app=app,
        parameters={
            "item_count": purchase_order.items.count(),
        },
    )


def purchase_order_cancelled_event(
    *,
    purchase_order: PurchaseOrder,
    reason: str | None = None,
    user: User | None = None,
    app: App | None = None,
) -> PurchaseOrderEvent:
    """Log purchase order cancellation event.

    Records when a purchase order is cancelled before receipt.
    """
    parameters = {}
    if reason:
        parameters["reason"] = reason

    return PurchaseOrderEvent.objects.create(
        type=PurchaseOrderEvents.CANCELLED,
        purchase_order=purchase_order,
        user=user,
        app=app,
        parameters=parameters,
    )


def purchase_order_received_event(
    *,
    purchase_order: PurchaseOrder,
    user: User | None = None,
    app: App | None = None,
) -> PurchaseOrderEvent:
    """Log purchase order receipt completion.

    Records when all goods for a purchase order have been received.
    """
    return PurchaseOrderEvent.objects.create(
        type=PurchaseOrderEvents.RECEIVED,
        purchase_order=purchase_order,
        user=user,
        app=app,
    )


def purchase_order_item_added_event(
    *,
    purchase_order_item: PurchaseOrderItem,
    user: User | None = None,
    app: App | None = None,
) -> PurchaseOrderEvent:
    """Log addition of item to purchase order.

    Records when a new line item is added to a purchase order.
    """
    return PurchaseOrderEvent.objects.create(
        type=PurchaseOrderEvents.ITEM_ADDED,
        purchase_order=purchase_order_item.order,
        purchase_order_item=purchase_order_item,
        user=user,
        app=app,
        parameters={
            "variant_id": purchase_order_item.product_variant_id,
            "sku": purchase_order_item.product_variant.sku,
            "quantity": purchase_order_item.quantity_ordered,
            "total_price": str(purchase_order_item.total_price_amount),
            "currency": purchase_order_item.currency,
        },
    )


def purchase_order_item_removed_event(
    *,
    purchase_order: PurchaseOrder,
    variant_sku: str,
    quantity: int,
    user: User | None = None,
    app: App | None = None,
) -> PurchaseOrderEvent:
    """Log removal of item from purchase order.

    Records when a line item is removed from a purchase order.
    Note: POI is already deleted, so we store key info in parameters.
    """
    return PurchaseOrderEvent.objects.create(
        type=PurchaseOrderEvents.ITEM_REMOVED,
        purchase_order=purchase_order,
        user=user,
        app=app,
        parameters={
            "sku": variant_sku,
            "quantity": quantity,
        },
    )


def purchase_order_item_confirmed_event(
    *,
    purchase_order_item: PurchaseOrderItem,
    user: User | None = None,
    app: App | None = None,
) -> PurchaseOrderEvent:
    """Log POI confirmation and stock movement.

    Records when a POI is confirmed and stock moves from supplier
    to owned warehouse.
    """
    return PurchaseOrderEvent.objects.create(
        type=PurchaseOrderEvents.CONFIRMED,
        purchase_order=purchase_order_item.order,
        purchase_order_item=purchase_order_item,
        user=user,
        app=app,
        parameters={
            "variant_id": purchase_order_item.product_variant_id,
            "sku": purchase_order_item.product_variant.sku,
            "quantity": purchase_order_item.quantity_ordered,
            "source_warehouse_id": purchase_order_item.order.source_warehouse_id,
            "destination_warehouse_id": purchase_order_item.order.destination_warehouse_id,
        },
    )


def adjustment_created_event(
    *,
    adjustment: PurchaseOrderItemAdjustment,
    user: User | None = None,
    app: App | None = None,
) -> PurchaseOrderEvent:
    """Log creation of inventory adjustment.

    Records when an adjustment is created (before processing).
    """
    return PurchaseOrderEvent.objects.create(
        type=PurchaseOrderEvents.ADJUSTMENT_CREATED,
        purchase_order=adjustment.purchase_order_item.order,
        purchase_order_item=adjustment.purchase_order_item,
        user=user,
        app=app,
        parameters={
            "adjustment_id": adjustment.id,
            "quantity_change": adjustment.quantity_change,
            "reason": adjustment.reason,
            "affects_payable": adjustment.affects_payable,
            "notes": adjustment.notes,
        },
    )


def adjustment_processed_event(
    *,
    adjustment: PurchaseOrderItemAdjustment,
    user: User | None = None,
    app: App | None = None,
) -> PurchaseOrderEvent:
    """Log processing of inventory adjustment.

    Records when an adjustment is processed and stock/allocations updated.
    """
    return PurchaseOrderEvent.objects.create(
        type=PurchaseOrderEvents.ADJUSTMENT_PROCESSED,
        purchase_order=adjustment.purchase_order_item.order,
        purchase_order_item=adjustment.purchase_order_item,
        user=user,
        app=app,
        parameters={
            "adjustment_id": adjustment.id,
            "quantity_change": adjustment.quantity_change,
            "reason": adjustment.reason,
            "financial_impact": str(adjustment.financial_impact),
        },
    )


def shipment_assigned_event(
    *,
    purchase_order_item: PurchaseOrderItem,
    shipment_id: int,
    user: User | None = None,
    app: App | None = None,
) -> PurchaseOrderEvent:
    """Log assignment of POI to shipment.

    Records when a purchase order item is assigned to an inbound shipment.
    """
    return PurchaseOrderEvent.objects.create(
        type=PurchaseOrderEvents.SHIPMENT_ASSIGNED,
        purchase_order=purchase_order_item.order,
        purchase_order_item=purchase_order_item,
        user=user,
        app=app,
        parameters={
            "shipment_id": shipment_id,
            "variant_id": purchase_order_item.product_variant_id,
            "sku": purchase_order_item.product_variant.sku,
        },
    )


def note_added_event(
    *,
    purchase_order: PurchaseOrder,
    note: str,
    user: User | None = None,
    app: App | None = None,
) -> PurchaseOrderEvent:
    """Log addition of note to purchase order.

    Records when a user or app adds a note/comment to a PO.
    """
    return PurchaseOrderEvent.objects.create(
        type=PurchaseOrderEvents.NOTE_ADDED,
        purchase_order=purchase_order,
        user=user,
        app=app,
        parameters={
            "note": note,
        },
    )
