"""Inventory test configuration and fixtures."""

import pytest

# Import fixtures from other modules
from ...order.tests.fixtures import order_line  # noqa: F401
from ...product.tests.fixtures.variant import variant  # noqa: F401
from ...warehouse.tests.fixtures.warehouse import (  # noqa: F401
    nonowned_warehouse,
    owned_warehouse,
)

# Import local fixtures
from .fixtures import (  # noqa: F401
    multiple_purchase_order_items,
    purchase_order,
    purchase_order_item,
    receipt,
    receipt_factory,
    receipt_line,
    receipt_line_factory,
    shipment,
)


@pytest.fixture
def product_variant_factory(product, channel_USD):
    """Create product variants on demand."""
    from decimal import Decimal

    from ...product.models import ProductVariant, ProductVariantChannelListing

    def create_variant(sku=None, **kwargs):
        if sku is None:
            import uuid

            sku = f"TEST-SKU-{uuid.uuid4().hex[:8]}"

        new_variant = ProductVariant.objects.create(product=product, sku=sku, **kwargs)
        ProductVariantChannelListing.objects.create(
            variant=new_variant,
            channel=channel_USD,
            price_amount=Decimal(10),
            discounted_price_amount=Decimal(10),
            cost_price_amount=Decimal(1),
            currency=channel_USD.currency_code,
        )
        return new_variant

    return create_variant
