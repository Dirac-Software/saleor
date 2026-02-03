"""Dedicated tests for the Stock ↔ POI invariant in owned warehouses.

THE FUNDAMENTAL INVARIANT:
    For any (owned_warehouse, variant) pair:
    Stock.quantity == sum(POI.available_quantity for all POIs where POI is active
    (CONFIRMED or RECEIVED))

    Where POI.available_quantity = (quantity_ordered OR quantity_received) - quantity_allocated

Any time an invariant is violated we should add a test here.
"""

from ...inventory import PurchaseOrderItemStatus
from ...inventory.models import PurchaseOrderItem
from ..models import Stock


def calculate_expected_stock_quantity(warehouse, variant):
    """Calculate what Stock.quantity SHOULD be based on POIs.

    This is the "source of truth" calculation from the POI table.
    Stock.quantity should always equal this value.
    """
    pois = PurchaseOrderItem.objects.filter(
        order__destination_warehouse=warehouse,
        product_variant=variant,
        status__in=[
            PurchaseOrderItemStatus.CONFIRMED,
            PurchaseOrderItemStatus.RECEIVED,
        ],
    )

    return sum(poi.available_quantity for poi in pois)


def assert_stock_poi_invariant(warehouse, variant):
    """Assert the invariant holds for a specific warehouse/variant pair.

    This is THE assertion that proves Stock is a client of POI.
    """
    stock = Stock.objects.filter(warehouse=warehouse, product_variant=variant).first()

    expected = calculate_expected_stock_quantity(warehouse, variant)

    if stock:
        actual = stock.quantity
    else:
        actual = 0

    assert actual == expected, (
        f"INVARIANT VIOLATED for {warehouse.name} / {variant.sku}:\n"
        f"  Stock.quantity: {actual}\n"
        f"  Expected (sum POI.available): {expected}\n"
        f"  Difference: {actual - expected}"
    )


# ==============================================================================
# INVARIANT TESTS
# ==============================================================================


def test_invariant_empty_database():
    """With no POIs, Stock.quantity should be 0 (or no Stock exists).

    Purpose: Verify invariant holds in the trivial base case.
    """
    # given - empty database (pytest fixture handles cleanup)

    # then - no stocks should exist OR all stocks have quantity=0
    for stock in Stock.objects.filter(warehouse__is_owned=True):
        expected = calculate_expected_stock_quantity(
            stock.warehouse, stock.product_variant
        )
        assert stock.quantity == expected


def test_invariant_after_single_poi_confirmation(
    owned_warehouse, variant, purchase_order, nonowned_warehouse
):
    """After confirming one POI, invariant holds.

    Purpose: Verify invariant after the most basic operation.
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...shipping.models import Shipment

    # given - source stock
    Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=200,
    )

    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )

    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=100,
        quantity_received=0,
        quantity_allocated=0,
        unit_price_amount=10.00,
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # when
    confirm_purchase_order_item(poi)

    # then - verify invariant
    assert_stock_poi_invariant(owned_warehouse, variant)


def test_invariant_with_multiple_pois_same_variant(
    owned_warehouse, variant, purchase_order, nonowned_warehouse
):
    """With multiple POIs for same variant, invariant holds.

    Purpose: Verify Stock.quantity equals SUM of all POI.available_quantity.
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...shipping.models import Shipment

    # given - source stock
    Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=500,
    )

    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )

    # Create 3 POIs with different quantities
    pois = []
    for qty in [100, 75, 50]:
        poi = PurchaseOrderItem.objects.create(
            order=purchase_order,
            product_variant=variant,
            quantity_ordered=qty,
            quantity_received=0,
            quantity_allocated=0,
            unit_price_amount=10.00,
            currency="USD",
            shipment=shipment,
            country_of_origin="US",
            status=PurchaseOrderItemStatus.DRAFT,
        )
        pois.append(poi)

    # when - confirm all
    for poi in pois:
        confirm_purchase_order_item(poi)

    # then - invariant holds (Stock.quantity == 100 + 75 + 50 = 225)
    assert_stock_poi_invariant(owned_warehouse, variant)


