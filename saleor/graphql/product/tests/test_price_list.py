"""GraphQL tests for PriceList queries and mutations."""

from decimal import Decimal
from unittest.mock import patch

import graphene
import pytest

from ....product.models import PriceList, PriceListItem
from ...tests.utils import assert_no_permission, get_graphql_content

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def non_owned_warehouse(db):
    from saleor.account.models import Address

    address = Address.objects.create(
        street_address_1="1 Test St",
        city="London",
        country="GB",
    )
    from saleor.warehouse.models import Warehouse

    return Warehouse.objects.create(
        name="External Warehouse",
        slug="external-warehouse",
        address=address,
        is_owned=False,
    )


@pytest.fixture
def channel_gbp(db):
    from saleor.channel.models import Channel

    return Channel.objects.create(
        name="GBP Channel",
        slug="gbp-channel",
        currency_code="GBP",
        default_country="GB",
        is_active=True,
    )


@pytest.fixture
def processed_price_list(db, non_owned_warehouse, channel_gbp):
    from django.utils import timezone

    pl = PriceList.objects.create(
        warehouse=non_owned_warehouse,
        name="Test Price List",
        config={},
        processing_completed_at=timezone.now(),
    )
    pl.channels.set([channel_gbp])
    PriceListItem.objects.create(
        price_list=pl,
        row_index=0,
        product_code="TEST-001",
        brand="TestBrand",
        description="Test Product",
        category="Apparel",
        sizes_and_qty={"S": 10, "M": 20},
        sell_price=Decimal("25.00"),
        currency="GBP",
        is_valid=True,
    )
    return pl


@pytest.fixture
def active_price_list(processed_price_list):
    from saleor.product import PriceListStatus

    processed_price_list.status = PriceListStatus.ACTIVE
    processed_price_list.save(update_fields=["status"])
    return processed_price_list


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

PRICE_LIST_QUERY = """
    query PriceList($id: ID!) {
        priceList(id: $id) {
            id
            name
            status
            itemCount
            validItemCount
            channels {
                id
                slug
            }
            items(first: 10) {
                edges {
                    node {
                        id
                        productCode
                        brand
                        isValid
                    }
                }
            }
        }
    }
"""

PRICE_LISTS_QUERY = """
    query PriceLists($first: Int) {
        priceLists(first: $first) {
            edges {
                node {
                    id
                    status
                }
            }
            totalCount
        }
    }
"""


def test_price_list_query(
    staff_api_client, permission_manage_products, processed_price_list, channel_gbp
):
    staff_api_client.user.user_permissions.add(permission_manage_products)

    variables = {
        "id": graphene.Node.to_global_id("PriceList", processed_price_list.pk),
    }
    response = staff_api_client.post_graphql(PRICE_LIST_QUERY, variables=variables)
    content = get_graphql_content(response)
    data = content["data"]["priceList"]

    assert data is not None
    assert data["name"] == "Test Price List"
    assert data["status"] == "INACTIVE"
    assert data["itemCount"] == 1
    assert data["validItemCount"] == 1
    assert len(data["channels"]) == 1
    assert data["channels"][0]["slug"] == channel_gbp.slug
    assert len(data["items"]["edges"]) == 1
    item = data["items"]["edges"][0]["node"]
    assert item["productCode"] == "TEST-001"
    assert item["brand"] == "TestBrand"
    assert item["isValid"] is True


def test_price_list_query_requires_permission(api_client, processed_price_list):
    variables = {
        "id": graphene.Node.to_global_id("PriceList", processed_price_list.pk),
    }
    response = api_client.post_graphql(PRICE_LIST_QUERY, variables=variables)
    assert_no_permission(response)


PRICE_LIST_ITEMS_FILTER_QUERY = """
    query PriceList($id: ID!, $filter: PriceListItemFilterInput) {
        priceList(id: $id) {
            items(first: 10, filter: $filter) {
                edges {
                    node {
                        productCode
                        isValid
                    }
                }
            }
        }
    }
"""


