"""Dedicated tests for the Stock ↔ POI invariants in owned warehouses.

THE TWO FUNDAMENTAL INVARIANTS:

1. Total Physical Stock - conservation of mass:
    Stock.quantity == sum(POI.quantity_ordered OR quantity_received)

    For any (owned_warehouse, variant) pair, Stock.quantity represents TOTAL physical
    stock from POIs. Use quantity_received if POI status is RECEIVED, else quantity_ordered.
    Consistent with standard Saleor where Stock.quantity = total physical inventory.

2. Allocation Tracking - AllocationSources link Stock to POI batches:
    Stock.quantity_allocated == sum(POI.quantity_allocated)

    For owned warehouses, AllocationSources ensure that Stock.quantity_allocated
    always matches the sum of POI.quantity_allocated across all active POIs.

DERIVED RELATIONSHIP (follows mathematically from the above):
    Available stock: Stock.quantity - Stock.quantity_allocated == sum(POI.available_quantity)
    Where POI.available_quantity = (quantity_ordered OR quantity_received) - quantity_allocated

Any time an invariant is violated we should add a test here. We should also regularly
run an invariant validation to make sure the Stock is in the correct state.
"""

import pytest

from ...inventory import PurchaseOrderItemStatus
from ...inventory.models import PurchaseOrderItem
from ..models import Stock


def calculate_expected_stock_quantity(warehouse, variant):
    """Calculate what Stock.quantity SHOULD be based on POIs (PRIMARY INVARIANT).

    This is the "source of truth" calculation from the POI table.
    Stock.quantity should always equal this value.

    Returns the sum of TOTAL physical stock (ordered/received) from POIs.
    Consistent with Saleor semantics where Stock.quantity = total physical stock.
    """
    pois = PurchaseOrderItem.objects.filter(
        order__destination_warehouse=warehouse,
        product_variant=variant,
        status__in=[
            PurchaseOrderItemStatus.CONFIRMED,
            PurchaseOrderItemStatus.RECEIVED,
        ],
    )

    total = 0
    for poi in pois:
        # Use quantity_received if RECEIVED, otherwise quantity_ordered
        if poi.status == PurchaseOrderItemStatus.RECEIVED:
            total += poi.quantity_received
        else:
            total += poi.quantity_ordered

    return total


