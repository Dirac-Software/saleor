"""Tests for order confirmation validation based on AllocationSources."""

from unittest.mock import patch

from ...order import OrderStatus
from ...order.fetch import OrderLineInfo
from ...plugins.manager import get_plugins_manager
from ..management import allocate_stocks, can_confirm_order
from ..models import Allocation, AllocationSource, Stock

COUNTRY_CODE = "US"


def test_can_confirm_order_with_fully_sourced_allocations(
    order_line, owned_warehouse, purchase_order_item, channel_USD
):
    """Order can be confirmed when all allocations have sources."""
    # given
    order = order_line.order
    order.status = OrderStatus.UNCONFIRMED
    order.save(update_fields=["status"])

    variant = order_line.variant
    stock, _ = Stock.objects.get_or_create(
        warehouse=owned_warehouse,
        product_variant=variant,
        defaults={"quantity": 100},
    )
    stock.quantity = 100
    stock.save(update_fields=["quantity"])

    order_line.quantity = 50
    order_line.save(update_fields=["quantity"])

    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=50)],
        COUNTRY_CODE,
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # when/then
    assert can_confirm_order(order) is True


def test_cannot_confirm_order_with_nonowned_warehouse(
    order_line, nonowned_warehouse, channel_USD
):
    """Order cannot be confirmed if allocations are in non-owned warehouse."""
    # given
    order = order_line.order
    order.status = OrderStatus.UNCONFIRMED
    order.save(update_fields=["status"])

    variant = order_line.variant
    stock, _ = Stock.objects.get_or_create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        defaults={"quantity": 100},
    )
    stock.quantity = 100
    stock.save(update_fields=["quantity"])

    order_line.quantity = 50
    order_line.save(update_fields=["quantity"])

    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=50)],
        COUNTRY_CODE,
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # when/then
    assert can_confirm_order(order) is False


def test_cannot_confirm_order_without_allocation_sources(order_line, owned_warehouse):
    """Order cannot be confirmed if allocations have no AllocationSources."""
    # given
    order = order_line.order
    order.status = OrderStatus.UNCONFIRMED
    order.save(update_fields=["status"])

    variant = order_line.variant
    stock, _ = Stock.objects.get_or_create(
        warehouse=owned_warehouse,
        product_variant=variant,
        defaults={"quantity": 100},
    )
    stock.quantity = 100
    stock.save(update_fields=["quantity"])

    Allocation.objects.create(order_line=order_line, stock=stock, quantity_allocated=50)
    # No AllocationSource created

    # when/then
    assert can_confirm_order(order) is False


def test_cannot_confirm_order_with_partial_sources(
    order_line, owned_warehouse, purchase_order_item
):
    """Order cannot be confirmed if allocation sources don't sum to quantity_allocated."""
    # given
    order = order_line.order
    order.status = OrderStatus.UNCONFIRMED
    order.save(update_fields=["status"])

    variant = order_line.variant
    stock, _ = Stock.objects.get_or_create(
        warehouse=owned_warehouse,
        product_variant=variant,
        defaults={"quantity": 100},
    )
    stock.quantity = 100
    stock.save(update_fields=["quantity"])

    allocation = Allocation.objects.create(
        order_line=order_line, stock=stock, quantity_allocated=50
    )

    # Create source for only 30 units (not the full 50)
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=purchase_order_item, quantity=30
    )

    # when/then
    assert can_confirm_order(order) is False


def test_cannot_confirm_order_without_allocations(order):
    """Order cannot be confirmed if it has no allocations."""
    # given - order with no allocations
    order.status = OrderStatus.UNCONFIRMED
    order.save(update_fields=["status"])

    # when/then
    assert can_confirm_order(order) is False


