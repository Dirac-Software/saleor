"""Custom exceptions for inventory/purchase order operations."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import PurchaseOrderItem


class InvalidPurchaseOrderItemStatus(Exception):
    """Raised when trying to perform an operation on a POI in the wrong status."""

    def __init__(self, purchase_order_item: "PurchaseOrderItem", expected_status: str):
        self.purchase_order_item = purchase_order_item
        self.expected_status = expected_status
        self.actual_status = purchase_order_item.status
        super().__init__(
            f"Cannot perform operation: PurchaseOrderItem {purchase_order_item.id} "
            f"is in status '{self.actual_status}', expected '{expected_status}'"
        )