def calculate_expected_available_quantity(warehouse, variant):
    """Calculate what available stock SHOULD be based on POIs (SECONDARY INVARIANT).

    This calculates: sum(POI.available_quantity)
    Which should equal: Stock.quantity - Stock.quantity_allocated

    Returns the sum of available (unallocated) quantities from POIs.
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


def calculate_expected_poi_allocated(warehouse, variant):
    """Calculate what POI.quantity_allocated SHOULD sum to (TERTIARY INVARIANT).

    For owned warehouses, this should equal Stock.quantity_allocated
    because AllocationSources link stock allocations to POI allocations.
    """
    pois = PurchaseOrderItem.objects.filter(
        order__destination_warehouse=warehouse,
        product_variant=variant,
        status__in=[
            PurchaseOrderItemStatus.CONFIRMED,
            PurchaseOrderItemStatus.RECEIVED,
        ],
    )

    return sum(poi.quantity_allocated for poi in pois)


def assert_stock_poi_invariant(warehouse, variant):
    """Assert the TWO FUNDAMENTAL INVARIANTS hold for a specific warehouse/variant pair.

    This is THE assertion that proves Stock is a client of POI.

    Checks:
    1. INVARIANT 1: Stock.quantity == sum(POI.quantity_ordered OR quantity_received)
    2. INVARIANT 2: Stock.quantity_allocated == sum(POI.quantity_allocated)
    3. DERIVED (bonus check): Stock.quantity - Stock.quantity_allocated == sum(POI.available_quantity)
    """
    from ...inventory import PurchaseOrderItemStatus
    from ...inventory.models import PurchaseOrderItem

    stock = Stock.objects.filter(warehouse=warehouse, product_variant=variant).first()
    expected_total = calculate_expected_stock_quantity(warehouse, variant)
    expected_available = calculate_expected_available_quantity(warehouse, variant)
    expected_poi_allocated = calculate_expected_poi_allocated(warehouse, variant)

    if stock:
        actual_quantity = stock.quantity
        actual_allocated = stock.quantity_allocated
        actual_available = stock.quantity - stock.quantity_allocated
    else:
        actual_quantity = 0
        actual_allocated = 0
        actual_available = 0

    # Get debug info about POIs
    pois = PurchaseOrderItem.objects.filter(
        order__destination_warehouse=warehouse,
        product_variant=variant,
        status__in=[
            PurchaseOrderItemStatus.CONFIRMED,
            PurchaseOrderItemStatus.RECEIVED,
        ],
    )

    poi_debug = []
    for poi in pois:
        poi_debug.append(
            f"    POI #{poi.id}: ordered={poi.quantity_ordered}, "
            f"allocated={poi.quantity_allocated}, available={poi.available_quantity}, "
            f"status={poi.status}"
        )

    # Check INVARIANT 1: Stock.quantity = total from POIs
    assert actual_quantity == expected_total, (
        f"INVARIANT 1 VIOLATED for {warehouse.name} / {variant.sku}:\n"
        f"  Stock.quantity (total physical): {actual_quantity}\n"
        f"  Expected (sum POI.ordered/received): {expected_total}\n"
        f"  Difference: {actual_quantity - expected_total}\n"
        f"  Stock.quantity_allocated: {actual_allocated}\n"
        f"  POIs:\n" + "\n".join(poi_debug if poi_debug else ["    None"])
    )

    # Check INVARIANT 2: Allocations tracked via POIs
    assert actual_allocated == expected_poi_allocated, (
        f"INVARIANT 2 VIOLATED for {warehouse.name} / {variant.sku}:\n"
        f"  Stock.quantity_allocated: {actual_allocated}\n"
        f"  Expected (sum POI.quantity_allocated): {expected_poi_allocated}\n"
        f"  Difference: {actual_allocated - expected_poi_allocated}\n"
        f"  This means AllocationSources are not properly tracking allocations to POIs!\n"
        f"  POIs:\n" + "\n".join(poi_debug if poi_debug else ["    None"])
    )

    # Check DERIVED relationship: Available stock matches
    assert actual_available == expected_available, (
        f"DERIVED RELATIONSHIP VIOLATED for {warehouse.name} / {variant.sku}:\n"
        f"  Stock.quantity - Stock.quantity_allocated: {actual_available}\n"
        f"  Expected (sum POI.available): {expected_available}\n"
        f"  Difference: {actual_available - expected_available}\n"
        f"  This should be mathematically impossible if Invariants 1 and 2 hold!\n"
        f"  Breakdown:\n"
        f"    Stock.quantity: {actual_quantity}\n"
        f"    Stock.quantity_allocated: {actual_allocated}\n"
        f"  POIs:\n" + "\n".join(poi_debug if poi_debug else ["    None"])
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
        quantity_allocated=0,
        total_price_amount=1000.0,  # 100 qty × $10.0/unit
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
            quantity_allocated=0,
            total_price_amount=1000.0,  # 100 qty × $10.0/unit
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
    """When POIs have allocations, both invariants hold.

    Purpose: Verify that POI allocations are tracked correctly in Stock.
    INVARIANT 1: Stock.quantity == sum(POI.quantity_ordered) = 100 + 80 = 180
    INVARIANT 2: Stock.quantity_allocated == sum(POI.quantity_allocated) = 30 + 80 = 110
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
        quantity_allocated=0,
        total_price_amount=1000.0,  # 100 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    poi2 = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=80,
        quantity_allocated=0,
        total_price_amount=800.0,  # 80 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # when - confirm and simulate allocations
    confirm_purchase_order_item(poi1)
    confirm_purchase_order_item(poi2)

    # Simulate allocations (POI.quantity_allocated updated by allocation process)
    poi1.quantity_allocated = 30
    poi1.save()

    poi2.quantity_allocated = 80
    poi2.save()

    # Update Stock.quantity_allocated to match
    stock = Stock.objects.get(warehouse=owned_warehouse, product_variant=variant)
    stock.quantity_allocated = 110  # 30 + 80 (INVARIANT 2)
    stock.save()
    # Note: Stock.quantity stays at 180 (100 + 80, INVARIANT 1)

    # then - both invariants hold
    assert_stock_poi_invariant(owned_warehouse, variant)