def test_can_confirm_order_with_multiple_sources(
    order_line, owned_warehouse, purchase_order
):
    """Order can be confirmed when allocation is split across multiple POIs."""
    # given
    from ...inventory import PurchaseOrderItemStatus
    from ...inventory.models import PurchaseOrderItem

    order = order_line.order
    order.status = OrderStatus.UNCONFIRMED
    order.save(update_fields=["status"])

    variant = order_line.variant
    stock, _ = Stock.objects.get_or_create(
        warehouse=owned_warehouse,
        product_variant=variant,
        defaults={"quantity": 100},
    )
    stock.quantity = 100
    stock.save(update_fields=["quantity"])

    # Create two PurchaseOrderItems
    poi1 = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=30,
        quantity_allocated=0,
        total_price_amount=300.0,  # 30 qty × $10.0/unit
        currency="USD",
        status=PurchaseOrderItemStatus.CONFIRMED,
    )
    poi2 = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=20,
        quantity_allocated=0,
        total_price_amount=200.0,  # 20 qty × $10.0/unit
        currency="USD",
        status=PurchaseOrderItemStatus.CONFIRMED,
    )

    # Create allocation with sources from both POIs
    allocation = Allocation.objects.create(
        order_line=order_line, stock=stock, quantity_allocated=50
    )
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=poi1, quantity=30
    )
    AllocationSource.objects.create(
        allocation=allocation, purchase_order_item=poi2, quantity=20
    )

    # when/then
    assert can_confirm_order(order) is True


def test_cannot_confirm_order_with_mixed_warehouses(
    order_line, owned_warehouse, nonowned_warehouse, purchase_order_item
):
    """Order cannot be confirmed if some allocations are in non-owned warehouses."""
    # given
    order = order_line.order
    order.status = OrderStatus.UNCONFIRMED
    order.save(update_fields=["status"])

    variant = order_line.variant

    stock_owned, _ = Stock.objects.get_or_create(
        warehouse=owned_warehouse,
        product_variant=variant,
        defaults={"quantity": 100},
    )
    stock_owned.quantity = 100
    stock_owned.save(update_fields=["quantity"])

    stock_nonowned, _ = Stock.objects.get_or_create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        defaults={"quantity": 100},
    )
    stock_nonowned.quantity = 100
    stock_nonowned.save(update_fields=["quantity"])

    # First allocation in owned warehouse with source
    allocation1 = Allocation.objects.create(
        order_line=order_line, stock=stock_owned, quantity_allocated=30
    )
    AllocationSource.objects.create(
        allocation=allocation1, purchase_order_item=purchase_order_item, quantity=30
    )

    # Second allocation in non-owned warehouse (no source created)
    Allocation.objects.create(
        order_line=order_line, stock=stock_nonowned, quantity_allocated=20
    )

    # when/then
    assert can_confirm_order(order) is False