def test_price_list_items_filter_invalid(
    staff_api_client, permission_manage_products, non_owned_warehouse, channel_gbp
):
    from django.utils import timezone

    staff_api_client.user.user_permissions.add(permission_manage_products)
    pl = PriceList.objects.create(
        warehouse=non_owned_warehouse,
        name="Filter Test",
        config={},
        processing_completed_at=timezone.now(),
    )
    PriceListItem.objects.create(
        price_list=pl,
        row_index=0,
        product_code="VALID-001",
        brand="B",
        description="Good",
        category="Apparel",
        sizes_and_qty={"S": 1},
        sell_price=Decimal("10.00"),
        currency="GBP",
        is_valid=True,
    )
    PriceListItem.objects.create(
        price_list=pl,
        row_index=1,
        product_code="BAD-001",
        brand="B",
        description="Bad",
        category="Apparel",
        sizes_and_qty={},
        sell_price=Decimal("10.00"),
        currency="GBP",
        is_valid=False,
        validation_errors=["product_code: required"],
    )

    variables = {
        "id": graphene.Node.to_global_id("PriceList", pl.pk),
        "filter": {"isValid": False},
    }
    response = staff_api_client.post_graphql(
        PRICE_LIST_ITEMS_FILTER_QUERY, variables=variables
    )
    content = get_graphql_content(response)
    edges = content["data"]["priceList"]["items"]["edges"]

    assert len(edges) == 1
    assert edges[0]["node"]["productCode"] == "BAD-001"
    assert edges[0]["node"]["isValid"] is False


def test_price_list_items_filter_valid(
    staff_api_client, permission_manage_products, non_owned_warehouse, channel_gbp
):
    from django.utils import timezone

    staff_api_client.user.user_permissions.add(permission_manage_products)
    pl = PriceList.objects.create(
        warehouse=non_owned_warehouse,
        name="Filter Test 2",
        config={},
        processing_completed_at=timezone.now(),
    )
    PriceListItem.objects.create(
        price_list=pl,
        row_index=0,
        product_code="VALID-001",
        brand="B",
        description="Good",
        category="Apparel",
        sizes_and_qty={"S": 1},
        sell_price=Decimal("10.00"),
        currency="GBP",
        is_valid=True,
    )
    PriceListItem.objects.create(
        price_list=pl,
        row_index=1,
        product_code="BAD-001",
        brand="B",
        description="Bad",
        category="Apparel",
        sizes_and_qty={},
        sell_price=Decimal("10.00"),
        currency="GBP",
        is_valid=False,
        validation_errors=["product_code: required"],
    )

    variables = {
        "id": graphene.Node.to_global_id("PriceList", pl.pk),
        "filter": {"isValid": True},
    }
    response = staff_api_client.post_graphql(
        PRICE_LIST_ITEMS_FILTER_QUERY, variables=variables
    )
    content = get_graphql_content(response)
    edges = content["data"]["priceList"]["items"]["edges"]

    assert len(edges) == 1
    assert edges[0]["node"]["productCode"] == "VALID-001"
    assert edges[0]["node"]["isValid"] is True


def test_price_list_items_no_filter_returns_all(
    staff_api_client, permission_manage_products, non_owned_warehouse, channel_gbp
):
    from django.utils import timezone

    staff_api_client.user.user_permissions.add(permission_manage_products)
    pl = PriceList.objects.create(
        warehouse=non_owned_warehouse,
        name="Filter Test 3",
        config={},
        processing_completed_at=timezone.now(),
    )
    PriceListItem.objects.create(
        price_list=pl,
        row_index=0,
        product_code="VALID-001",
        brand="B",
        description="Good",
        category="Apparel",
        sizes_and_qty={"S": 1},
        sell_price=Decimal("10.00"),
        currency="GBP",
        is_valid=True,
    )
    PriceListItem.objects.create(
        price_list=pl,
        row_index=1,
        product_code="BAD-001",
        brand="B",
        description="Bad",
        category="Apparel",
        sizes_and_qty={},
        sell_price=Decimal("10.00"),
        currency="GBP",
        is_valid=False,
        validation_errors=["product_code: required"],
    )

    variables = {
        "id": graphene.Node.to_global_id("PriceList", pl.pk),
    }
    response = staff_api_client.post_graphql(
        PRICE_LIST_ITEMS_FILTER_QUERY, variables=variables
    )
    content = get_graphql_content(response)
    edges = content["data"]["priceList"]["items"]["edges"]

    assert len(edges) == 2