def test_invariant_with_fully_allocated_poi(
    owned_warehouse, variant, purchase_order, nonowned_warehouse
):
    """Fully allocated POI still contributes to Stock.quantity (INVARIANT 1).

    Purpose: Edge case - POI with available_quantity=0 still has total physical stock.
    INVARIANT 1: Stock.quantity == POI.quantity_ordered = 100
    INVARIANT 2: Stock.quantity_allocated == POI.quantity_allocated = 100
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
        quantity_allocated=0,
        total_price_amount=1000.0,  # 100 qty × $10.0/unit
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
    stock.quantity_allocated = 100  # INVARIANT 2
    stock.save()
    # Note: Stock.quantity stays at 100 (INVARIANT 1 - total doesn't change)

    # then - both invariants hold
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
        quantity_allocated=0,
        total_price_amount=1000.0,  # 100 qty × $10.0/unit
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
        quantity_allocated=0,
        total_price_amount=750.0,  # 75 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    poi2 = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant2,
        quantity_ordered=125,
        quantity_allocated=0,
        total_price_amount=1875.0,  # 125 qty × $15.0/unit
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
        quantity_allocated=0,
        total_price_amount=1000.0,  # 100 qty × $10.0/unit
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


def test_invariant_when_confirming_poi_moves_allocations_from_supplier(
    owned_warehouse,
    nonowned_warehouse,
    purchase_order,
    order_line,
    channel_USD,
):
    """Confirming POI moves existing allocations from supplier to owned warehouse.

    Purpose: Test the REALISTIC flow - customer orders first, then we order from supplier.
    This is the most common scenario in production.

    Flow:
    1. Customer places order → allocated from supplier warehouse (non-owned)
    2. We create and confirm PurchaseOrder
    3. Allocations should migrate: supplier → owned warehouse
    4. AllocationSources should be created
    5. Invariant should hold
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...order.fetch import OrderLineInfo
    from ...plugins.manager import get_plugins_manager
    from ...shipping.models import Shipment
    from ..management import allocate_stocks
    from ..models import Allocation

    # CRITICAL: Use order_line.variant for ALL operations
    variant = order_line.variant

    # given - stock at supplier
    Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=200,
        quantity_allocated=0,
    )

    # Step 1: Customer orders (allocates from supplier warehouse)
    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=50)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # Verify: Allocation exists at supplier (non-owned)
    allocation = Allocation.objects.get(order_line=order_line)
    assert allocation.stock.warehouse == nonowned_warehouse
    assert allocation.quantity_allocated == 50
    assert allocation.allocation_sources.count() == 0  # No sources (non-owned)

    # Verify: No stock in owned warehouse yet
    owned_stock = Stock.objects.filter(
        warehouse=owned_warehouse, product_variant=variant
    ).first()
    assert owned_stock is None

    # Step 2: Create and confirm POI (order from supplier)
    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )

    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=100,  # Order 100, but only 50 allocated        quantity_allocated=0,
        total_price_amount=1000.0,  # 100 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # when - confirm POI (this should move allocations)
    confirm_purchase_order_item(poi)

    # then - allocation should have moved to owned warehouse
    allocation.refresh_from_db()
    assert allocation.stock.warehouse == owned_warehouse
    assert allocation.quantity_allocated == 50

    # AllocationSources should now exist (owned warehouse)
    assert allocation.allocation_sources.count() == 1
    source = allocation.allocation_sources.first()
    assert source.purchase_order_item == poi
    assert source.quantity == 50

    # POI should reflect allocation
    poi.refresh_from_db()
    assert poi.quantity_allocated == 50

    # Invariant should hold
    # Stock.quantity = 100 - 50 = 50 (50 allocated, 50 available)
    assert_stock_poi_invariant(owned_warehouse, variant)