def test_can_confirm_order_multiple_order_lines(
    order_line, owned_warehouse, purchase_order_item, purchase_order, channel_USD
):
    """Order can be confirmed when all order lines have fully sourced allocations."""
    # given
    from ...inventory import PurchaseOrderItemStatus
    from ...inventory.models import PurchaseOrderItem

    order = order_line.order
    order.status = OrderStatus.UNCONFIRMED
    order.save(update_fields=["status"])

    from ...product.models import Product, ProductType

    # Create second product variant
    product_type = ProductType.objects.create(name="Test Type", slug="test-type")
    product2 = Product.objects.create(
        name="Test Product 2",
        slug="test-product-2",
        product_type=product_type,
    )
    variant2 = product2.variants.create(sku="TEST-SKU-2")

    # Create PurchaseOrderItem for variant2
    PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant2,
        quantity_ordered=100,
        quantity_allocated=0,
        total_price_amount=1000.0,  # 100 qty × $10.0/unit
        currency="USD",
        status=PurchaseOrderItemStatus.CONFIRMED,
    )

    # Create second order line
    order_line2 = order.lines.create(
        variant=variant2,
        quantity=10,
        unit_price_gross_amount=20,
        unit_price_net_amount=20,
        total_price_gross_amount=200,
        total_price_net_amount=200,
        currency="USD",
        is_shipping_required=True,
        is_gift_card=False,
    )

    # Allocate first line
    order_line1 = order_line
    stock1, _ = Stock.objects.get_or_create(
        warehouse=owned_warehouse,
        product_variant=order_line1.variant,
        defaults={"quantity": 100},
    )
    stock1.quantity = 100
    stock1.save(update_fields=["quantity"])

    order_line1.quantity = 50
    order_line1.save(update_fields=["quantity"])

    allocate_stocks(
        [OrderLineInfo(line=order_line1, variant=order_line1.variant, quantity=50)],
        COUNTRY_CODE,
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # Allocate second line
    stock2, _ = Stock.objects.get_or_create(
        warehouse=owned_warehouse,
        product_variant=variant2,
        defaults={"quantity": 100},
    )
    stock2.quantity = 100
    stock2.save(update_fields=["quantity"])
    allocate_stocks(
        [OrderLineInfo(line=order_line2, variant=variant2, quantity=10)],
        COUNTRY_CODE,
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # when/then
    assert can_confirm_order(order) is True


def test_cannot_confirm_order_with_one_line_incomplete(
    order_line, owned_warehouse, purchase_order_item, channel_USD
):
    """Order cannot be confirmed if any order line lacks sources."""
    # given
    order = order_line.order
    order.status = OrderStatus.UNCONFIRMED
    order.save(update_fields=["status"])

    from ...product.models import Product, ProductType

    # Create second product variant
    product_type = ProductType.objects.create(name="Test Type", slug="test-type")
    product2 = Product.objects.create(
        name="Test Product 2",
        slug="test-product-2",
        product_type=product_type,
    )
    variant2 = product2.variants.create(sku="TEST-SKU-2")

    # Create second order line (but don't create POI for it - that's the test!)
    order_line2 = order.lines.create(
        variant=variant2,
        quantity=10,
        unit_price_gross_amount=20,
        unit_price_net_amount=20,
        total_price_gross_amount=200,
        total_price_net_amount=200,
        currency="USD",
        is_shipping_required=True,
        is_gift_card=False,
    )

    # Allocate first line (properly sourced)
    order_line1 = order_line
    stock1, _ = Stock.objects.get_or_create(
        warehouse=owned_warehouse,
        product_variant=order_line1.variant,
        defaults={"quantity": 100},
    )
    stock1.quantity = 100
    stock1.save(update_fields=["quantity"])

    order_line1.quantity = 50
    order_line1.save(update_fields=["quantity"])

    allocate_stocks(
        [OrderLineInfo(line=order_line1, variant=order_line1.variant, quantity=50)],
        COUNTRY_CODE,
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # Second line: allocation WITHOUT sources
    stock2, _ = Stock.objects.get_or_create(
        warehouse=owned_warehouse,
        product_variant=variant2,
        defaults={"quantity": 100},
    )
    stock2.quantity = 100
    stock2.save(update_fields=["quantity"])

    Allocation.objects.create(
        order_line=order_line2, stock=stock2, quantity_allocated=10
    )

    # when/then - order cannot be confirmed because line 2 has no sources
    assert can_confirm_order(order) is False


def test_allocate_stocks_auto_confirm_fires_xero_order_confirmed_for_deposit_required(
    order_line,
    owned_warehouse,
    purchase_order_item,
    channel_USD,
    django_capture_on_commit_callbacks,
):
    # given
    order = order_line.order
    order.status = OrderStatus.UNCONFIRMED
    order.deposit_required = True
    order.save(update_fields=["status", "deposit_required"])

    channel_USD.automatically_confirm_all_new_orders = True
    channel_USD.save(update_fields=["automatically_confirm_all_new_orders"])

    variant = order_line.variant
    stock, _ = Stock.objects.get_or_create(
        warehouse=owned_warehouse,
        product_variant=variant,
        defaults={"quantity": 100},
    )
    stock.quantity = 100
    stock.save(update_fields=["quantity"])

    order_line.quantity = 50
    order_line.save(update_fields=["quantity"])

    # when
    with patch(
        "saleor.plugins.manager.PluginsManager.xero_order_confirmed"
    ) as mock_xero:
        with django_capture_on_commit_callbacks(execute=True):
            allocate_stocks(
                [OrderLineInfo(line=order_line, variant=variant, quantity=50)],
                COUNTRY_CODE,
                channel_USD,
                manager=get_plugins_manager(allow_replica=False),
            )

    # then
    order.refresh_from_db()
    assert order.status == OrderStatus.UNFULFILLED
    mock_xero.assert_called_once_with(order)
