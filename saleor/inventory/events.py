"""Event logging for inventory/purchase order operations."""
from typing import Optional

from ..account.models import User
from ..app.models import App
from .models import PurchaseOrder


def purchase_order_created_event(
    *,
    purchase_order: PurchaseOrder,
    user: Optional[User] = None,
    app: Optional[App] = None,
) -> None:
    """Log purchase order creation event.

    This creates an audit trail entry when a purchase order is created.
    """
    # In a full implementation, this would create an event log entry
    # For now, we'll keep it as a placeholder for the event system
    # that Saleor uses for audit trails
    pass


def purchase_order_confirmed_event(
    *,
    purchase_order: PurchaseOrder,
    user: Optional[User] = None,
    app: Optional[App] = None,
) -> None:
    """Log purchase order confirmation event.

    This creates an audit trail entry when a purchase order is confirmed.
    """
    # In a full implementation, this would create an event log entry
    # For now, we'll keep it as a placeholder for the event system
    # that Saleor uses for audit trails
    pass