def test_invariant_when_multiple_customer_orders_then_confirm_poi(
    owned_warehouse,
    nonowned_warehouse,
    variant,
    purchase_order,
    order,
    channel_USD,
):
    """Multiple customer orders from supplier, then confirm POI.

    Purpose: Test that confirming POI correctly handles multiple pre-existing allocations.

    Flow:
    1. Three customers order from supplier (3 allocations)
    2. We confirm POI
    3. All allocations should migrate
    4. Invariant should hold
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...order.fetch import OrderLineInfo
    from ...plugins.manager import get_plugins_manager
    from ...shipping.models import Shipment
    from ..management import allocate_stocks

    # given - supplier stock
    Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=500,
    )

    # Ensure order lines use the same variant
    order.lines.all().delete()  # Clear any existing lines

    # Create 3 customer orders
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

    # Allocate: 30 + 40 + 20 = 90 units from supplier
    allocate_stocks(
        [OrderLineInfo(line=order_lines[0], variant=variant, quantity=30)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )
    allocate_stocks(
        [OrderLineInfo(line=order_lines[1], variant=variant, quantity=40)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )
    allocate_stocks(
        [OrderLineInfo(line=order_lines[2], variant=variant, quantity=20)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # Verify: All allocations at supplier
    from ..models import Allocation

    allocations = Allocation.objects.filter(order_line__in=order_lines)
    assert allocations.count() == 3
    for alloc in allocations:
        assert alloc.stock.warehouse == nonowned_warehouse

    # when - confirm POI for 150 units (more than allocated)
    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )

    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=150,
        quantity_allocated=0,
        total_price_amount=1500.0,  # 150 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    confirm_purchase_order_item(poi)

    # then - all allocations moved to owned warehouse
    allocations = Allocation.objects.filter(order_line__in=order_lines)
    for alloc in allocations:
        alloc.refresh_from_db()
        assert alloc.stock.warehouse == owned_warehouse
        assert alloc.allocation_sources.count() > 0

    # POI should have 90 allocated (30 + 40 + 20)
    poi.refresh_from_db()
    assert poi.quantity_allocated == 90

    # Invariant should hold
    # Stock.quantity = 150 - 90 = 60 (90 allocated, 60 available)
    assert_stock_poi_invariant(owned_warehouse, variant)


def test_invariant_when_poi_quantity_less_than_allocations(
    owned_warehouse,
    nonowned_warehouse,
    variant,
    purchase_order,
    order,
    channel_USD,
):
    """POI quantity < allocated quantity - partial allocation movement.

    Purpose: Test edge case where we can't move all allocations (POI too small).

    Flow:
    1. Customer orders 100 units from supplier
    2. We only confirm POI for 60 units
    3. Only 60 units of allocation should move
    4. 40 units should stay at supplier
    5. Invariant should hold (only owned warehouse part)
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...order.fetch import OrderLineInfo
    from ...plugins.manager import get_plugins_manager
    from ...shipping.models import Shipment
    from ..management import allocate_stocks
    from ..models import Allocation

    # given - supplier stock
    Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=500,
    )

    # Clear any existing order lines
    order.lines.all().delete()

    # Customer orders 100 units
    order_line = order.lines.create(
        product_name="Product",
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

    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=100)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # when - confirm POI for only 60 units (less than allocated!)
    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )

    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=60,  # Less than 100 allocated!        quantity_allocated=0,
        total_price_amount=600.0,  # 60 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    confirm_purchase_order_item(poi)

    # then - allocation should be moved (possibly split)
    allocations = Allocation.objects.filter(order_line=order_line)

    # The allocation behavior depends on implementation:
    # - Could be 1 allocation moved to owned (if all fits)
    # - Could be 2 allocations (split: owned + supplier)
    # What matters: owned warehouse has the right amount
    owned_alloc = allocations.filter(stock__warehouse=owned_warehouse).first()
    assert owned_alloc is not None
    assert owned_alloc.quantity_allocated == 60

    # If there's leftover at supplier, check it
    supplier_alloc = allocations.filter(stock__warehouse=nonowned_warehouse).first()
    if supplier_alloc:
        assert supplier_alloc.quantity_allocated == 40

    # Owned allocation should have sources
    assert owned_alloc.allocation_sources.count() > 0

    # POI should be fully allocated
    poi.refresh_from_db()
    assert poi.quantity_allocated == 60

    # Invariant should hold for owned warehouse
    # Stock.quantity = 60 - 60 = 0 (all allocated)
    assert_stock_poi_invariant(owned_warehouse, variant)