def test_price_lists_query(
    staff_api_client,
    permission_manage_products,
    processed_price_list,
    non_owned_warehouse,
):
    from django.utils import timezone

    second_pl = PriceList.objects.create(
        warehouse=non_owned_warehouse, config={}, processing_completed_at=timezone.now()
    )

    staff_api_client.user.user_permissions.add(permission_manage_products)

    response = staff_api_client.post_graphql(PRICE_LISTS_QUERY, variables={"first": 10})
    content = get_graphql_content(response)
    data = content["data"]["priceLists"]

    ids = {edge["node"]["id"] for edge in data["edges"]}
    assert graphene.Node.to_global_id("PriceList", processed_price_list.pk) in ids
    assert graphene.Node.to_global_id("PriceList", second_pl.pk) in ids


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

PRICE_LIST_ACTIVATE_MUTATION = """
    mutation PriceListActivate($id: ID!) {
        priceListActivate(id: $id) {
            priceList {
                id
                status
            }
            errors {
                field
                code
                message
            }
        }
    }
"""

PRICE_LIST_DEACTIVATE_MUTATION = """
    mutation PriceListDeactivate($id: ID!) {
        priceListDeactivate(id: $id) {
            priceList {
                id
                status
            }
            errors {
                field
                message
            }
        }
    }
"""

PRICE_LIST_REPLACE_MUTATION = """
    mutation PriceListReplace($oldId: ID!, $newId: ID!) {
        priceListReplace(oldPriceListId: $oldId, newPriceListId: $newId) {
            oldPriceList { id status }
            newPriceList { id status }
            errors { field message }
        }
    }
"""

PRICE_LIST_DELETE_MUTATION = """
    mutation PriceListDelete($id: ID!) {
        priceListDelete(id: $id) {
            priceListId
            errors {
                field
                message
            }
        }
    }
"""


def test_price_list_activate_dispatches_task(
    staff_api_client, permission_manage_products, processed_price_list
):
    staff_api_client.user.user_permissions.add(permission_manage_products)

    variables = {
        "id": graphene.Node.to_global_id("PriceList", processed_price_list.pk),
    }
    with patch("saleor.product.tasks.activate_price_list_task.delay") as mock_delay:
        response = staff_api_client.post_graphql(
            PRICE_LIST_ACTIVATE_MUTATION, variables=variables
        )

    content = get_graphql_content(response)
    data = content["data"]["priceListActivate"]

    assert data["errors"] == []
    assert data["priceList"]["id"] == graphene.Node.to_global_id(
        "PriceList", processed_price_list.pk
    )
    mock_delay.assert_called_once_with(processed_price_list.pk)


def test_price_list_activate_requires_permission(api_client, processed_price_list):
    variables = {
        "id": graphene.Node.to_global_id("PriceList", processed_price_list.pk),
    }
    response = api_client.post_graphql(
        PRICE_LIST_ACTIVATE_MUTATION, variables=variables
    )
    assert_no_permission(response)


def test_price_list_activate_errors_when_not_processed(
    staff_api_client, permission_manage_products, non_owned_warehouse, channel_gbp
):
    pl = PriceList.objects.create(
        warehouse=non_owned_warehouse,
        name="Unprocessed Price List",
        config={},
    )
    pl.channels.set([channel_gbp])
    staff_api_client.user.user_permissions.add(permission_manage_products)

    variables = {"id": graphene.Node.to_global_id("PriceList", pl.pk)}
    response = staff_api_client.post_graphql(
        PRICE_LIST_ACTIVATE_MUTATION, variables=variables
    )

    content = get_graphql_content(response)
    errors = content["data"]["priceListActivate"]["errors"]
    assert len(errors) == 1
    assert errors[0]["field"] == "id"
    assert errors[0]["code"] == "INVALID"


