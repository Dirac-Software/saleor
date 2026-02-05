import pytest

from ....warehouse.models import Warehouse

# Note: pytest_plugins can only be defined in top-level conftest.py
# Warehouse fixtures are imported by the root conftest.py


@pytest.fixture
def warehouse(address, shipping_zone, channel_USD):
    """Override to ensure warehouse is owned by default for tests."""
    warehouse = Warehouse.objects.create(
        address=address,
        name="Example Warehouse",
        slug="example-warehouse",
        email="test@example.com",
        is_owned=True,  # Owned warehouse for destination
    )
    warehouse.shipping_zones.add(shipping_zone)
    warehouse.channels.add(channel_USD)
    return warehouse


@pytest.fixture
def supplier_warehouse(address, shipping_zone, channel_USD):
    """Supplier warehouse (non-owned) for purchase order source."""
    warehouse = Warehouse.objects.create(
        address=address.get_copy(),
        name="Supplier Warehouse",
        slug="supplier-warehouse",
        email="supplier@example.com",
        is_owned=False,
    )
    warehouse.shipping_zones.add(shipping_zone)
    warehouse.channels.add(channel_USD)
    return warehouse