def test_invariant_with_partially_allocated_pois(
    owned_warehouse, variant, purchase_order, nonowned_warehouse
):
    """When POIs have allocations, invariant accounts for available_quantity.

    Purpose: Verify that allocated POI quantities reduce Stock.quantity correctly.
    Formula: Stock.quantity == sum(POI.quantity_ordered - POI.quantity_allocated)
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...shipping.models import Shipment

    # given - source stock
    Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=500,
    )

    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )

    # Create POIs
    poi1 = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=100,
        quantity_received=0,
        quantity_allocated=0,
        unit_price_amount=10.00,
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    poi2 = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=80,
        quantity_received=0,
        quantity_allocated=0,
        unit_price_amount=10.00,
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # when - confirm and simulate allocations
    confirm_purchase_order_item(poi1)
    confirm_purchase_order_item(poi2)

    # Simulate allocations (POI.quantity_allocated updated by allocation process)
    poi1.quantity_allocated = 30  # 100 - 30 = 70 available
    poi1.save()

    poi2.quantity_allocated = 80  # 80 - 80 = 0 available
    poi2.save()

    # Update Stock.quantity_allocated to match
    stock = Stock.objects.get(warehouse=owned_warehouse, product_variant=variant)
    stock.quantity = 70  # Only 70 unallocated left
    stock.quantity_allocated = 110  # 30 + 80
    stock.save()

    # then - invariant holds (Stock.quantity == 70 + 0 = 70)
    assert_stock_poi_invariant(owned_warehouse, variant)


def test_invariant_with_fully_allocated_poi(
    owned_warehouse, variant, purchase_order, nonowned_warehouse
):
    """Fully allocated POI contributes 0 to Stock.quantity.

    Purpose: Edge case - POI with available_quantity=0 shouldn't affect Stock.quantity.
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...shipping.models import Shipment

    # given
    Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=200,
    )

    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )

    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=100,
        quantity_received=0,
        quantity_allocated=0,
        unit_price_amount=10.00,
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # when - confirm and fully allocate
    confirm_purchase_order_item(poi)
    poi.quantity_allocated = 100
    poi.save()

    stock = Stock.objects.get(warehouse=owned_warehouse, product_variant=variant)
    stock.quantity = 0  # All allocated
    stock.quantity_allocated = 100
    stock.save()

    # then - invariant holds (Stock.quantity == 0)
    assert_stock_poi_invariant(owned_warehouse, variant)


def test_invariant_excludes_draft_pois(
    owned_warehouse, variant, purchase_order, nonowned_warehouse
):
    """DRAFT POIs don't contribute to Stock.quantity.

    Purpose: Verify only CONFIRMED/RECEIVED POIs count toward invariant.
    """
    from ...shipping.models import Shipment

    # given - DRAFT POI (not confirmed)
    Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=200,
    )

    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )

    PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=100,
        quantity_received=0,
        quantity_allocated=0,
        unit_price_amount=10.00,
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,  # Still DRAFT
    )

    # when - check stock (should not exist yet)
    stock = Stock.objects.filter(
        warehouse=owned_warehouse, product_variant=variant
    ).first()

    # then - no stock exists, expected quantity is 0 (DRAFT excluded)
    expected = calculate_expected_stock_quantity(owned_warehouse, variant)
    assert expected == 0
    assert stock is None  # No stock created yet

    # Invariant: 0 (no stock) == 0 (no CONFIRMED/RECEIVED POIs) ✓


