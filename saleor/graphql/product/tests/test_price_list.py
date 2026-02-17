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
def processed_price_list(db, non_owned_warehouse):
    from django.utils import timezone

    pl = PriceList.objects.create(
        warehouse=non_owned_warehouse,
        config={},
        processing_completed_at=timezone.now(),
    )
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
            status
            itemCount
            validItemCount
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
    staff_api_client, permission_manage_products, processed_price_list
):
    staff_api_client.user.user_permissions.add(permission_manage_products)

    variables = {
        "id": graphene.Node.to_global_id("PriceList", processed_price_list.pk),
    }
    response = staff_api_client.post_graphql(PRICE_LIST_QUERY, variables=variables)
    content = get_graphql_content(response)
    data = content["data"]["priceList"]

    assert data is not None
    assert data["status"] == "INACTIVE"
    assert data["itemCount"] == 1
    assert data["validItemCount"] == 1
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