def test_price_list_activate_errors_for_owned_warehouse(
    staff_api_client, permission_manage_products, channel_gbp
):
    from django.utils import timezone

    from saleor.account.models import Address
    from saleor.warehouse.models import Warehouse

    address = Address.objects.create(
        street_address_1="1 Owned St", city="London", country="GB"
    )
    owned_warehouse = Warehouse.objects.create(
        name="Owned Warehouse",
        slug="owned-warehouse",
        address=address,
        is_owned=True,
    )
    pl = PriceList.objects.create(
        warehouse=owned_warehouse,
        name="Owned Warehouse Price List",
        config={},
        processing_completed_at=timezone.now(),
    )
    pl.channels.set([channel_gbp])
    staff_api_client.user.user_permissions.add(permission_manage_products)

    variables = {"id": graphene.Node.to_global_id("PriceList", pl.pk)}
    response = staff_api_client.post_graphql(
        PRICE_LIST_ACTIVATE_MUTATION, variables=variables
    )

    content = get_graphql_content(response)
    errors = content["data"]["priceListActivate"]["errors"]
    assert len(errors) == 1
    assert errors[0]["field"] == "id"
    assert errors[0]["code"] == "OWNED_WAREHOUSE"


def test_price_list_deactivate_dispatches_task(
    staff_api_client, permission_manage_products, active_price_list
):
    staff_api_client.user.user_permissions.add(permission_manage_products)

    variables = {
        "id": graphene.Node.to_global_id("PriceList", active_price_list.pk),
    }
    with patch("saleor.product.tasks.deactivate_price_list_task.delay") as mock_delay:
        response = staff_api_client.post_graphql(
            PRICE_LIST_DEACTIVATE_MUTATION, variables=variables
        )

    content = get_graphql_content(response)
    data = content["data"]["priceListDeactivate"]

    assert data["errors"] == []
    assert data["priceList"]["id"] == graphene.Node.to_global_id(
        "PriceList", active_price_list.pk
    )
    mock_delay.assert_called_once_with(active_price_list.pk)


def test_price_list_replace_dispatches_task(
    staff_api_client, permission_manage_products, active_price_list, non_owned_warehouse
):
    from django.utils import timezone

    staff_api_client.user.user_permissions.add(permission_manage_products)

    new_pl = PriceList.objects.create(
        warehouse=non_owned_warehouse,
        config={},
        processing_completed_at=timezone.now(),
    )

    variables = {
        "oldId": graphene.Node.to_global_id("PriceList", active_price_list.pk),
        "newId": graphene.Node.to_global_id("PriceList", new_pl.pk),
    }
    with patch("saleor.product.tasks.replace_price_list_task.delay") as mock_delay:
        response = staff_api_client.post_graphql(
            PRICE_LIST_REPLACE_MUTATION, variables=variables
        )

    content = get_graphql_content(response)
    data = content["data"]["priceListReplace"]

    assert data["errors"] == []
    assert data["oldPriceList"]["id"] == graphene.Node.to_global_id(
        "PriceList", active_price_list.pk
    )
    assert data["newPriceList"]["id"] == graphene.Node.to_global_id(
        "PriceList", new_pl.pk
    )
    mock_delay.assert_called_once_with(active_price_list.pk, new_pl.pk)


def test_price_list_delete_inactive(
    staff_api_client, permission_manage_products, processed_price_list
):
    staff_api_client.user.user_permissions.add(permission_manage_products)
    pk = processed_price_list.pk

    variables = {
        "id": graphene.Node.to_global_id("PriceList", pk),
    }
    response = staff_api_client.post_graphql(
        PRICE_LIST_DELETE_MUTATION, variables=variables
    )
    content = get_graphql_content(response)
    data = content["data"]["priceListDelete"]

    assert data["errors"] == []
    assert not PriceList.objects.filter(pk=pk).exists()


def test_price_list_delete_active_returns_error(
    staff_api_client, permission_manage_products, active_price_list
):
    staff_api_client.user.user_permissions.add(permission_manage_products)
    pk = active_price_list.pk

    variables = {
        "id": graphene.Node.to_global_id("PriceList", pk),
    }
    response = staff_api_client.post_graphql(
        PRICE_LIST_DELETE_MUTATION, variables=variables
    )
    content = get_graphql_content(response)
    data = content["data"]["priceListDelete"]

    assert len(data["errors"]) == 1
    assert "active" in data["errors"][0]["message"].lower()
    assert PriceList.objects.filter(pk=pk).exists()