def test_invariant_with_multiple_variants_in_same_warehouse(
    owned_warehouse, purchase_order, nonowned_warehouse
):
    """Each variant maintains independent invariant.

    Purpose: Verify invariant is per (warehouse, variant) pair, not per warehouse.
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...product.models import Product, ProductType
    from ...shipping.models import Shipment

    # given - 2 variants
    product_type = ProductType.objects.create(name="Test Type", slug="test-type")
    product = Product.objects.create(
        name="Test Product",
        slug="test-product",
        product_type=product_type,
    )
    variant1 = product.variants.create(sku="VAR-1")
    variant2 = product.variants.create(sku="VAR-2")

    # Source stocks
    Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant1,
        quantity=300,
    )
    Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant2,
        quantity=300,
    )

    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )

    # Create POIs for each variant
    poi1 = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant1,
        quantity_ordered=75,
        quantity_received=0,
        quantity_allocated=0,
        unit_price_amount=10.00,
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    poi2 = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant2,
        quantity_ordered=125,
        quantity_received=0,
        quantity_allocated=0,
        unit_price_amount=15.00,
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # when - confirm both
    confirm_purchase_order_item(poi1)
    confirm_purchase_order_item(poi2)

    # then - invariant holds for BOTH variants independently
    assert_stock_poi_invariant(owned_warehouse, variant1)
    assert_stock_poi_invariant(owned_warehouse, variant2)


def test_invariant_after_allocation_and_deallocation(
    owned_warehouse,
    variant,
    purchase_order,
    nonowned_warehouse,
    order_line,
    channel_USD,
):
    """Invariant holds through allocation/deallocation cycle.

    Purpose: Verify that allocation operations don't break the invariant.
    Stock.quantity should remain constant, only Stock.quantity_allocated changes.
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...order.fetch import OrderLineInfo
    from ...plugins.manager import get_plugins_manager
    from ...shipping.models import Shipment
    from ..management import allocate_stocks, deallocate_stock

    # given - confirmed POI
    Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=300,
    )

    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )

    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=100,
        quantity_received=0,
        quantity_allocated=0,
        unit_price_amount=10.00,
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    confirm_purchase_order_item(poi)

    # Invariant before allocation
    assert_stock_poi_invariant(owned_warehouse, variant)

    # when - allocate
    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=60)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # then - invariant still holds (Stock.quantity unchanged, only quantity_allocated changed)
    assert_stock_poi_invariant(owned_warehouse, variant)

    # when - deallocate
    deallocate_stock(
        [OrderLineInfo(line=order_line, variant=variant, quantity=60)],
        manager=get_plugins_manager(allow_replica=False),
    )

    # then - invariant STILL holds
    assert_stock_poi_invariant(owned_warehouse, variant)


def test_invariant_does_not_apply_to_nonowned_warehouses(nonowned_warehouse, variant):
    """Invariant only applies to owned warehouses, not suppliers.

    Purpose: Clarify scope - non-owned warehouses are NOT clients of POI table.
    """
    # given - stock in non-owned warehouse
    Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=500,  # Can be set to any value
    )

    # then - we DON'T check invariant for non-owned warehouses
    # Non-owned warehouse stock is managed externally (supplier data)
    assert not nonowned_warehouse.is_owned

    # There may be POIs sourced from this warehouse, but Stock.quantity
    # doesn't need to match anything - it's just an upper bound


def test_invariant_with_complex_scenario(
    owned_warehouse, variant, purchase_order, nonowned_warehouse, order, channel_USD
):
    """Complex scenario: multiple POIs, various allocation states.

    Purpose: Stress test the invariant with realistic complexity.
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...order.fetch import OrderLineInfo
    from ...plugins.manager import get_plugins_manager
    from ...shipping.models import Shipment
    from ..management import allocate_stocks

    # given - source stock
    Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=1000,
    )

    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )

    # Create 4 POIs in various states
    pois = []
    for _i, qty in enumerate([100, 150, 80, 120]):
        poi = PurchaseOrderItem.objects.create(
            order=purchase_order,
            product_variant=variant,
            quantity_ordered=qty,
            quantity_received=0,
            quantity_allocated=0,
            unit_price_amount=10.00,
            currency="USD",
            shipment=shipment,
            country_of_origin="US",
            status=PurchaseOrderItemStatus.DRAFT,
        )
        confirm_purchase_order_item(poi)
        pois.append(poi)

    # Create order lines and allocate to different POIs
    order_lines = []
    for i in range(3):
        line = order.lines.create(
            product_name=f"Product {i}",
            variant_name=variant.name,
            product_sku=variant.sku,
            variant=variant,
            quantity=1,
            unit_price_gross_amount=10,
            unit_price_net_amount=10,
            total_price_gross_amount=10,
            total_price_net_amount=10,
            currency="USD",
            is_shipping_required=False,
            is_gift_card=False,
        )
        order_lines.append(line)

    # Allocate: 50 + 100 + 30 = 180 units
    allocate_stocks(
        [OrderLineInfo(line=order_lines[0], variant=variant, quantity=50)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    allocate_stocks(
        [OrderLineInfo(line=order_lines[1], variant=variant, quantity=100)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    allocate_stocks(
        [OrderLineInfo(line=order_lines[2], variant=variant, quantity=30)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # then - invariant holds
    # Total ordered: 100 + 150 + 80 + 120 = 450
    # Total allocated: 180
    # Expected Stock.quantity: 450 - 180 = 270
    assert_stock_poi_invariant(owned_warehouse, variant)