def test_invariant_when_order_auto_confirms_after_poi_confirmation(
    owned_warehouse,
    nonowned_warehouse,
    variant,
    purchase_order,
    order_line,
    channel_USD,
):
    """Order auto-confirms when POI gives it allocation sources.

    Purpose: Test integration - confirming POI should trigger order confirmation.

    Flow:
    1. Order created (UNCONFIRMED) with allocation from supplier
    2. Confirm POI
    3. Allocation moves and gets sources
    4. Order should auto-confirm (UNCONFIRMED → UNFULFILLED)
    5. Invariant should hold
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...order import OrderStatus
    from ...order.fetch import OrderLineInfo
    from ...plugins.manager import get_plugins_manager
    from ...shipping.models import Shipment
    from ..management import allocate_stocks

    # IMPORTANT: Use order_line.variant
    variant = order_line.variant

    # given - supplier stock
    Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=200,
    )

    # Order is UNCONFIRMED
    order = order_line.order
    order.status = OrderStatus.UNCONFIRMED
    order.save(update_fields=["status"])

    # Allocate from supplier
    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=50)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # Verify order still UNCONFIRMED (no sources yet)
    order.refresh_from_db()
    assert order.status == OrderStatus.UNCONFIRMED

    # when - confirm POI
    shipment = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )

    poi = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=100,
        quantity_allocated=0,
        total_price_amount=1000.0,  # 100 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    confirm_purchase_order_item(poi)

    # then - order should auto-confirm
    order.refresh_from_db()

    # Debug: Check why order didn't auto-confirm
    if order.status != OrderStatus.UNFULFILLED:
        from ..management import can_confirm_order
        from ..models import Allocation

        allocations = Allocation.objects.filter(order_line=order_line)
        debug_info = []
        for alloc in allocations:
            sources = alloc.allocation_sources.all()
            sources_sum = sum(s.quantity for s in sources)
            debug_info.append(
                f"    Allocation: warehouse={alloc.stock.warehouse.name}, "
                f"qty={alloc.quantity_allocated}, sources_count={sources.count()}, "
                f"sources_sum={sources_sum}"
            )

        can_confirm = can_confirm_order(order)

        pytest.fail(
            f"Order did not auto-confirm!\n"
            f"  Order status: {order.status}\n"
            f"  can_confirm_order(): {can_confirm}\n"
            f"  Allocations:\n" + "\n".join(debug_info)
        )

    assert order.status == OrderStatus.UNFULFILLED

    # Invariant should hold
    assert_stock_poi_invariant(owned_warehouse, variant)


def test_invariant_with_mixed_allocations_owned_and_nonowned(
    owned_warehouse,
    nonowned_warehouse,
    variant,
    purchase_order,
    order,
    channel_USD,
):
    """Some allocations in owned, some in non-owned, then confirm more POIs.

    Purpose: Test that system handles mixed state correctly.

    Flow:
    1. Confirm POI1 (creates owned warehouse stock)
    2. Customer orders (some from owned, some from supplier)
    3. Confirm POI2 (moves remaining supplier allocations)
    4. Invariant should hold throughout
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...order.fetch import OrderLineInfo
    from ...plugins.manager import get_plugins_manager
    from ...shipping.models import Shipment
    from ..management import allocate_stocks

    # given - supplier stock
    Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=500,
    )

    # Clear existing order lines
    order.lines.all().delete()

    # Step 1: Confirm first POI (50 units to owned warehouse)
    shipment1 = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-123",
    )

    poi1 = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=50,
        quantity_allocated=0,
        total_price_amount=500.0,  # 50 qty × $10.0/unit
        currency="USD",
        shipment=shipment1,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    confirm_purchase_order_item(poi1)
    # After POI1: supplier=450, owned=50
    # Allocation strategy prefers owned warehouse despite having less stock

    # Invariant check after POI1
    assert_stock_poi_invariant(owned_warehouse, variant)

    # Step 2: Customer orders 80 units total
    # Should allocate: 50 from owned (prioritized) + 30 from supplier
    order_line = order.lines.create(
        product_name="Product",
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

    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=80)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # Verify: Should have allocations totaling 80
    from ..models import Allocation

    allocations = Allocation.objects.filter(order_line=order_line)
    total_allocated = sum(a.quantity_allocated for a in allocations)
    assert total_allocated == 80

    # Check allocations - with updated allocation strategy, should use owned first
    owned_alloc = allocations.filter(stock__warehouse=owned_warehouse).first()
    supplier_alloc = allocations.filter(stock__warehouse=nonowned_warehouse).first()

    # Owned warehouse is prioritized, so should allocate 50 from there
    assert owned_alloc is not None, "Should have allocation from owned warehouse"
    assert owned_alloc.quantity_allocated == 50

    # Remaining 30 should come from supplier
    assert supplier_alloc is not None, "Should have allocation from supplier"
    assert supplier_alloc.quantity_allocated == 30

    # Invariant after mixed allocations
    assert_stock_poi_invariant(owned_warehouse, variant)

    # Step 3: Confirm second POI (100 units)
    shipment2 = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-456",
    )

    poi2 = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=100,
        quantity_allocated=0,
        total_price_amount=1000.0,  # 100 qty × $10.0/unit
        currency="USD",
        shipment=shipment2,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )

    # Check state BEFORE confirming POI2
    Stock.objects.get(warehouse=owned_warehouse, product_variant=variant)

    confirm_purchase_order_item(poi2)

    # Check state AFTER confirming POI2
    Stock.objects.get(warehouse=owned_warehouse, product_variant=variant)
    poi1.refresh_from_db()
    poi2.refresh_from_db()

    # then - supplier allocation should have moved
    allocations = Allocation.objects.filter(order_line=order_line)
    for alloc in allocations:
        alloc.refresh_from_db()
        assert alloc.stock.warehouse == owned_warehouse

    # Final invariant check
    # Stock.quantity = (50 + 100) - 80 = 70
    # 80 allocated across two POIs, 70 available
    assert_stock_poi_invariant(owned_warehouse, variant)


