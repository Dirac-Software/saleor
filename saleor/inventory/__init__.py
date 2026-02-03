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
