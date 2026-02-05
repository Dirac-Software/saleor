"""Pytest fixtures for Receipt tests."""

import pytest

from ... import ReceiptStatus
from ...models import Receipt, ReceiptLine


@pytest.fixture
def receipt(shipment, staff_user):
    """Create an in-progress receipt for testing."""
    return Receipt.objects.create(
        shipment=shipment,
        status=ReceiptStatus.IN_PROGRESS,
        created_by=staff_user,
    )


@pytest.fixture
def receipt_factory(db):
    """Create receipts with custom parameters."""

    def create_receipt(**kwargs):
        return Receipt.objects.create(**kwargs)

    return create_receipt


@pytest.fixture
def receipt_line(receipt, purchase_order_item, staff_user):
    """Create a receipt line for testing."""
    return ReceiptLine.objects.create(
        receipt=receipt,
        purchase_order_item=purchase_order_item,
        quantity_received=50,
        received_by=staff_user,
    )


@pytest.fixture
def receipt_line_factory(db):
    """Create receipt lines with custom parameters."""

    def create_receipt_line(**kwargs):
        return ReceiptLine.objects.create(**kwargs)

    return create_receipt_line