def test_invariant_stress_test_realistic_daily_operations(
    owned_warehouse, variant, purchase_order, nonowned_warehouse, order, channel_USD
):
    """Stress test: simulate realistic daily operations over time.

    Purpose: Integration test with realistic complexity - many operations interleaved.

    Simulates:
    - Day 1: Customer orders (supplier allocation)
    - Day 2: Confirm POI
    - Day 3: More customer orders
    - Day 4: Customer cancels
    - Day 5: Confirm another POI
    - Throughout: Invariant should ALWAYS hold
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...order.fetch import OrderLineInfo
    from ...plugins.manager import get_plugins_manager
    from ...shipping.models import Shipment
    from ..management import allocate_stocks, deallocate_stock

    # Setup: Supplier has stock
    Stock.objects.create(
        warehouse=nonowned_warehouse,
        product_variant=variant,
        quantity=1000,
    )

    # Clear existing order lines
    order.lines.all().delete()

    # Day 1: Three customers order from website (allocates from supplier)
    order_lines = []
    for i, qty in enumerate([30, 50, 20]):
        line = order.lines.create(
            product_name=f"Order {i + 1}",
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
        allocate_stocks(
            [OrderLineInfo(line=line, variant=variant, quantity=qty)],
            "US",
            channel_USD,
            manager=get_plugins_manager(allow_replica=False),
        )
        order_lines.append(line)

    # Invariant: No owned stock yet (all at supplier)
    owned_stock = Stock.objects.filter(
        warehouse=owned_warehouse, product_variant=variant
    ).first()
    assert owned_stock is None

    # Day 2: We order 150 units from supplier (confirm POI)
    shipment1 = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="SHIP-001",
    )
    poi1 = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=150,
        quantity_allocated=0,
        total_price_amount=1500.0,  # 150 qty × $10.0/unit
        currency="USD",
        shipment=shipment1,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )
    confirm_purchase_order_item(poi1)

    # Invariant after POI confirmation
    # Should have: 150 - (30+50+20) = 50 available
    assert_stock_poi_invariant(owned_warehouse, variant)

    # Day 3: Two more customers order (allocate from owned warehouse)
    for i, qty in enumerate([25, 15], start=3):
        line = order.lines.create(
            product_name=f"Order {i + 1}",
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
        allocate_stocks(
            [OrderLineInfo(line=line, variant=variant, quantity=qty)],
            "US",
            channel_USD,
            manager=get_plugins_manager(allow_replica=False),
        )
        order_lines.append(line)

    # Invariant after more allocations
    # Should have: 150 - (30+50+20+25+15) = 10 available
    assert_stock_poi_invariant(owned_warehouse, variant)

    # Day 4: First customer cancels (deallocate 30 units)
    deallocate_stock(
        [OrderLineInfo(line=order_lines[0], variant=variant, quantity=30)],
        manager=get_plugins_manager(allow_replica=False),
    )

    # Invariant after cancellation
    # Should have: 150 - (50+20+25+15) = 40 available
    assert_stock_poi_invariant(owned_warehouse, variant)

    # Day 5: Order more from supplier (80 units)
    shipment2 = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="SHIP-002",
    )
    poi2 = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=80,
        quantity_allocated=0,
        total_price_amount=800.0,  # 80 qty × $10.0/unit
        currency="USD",
        shipment=shipment2,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )
    confirm_purchase_order_item(poi2)

    # Final invariant check
    # Should have: (150 + 80) - (50+20+25+15) = 120 available
    assert_stock_poi_invariant(owned_warehouse, variant)


# ==============================================================================
# EXPLICIT MECHANISM VALIDATION TESTS
# ==============================================================================


def test_allocation_creates_allocation_sources(
    owned_warehouse,
    variant,
    purchase_order,
    nonowned_warehouse,
    order_line,
    channel_USD,
):
    """Allocating creates AllocationSources linking Stock to POI batches.

    Purpose: Explicitly validate INVARIANT 2 mechanism.
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...order.fetch import OrderLineInfo
    from ...plugins.manager import get_plugins_manager
    from ...shipping.models import Shipment
    from ..management import allocate_stocks
    from ..models import Allocation, AllocationSource

    # given
    Stock.objects.create(
        warehouse=nonowned_warehouse, product_variant=variant, quantity=200
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
        quantity_allocated=0,
        total_price_amount=1000.0,  # 100 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )
    confirm_purchase_order_item(poi)
    # After confirmation: nonowned has 100, owned has 100
    # Allocation strategy now prefers owned warehouse

    # when - allocate 60 units
    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=60)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # then - AllocationSources created and POI.quantity_allocated increased
    poi.refresh_from_db()
    assert poi.quantity_allocated == 60

    allocation = Allocation.objects.get(order_line=order_line)
    sources = AllocationSource.objects.filter(allocation=allocation)
    assert sources.count() == 1
    assert sources.first().purchase_order_item == poi
    assert sources.first().quantity == 60

    # and - both invariants hold
    assert_stock_poi_invariant(owned_warehouse, variant)


