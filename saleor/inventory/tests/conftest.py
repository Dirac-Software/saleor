"""Inventory test configuration and fixtures."""

# Import fixtures from other modules
from ...order.tests.fixtures import order_line  # noqa: F401
from ...product.tests.fixtures.variant import variant  # noqa: F401
from ...warehouse.tests.fixtures.warehouse import (  # noqa: F401
    nonowned_warehouse,
    owned_warehouse,
)