def test_price_list_delete_requires_permission(api_client, processed_price_list):
    variables = {
        "id": graphene.Node.to_global_id("PriceList", processed_price_list.pk),
    }
    response = api_client.post_graphql(PRICE_LIST_DELETE_MUTATION, variables=variables)
    assert_no_permission(response)


# ---------------------------------------------------------------------------
# Channel and name tests
# ---------------------------------------------------------------------------


PRICE_LIST_CREATE_MUTATION = """
    mutation PriceListCreate($input: PriceListCreateInput!) {
        priceListCreate(input: $input) {
            priceList {
                id
                name
                channels {
                    slug
                }
            }
            errors {
                field
                message
            }
        }
    }
"""


def test_price_list_create_requires_at_least_one_channel(
    staff_api_client, permission_manage_products, non_owned_warehouse
):
    staff_api_client.user.user_permissions.add(permission_manage_products)

    variables = {
        "input": {
            "warehouseId": graphene.Node.to_global_id(
                "Warehouse", non_owned_warehouse.pk
            ),
            "channelIds": [],
            "defaultCurrency": "GBP",
            "columnMap": {},
        }
    }
    response = staff_api_client.post_graphql(
        PRICE_LIST_CREATE_MUTATION, variables=variables
    )
    content = get_graphql_content(response)
    assert content["data"]["priceListCreate"] is not None
    errors = content["data"]["priceListCreate"]["errors"]
    assert any(e["field"] == "channelIds" for e in errors)


def test_price_list_create_validates_currency_matches_channels(
    staff_api_client, permission_manage_products, non_owned_warehouse, channel_gbp
):
    staff_api_client.user.user_permissions.add(permission_manage_products)

    variables = {
        "input": {
            "warehouseId": graphene.Node.to_global_id(
                "Warehouse", non_owned_warehouse.pk
            ),
            "channelIds": [graphene.Node.to_global_id("Channel", channel_gbp.pk)],
            "defaultCurrency": "USD",
            "columnMap": {},
        }
    }
    response = staff_api_client.post_graphql(
        PRICE_LIST_CREATE_MUTATION, variables=variables
    )
    content = get_graphql_content(response)
    assert content["data"]["priceListCreate"] is not None
    errors = content["data"]["priceListCreate"]["errors"]
    assert any(e["field"] == "channelIds" for e in errors)


def test_price_lists_query_includes_channels(
    staff_api_client,
    permission_manage_products,
    processed_price_list,
    channel_gbp,
):
    staff_api_client.user.user_permissions.add(permission_manage_products)

    response = staff_api_client.post_graphql(
        """
        query {
            priceLists(first: 10) {
                edges {
                    node {
                        id
                        name
                        channels { slug }
                    }
                }
            }
        }
        """
    )
    content = get_graphql_content(response)
    edges = content["data"]["priceLists"]["edges"]
    assert len(edges) == 1
    node = edges[0]["node"]
    assert node["name"] == "Test Price List"
    assert any(ch["slug"] == channel_gbp.slug for ch in node["channels"])


# ---------------------------------------------------------------------------
# excelFileUrl field tests
# ---------------------------------------------------------------------------

EXCEL_FILE_URL_QUERY = """
    query PriceList($id: ID!) {
        priceList(id: $id) {
            excelFileUrl
        }
    }
"""


def test_excel_file_url_returns_url_when_file_attached(
    staff_api_client, permission_manage_products, non_owned_warehouse, tmp_path
):
    import openpyxl
    from django.core.files import File

    wb = openpyxl.Workbook()
    path = tmp_path / "test.xlsx"
    wb.save(path)

    with open(path, "rb") as f:
        pl = PriceList.objects.create(
            warehouse=non_owned_warehouse,
            name="With File",
            config={},
            excel_file=File(f, name="test.xlsx"),
        )

    staff_api_client.user.user_permissions.add(permission_manage_products)
    variables = {"id": graphene.Node.to_global_id("PriceList", pl.pk)}
    response = staff_api_client.post_graphql(EXCEL_FILE_URL_QUERY, variables=variables)
    content = get_graphql_content(response)
    url = content["data"]["priceList"]["excelFileUrl"]

    assert url is not None
    assert f"/media/price_lists/{pl.pk}/" in url