def test_stock_quantity_constant_during_allocation(
    owned_warehouse,
    variant,
    purchase_order,
    nonowned_warehouse,
    order_line,
    channel_USD,
):
    """Stock.quantity stays constant when allocating (INVARIANT 1).

    Purpose: Explicitly validate that Stock.quantity doesn't change during allocation.
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...order.fetch import OrderLineInfo
    from ...plugins.manager import get_plugins_manager
    from ...shipping.models import Shipment
    from ..management import allocate_stocks, deallocate_stock

    # given
    Stock.objects.create(
        warehouse=nonowned_warehouse, product_variant=variant, quantity=200
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
        quantity_allocated=0,
        total_price_amount=1000.0,  # 100 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )
    confirm_purchase_order_item(poi)
    # After confirmation: nonowned has 100, owned has 100
    # Allocation strategy now prefers owned warehouse

    stock = Stock.objects.get(warehouse=owned_warehouse, product_variant=variant)
    initial_quantity = stock.quantity

    # when - allocate
    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=60)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # then - Stock.quantity unchanged
    stock.refresh_from_db()
    assert stock.quantity == initial_quantity  # INVARIANT 1: total unchanged

    # when - deallocate
    deallocate_stock(
        [OrderLineInfo(line=order_line, variant=variant, quantity=60)],
        manager=get_plugins_manager(allow_replica=False),
    )

    # then - Stock.quantity still unchanged
    stock.refresh_from_db()
    assert stock.quantity == initial_quantity  # Still constant


def test_allocation_spans_poi_batches_fifo(
    owned_warehouse,
    variant,
    purchase_order,
    nonowned_warehouse,
    order_line,
    channel_USD,
):
    """Allocation spanning multiple POIs uses FIFO (oldest first).

    Purpose: Validate INVARIANT 2 with multiple POI batches.
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...order.fetch import OrderLineInfo
    from ...plugins.manager import get_plugins_manager
    from ...shipping.models import Shipment
    from ..management import allocate_stocks

    # given - two POIs
    Stock.objects.create(
        warehouse=nonowned_warehouse, product_variant=variant, quantity=300
    )

    shipment1 = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-1",
    )
    poi1 = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=50,
        quantity_allocated=0,
        total_price_amount=500.0,  # 50 qty × $10.0/unit
        currency="USD",
        shipment=shipment1,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )
    confirm_purchase_order_item(poi1)

    shipment2 = Shipment.objects.create(
        source=nonowned_warehouse.address,
        destination=owned_warehouse.address,
        tracking_number="TEST-2",
    )
    poi2 = PurchaseOrderItem.objects.create(
        order=purchase_order,
        product_variant=variant,
        quantity_ordered=80,
        quantity_allocated=0,
        total_price_amount=800.0,  # 80 qty × $10.0/unit
        currency="USD",
        shipment=shipment2,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )
    confirm_purchase_order_item(poi2)
    # After confirmation: nonowned has 170, owned has 130
    # Allocation strategy now prefers owned warehouse

    # when - allocate 100 (spans both POIs)
    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=100)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # then - FIFO: POI1 fully allocated (50), POI2 partially (50)
    poi1.refresh_from_db()
    poi2.refresh_from_db()
    assert poi1.quantity_allocated == 50
    assert poi2.quantity_allocated == 50

    # and - both invariants hold
    assert_stock_poi_invariant(owned_warehouse, variant)


