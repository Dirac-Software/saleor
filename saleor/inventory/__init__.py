class PurchaseOrderItemStatus:
    """Status of a purchase order item through its lifecycle."""

    DRAFT = "draft"  # Being entered into system
    CONFIRMED = "confirmed"  # Ordered from supplier, in transit
    RECEIVED = "received"  # Physically arrived in the warehouse
    CANCELLED = "cancelled"  # Cancelled

    CHOICES = [
        (DRAFT, "Draft"),
        (CONFIRMED, "Confirmed"),
        (RECEIVED, "Received"),
        (CANCELLED, "Cancelled"),
    ]


class PurchaseOrderItemAdjustmentReason:
    """Reasons for post-receipt inventory adjustments."""

    SHRINKAGE_THEFT = "shrinkage_theft"
    SHRINKAGE_DAMAGE = "shrinkage_damage"
    SHRINKAGE_UNKNOWN = "shrinkage_unknown"

    CYCLE_COUNT_NEGATIVE = "cycle_count_neg"
    CYCLE_COUNT_POSITIVE = "cycle_count_pos"

    INVOICE_VARIANCE = "invoice_variance"
    DELIVERY_SHORT = "delivery_short"

    CHOICES = [
        (SHRINKAGE_THEFT, "Shrinkage - Theft"),
        (SHRINKAGE_DAMAGE, "Shrinkage - Damage"),
        (SHRINKAGE_UNKNOWN, "Shrinkage - Unknown"),
        (CYCLE_COUNT_NEGATIVE, "Cycle Count - Shortage Found"),
        (CYCLE_COUNT_POSITIVE, "Cycle Count - Excess Found"),
        (INVOICE_VARIANCE, "Invoice Variance"),
        (DELIVERY_SHORT, "Delivery Short"),
    ]


class ReceiptStatus:
    """Status of a goods receipt."""

    IN_PROGRESS = "in_progress"  # Currently receiving items
    COMPLETED = "completed"  # All items processed, shipment marked received
    CANCELLED = "cancelled"  # Receipt cancelled

    CHOICES = [
        (IN_PROGRESS, "In Progress"),
        (COMPLETED, "Completed"),
        (CANCELLED, "Cancelled"),
    ]