def test_excel_file_url_is_none_when_no_file(
    staff_api_client, permission_manage_products, processed_price_list
):
    staff_api_client.user.user_permissions.add(permission_manage_products)
    variables = {"id": graphene.Node.to_global_id("PriceList", processed_price_list.pk)}
    response = staff_api_client.post_graphql(EXCEL_FILE_URL_QUERY, variables=variables)
    content = get_graphql_content(response)
    assert content["data"]["priceList"]["excelFileUrl"] is None


# ---------------------------------------------------------------------------
# force= pre-check mutation tests
# ---------------------------------------------------------------------------

PRICE_LIST_DEACTIVATE_FORCE_MUTATION = """
    mutation PriceListDeactivate($id: ID!, $force: Boolean) {
        priceListDeactivate(id: $id, force: $force) {
            priceList { id status }
            errors { field message code }
        }
    }
"""

PRICE_LIST_REPLACE_FORCE_MUTATION = """
    mutation PriceListReplace($oldId: ID!, $newId: ID!, $force: Boolean) {
        priceListReplace(oldPriceListId: $oldId, newPriceListId: $newId, force: $force) {
            oldPriceList { id status }
            newPriceList { id status }
            errors { field message code }
        }
    }
"""


def _make_unconfirmed_allocation_for_pl(price_list, warehouse):
    """Create a stock + unconfirmed order + allocation for the first product in a price list."""
    from decimal import Decimal

    from saleor.channel.models import Channel
    from saleor.order import OrderOrigin, OrderStatus
    from saleor.order.models import Order, OrderLine
    from saleor.product.models import ProductType, ProductVariant
    from saleor.warehouse.models import Allocation, Stock

    product_type, _ = ProductType.objects.get_or_create(
        slug="mutation-test-type",
        defaults={"name": "Mutation Test Type", "has_variants": True},
    )
    from saleor.product.models import Product

    product = Product.objects.create(
        name="Mutation Test Product",
        slug=f"mutation-test-product-{Product.objects.count()}",
        product_type=product_type,
    )
    variant = ProductVariant.objects.create(
        product=product,
        name="S",
        sku=f"mut-sku-{product.pk}",
    )
    stock = Stock.objects.create(
        product_variant=variant, warehouse=warehouse, quantity=50, quantity_allocated=20
    )

    price_list.items.filter(is_valid=True).update(product=product)

    channel, _ = Channel.objects.get_or_create(
        slug="mutation-test-ch",
        defaults={
            "name": "Mutation Test Channel",
            "currency_code": "GBP",
            "default_country": "GB",
        },
    )
    order = Order.objects.create(
        status=OrderStatus.UNCONFIRMED,
        channel=channel,
        currency="GBP",
        origin=OrderOrigin.DRAFT,
        lines_count=1,
    )
    line = OrderLine.objects.create(
        order=order,
        variant=variant,
        product_name="Mutation Test Product",
        quantity=20,
        currency="GBP",
        unit_price_net_amount=Decimal(10),
        unit_price_gross_amount=Decimal(10),
        total_price_net_amount=Decimal(10),
        total_price_gross_amount=Decimal(10),
        is_shipping_required=False,
        is_gift_card=False,
    )
    return Allocation.objects.create(
        order_line=line, stock=stock, quantity_allocated=20
    )


def test_deactivate_returns_error_when_unconfirmed_orders_exist(
    staff_api_client, permission_manage_products, active_price_list, non_owned_warehouse
):
    staff_api_client.user.user_permissions.add(permission_manage_products)
    _make_unconfirmed_allocation_for_pl(active_price_list, non_owned_warehouse)

    variables = {
        "id": graphene.Node.to_global_id("PriceList", active_price_list.pk),
        "force": False,
    }
    with patch("saleor.product.tasks.deactivate_price_list_task.delay") as mock_delay:
        response = staff_api_client.post_graphql(
            PRICE_LIST_DEACTIVATE_FORCE_MUTATION, variables=variables
        )

    content = get_graphql_content(response)
    errors = content["data"]["priceListDeactivate"]["errors"]
    assert len(errors) == 1
    assert errors[0]["field"] == "force"
    assert errors[0]["code"] == "ORDERS_REQUIRE_AMENDMENT"
    mock_delay.assert_not_called()