def test_partial_deallocation(
    owned_warehouse,
    variant,
    purchase_order,
    nonowned_warehouse,
    order_line,
    channel_USD,
):
    """Partial deallocation correctly updates POI.quantity_allocated.

    Purpose: Validate INVARIANT 2 with partial operations.
    """
    from ...inventory.stock_management import confirm_purchase_order_item
    from ...order.fetch import OrderLineInfo
    from ...plugins.manager import get_plugins_manager
    from ...shipping.models import Shipment
    from ..management import allocate_stocks, deallocate_stock

    # given - allocated 100
    Stock.objects.create(
        warehouse=nonowned_warehouse, product_variant=variant, quantity=200
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
        quantity_allocated=0,
        total_price_amount=1000.0,  # 100 qty × $10.0/unit
        currency="USD",
        shipment=shipment,
        country_of_origin="US",
        status=PurchaseOrderItemStatus.DRAFT,
    )
    confirm_purchase_order_item(poi)
    # After confirmation: nonowned has 100, owned has 100
    # Allocation strategy now prefers owned warehouse

    allocate_stocks(
        [OrderLineInfo(line=order_line, variant=variant, quantity=100)],
        "US",
        channel_USD,
        manager=get_plugins_manager(allow_replica=False),
    )

    # when - deallocate only 30 (partial)
    deallocate_stock(
        [OrderLineInfo(line=order_line, variant=variant, quantity=30)],
        manager=get_plugins_manager(allow_replica=False),
    )

    # then - POI.quantity_allocated decreased by 30
    poi.refresh_from_db()
    assert poi.quantity_allocated == 70

    # and - both invariants hold
    assert_stock_poi_invariant(owned_warehouse, variant)

    # when - deallocate remaining 70
    deallocate_stock(
        [OrderLineInfo(line=order_line, variant=variant, quantity=70)],
        manager=get_plugins_manager(allow_replica=False),
    )

    # then - fully deallocated
    poi.refresh_from_db()
    assert poi.quantity_allocated == 0

    # and - invariants still hold
    assert_stock_poi_invariant(owned_warehouse, variant)
