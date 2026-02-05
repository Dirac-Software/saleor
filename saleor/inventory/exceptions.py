"""Custom exceptions for inventory/purchase order operations."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import (
        PurchaseOrderItem,
        PurchaseOrderItemAdjustment,
        Receipt,
        ReceiptLine,
    )


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


class AdjustmentAlreadyProcessed(Exception):
    """Raised when trying to process an adjustment that has already been processed."""

    def __init__(self, adjustment: "PurchaseOrderItemAdjustment"):
        self.adjustment = adjustment
        super().__init__(
            f"Adjustment {adjustment.id} has already been processed at "
            f"{adjustment.processed_at}"
        )


class AdjustmentAffectsConfirmedOrders(Exception):
    """Raised when a negative adjustment in stock would affect confirmed orders.

    These require complex resolution (refund/substitution workflow).
    """

    def __init__(self, adjustment: "PurchaseOrderItemAdjustment", order_numbers: list):
        self.adjustment = adjustment
        self.order_numbers = order_numbers
        super().__init__(
            f"Cannot process adjustment {adjustment.id}: affects orders with state > UNCONFIRMED"
            f"{order_numbers}. Resolution required."
        )


class AdjustmentAffectsFulfilledOrders(Exception):
    """Raised when a negative adjustment would affect UNFULFILLED orders.

    UNFULFILLED orders are locked and cannot be edited via standard mutations.
    These require manual resolution (refund workflow, line cancellation, etc.).
    """

    def __init__(self, adjustment: "PurchaseOrderItemAdjustment", order_numbers: list):
        self.adjustment = adjustment
        self.order_numbers = order_numbers
        super().__init__(
            f"Cannot process adjustment {adjustment.id}: affects UNFULFILLED orders "
            f"{order_numbers}. UNFULFILLED orders cannot be automatically modified. "
            f"Manual resolution required."
        )


class AdjustmentAffectsPaidOrders(Exception):
    """Raised when a negative adjustment would affect fully paid orders.

    Paid orders require refund workflow before stock can be reduced.
    """

    def __init__(self, adjustment: "PurchaseOrderItemAdjustment", order_numbers: list):
        self.adjustment = adjustment
        self.order_numbers = order_numbers
        super().__init__(
            f"Cannot process adjustment {adjustment.id}: affects fully paid orders "
            f"{order_numbers}. Refund workflow required."
        )


# Receipt exceptions


class ReceiptNotInProgress(Exception):
    """Raised when trying to modify a receipt that is not in progress."""

    def __init__(self, receipt: "Receipt"):
        self.receipt = receipt
        self.status = receipt.status
        super().__init__(
            f"Receipt {receipt.id} is not in progress (status: {self.status}). "
            f"Only in-progress receipts can be modified."
        )


class ReceiptLineNotInProgress(Exception):
    """Raised when trying to delete a receipt line from a completed receipt."""

    def __init__(self, receipt_line: "ReceiptLine"):
        self.receipt_line = receipt_line
        self.receipt = receipt_line.receipt
        self.status = receipt_line.receipt.status
        super().__init__(
            f"Cannot delete receipt line {receipt_line.id}: "
            f"receipt {self.receipt.id} is {self.status}. "
            f"Only lines from in-progress receipts can be deleted."
        )
