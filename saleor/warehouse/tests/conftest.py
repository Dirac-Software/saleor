"""Warehouse test configuration and fixtures."""

# Import inventory fixtures to make them available to warehouse tests
from ...inventory.tests.fixtures.purchase_order import (  # noqa: F401
    multiple_purchase_order_items,
    purchase_order,
    purchase_order_item,
)