def test_deactivate_proceeds_with_force_when_unconfirmed_orders_exist(
    staff_api_client, permission_manage_products, active_price_list, non_owned_warehouse
):
    staff_api_client.user.user_permissions.add(permission_manage_products)
    _make_unconfirmed_allocation_for_pl(active_price_list, non_owned_warehouse)

    variables = {
        "id": graphene.Node.to_global_id("PriceList", active_price_list.pk),
        "force": True,
    }
    with patch("saleor.product.tasks.deactivate_price_list_task.delay") as mock_delay:
        response = staff_api_client.post_graphql(
            PRICE_LIST_DEACTIVATE_FORCE_MUTATION, variables=variables
        )

    content = get_graphql_content(response)
    assert content["data"]["priceListDeactivate"]["errors"] == []
    mock_delay.assert_called_once_with(active_price_list.pk)


def test_deactivate_proceeds_without_force_when_no_orders_affected(
    staff_api_client, permission_manage_products, active_price_list
):
    staff_api_client.user.user_permissions.add(permission_manage_products)

    variables = {
        "id": graphene.Node.to_global_id("PriceList", active_price_list.pk),
        "force": False,
    }
    with patch("saleor.product.tasks.deactivate_price_list_task.delay") as mock_delay:
        response = staff_api_client.post_graphql(
            PRICE_LIST_DEACTIVATE_FORCE_MUTATION, variables=variables
        )

    content = get_graphql_content(response)
    assert content["data"]["priceListDeactivate"]["errors"] == []
    mock_delay.assert_called_once_with(active_price_list.pk)


def test_replace_returns_error_when_unconfirmed_orders_exist(
    staff_api_client, permission_manage_products, active_price_list, non_owned_warehouse
):
    from django.utils import timezone

    staff_api_client.user.user_permissions.add(permission_manage_products)
    _make_unconfirmed_allocation_for_pl(active_price_list, non_owned_warehouse)

    new_pl = PriceList.objects.create(
        warehouse=non_owned_warehouse,
        config={},
        processing_completed_at=timezone.now(),
    )

    variables = {
        "oldId": graphene.Node.to_global_id("PriceList", active_price_list.pk),
        "newId": graphene.Node.to_global_id("PriceList", new_pl.pk),
        "force": False,
    }
    with patch("saleor.product.tasks.replace_price_list_task.delay") as mock_delay:
        response = staff_api_client.post_graphql(
            PRICE_LIST_REPLACE_FORCE_MUTATION, variables=variables
        )

    content = get_graphql_content(response)
    errors = content["data"]["priceListReplace"]["errors"]
    assert len(errors) == 1
    assert errors[0]["field"] == "force"
    assert errors[0]["code"] == "ORDERS_REQUIRE_AMENDMENT"
    mock_delay.assert_not_called()


def test_replace_proceeds_with_force(
    staff_api_client, permission_manage_products, active_price_list, non_owned_warehouse
):
    from django.utils import timezone

    staff_api_client.user.user_permissions.add(permission_manage_products)
    _make_unconfirmed_allocation_for_pl(active_price_list, non_owned_warehouse)

    new_pl = PriceList.objects.create(
        warehouse=non_owned_warehouse,
        config={},
        processing_completed_at=timezone.now(),
    )

    variables = {
        "oldId": graphene.Node.to_global_id("PriceList", active_price_list.pk),
        "newId": graphene.Node.to_global_id("PriceList", new_pl.pk),
        "force": True,
    }
    with patch("saleor.product.tasks.replace_price_list_task.delay") as mock_delay:
        response = staff_api_client.post_graphql(
            PRICE_LIST_REPLACE_FORCE_MUTATION, variables=variables
        )

    content = get_graphql_content(response)
    assert content["data"]["priceListReplace"]["errors"] == []
    mock_delay.assert_called_once_with(active_price_list.pk, new_pl.pk)
