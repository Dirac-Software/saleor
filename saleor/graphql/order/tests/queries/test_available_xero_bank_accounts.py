from unittest.mock import patch

import pytest

from .....graphql.tests.utils import get_graphql_content
from .....order.error_codes import OrderErrorCode

AVAILABLE_XERO_BANK_ACCOUNTS_QUERY = """
    query AvailableXeroBankAccounts($channelSlug: String!) {
        availableXeroBankAccounts(channelSlug: $channelSlug) {
            bankAccounts {
                code
                name
                sortCode
                accountNumber
            }
            errors {
                code
                message
            }
        }
    }
"""


@pytest.mark.django_db
@patch("saleor.graphql.order.queries.xero_bank_accounts.get_plugin_manager_promise")
def test_available_xero_bank_accounts_returns_accounts(
    mock_manager_promise, staff_api_client, permission_manage_orders
):
    # given
    staff_api_client.user.user_permissions.add(permission_manage_orders)
    mock_manager = mock_manager_promise.return_value.get.return_value
    mock_manager.xero_list_bank_accounts.return_value = [
        {
            "code": "090",
            "name": "Business Account",
            "sort_code": "123456",
            "account_number": "12345678",
        },
        {
            "code": "091",
            "name": "Savings Account",
            "sort_code": "654321",
            "account_number": "87654321",
        },
    ]

    # when
    response = staff_api_client.post_graphql(
        AVAILABLE_XERO_BANK_ACCOUNTS_QUERY, {"channelSlug": "default-channel"}
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["availableXeroBankAccounts"]
    assert not data["errors"]
    assert len(data["bankAccounts"]) == 2
    assert data["bankAccounts"][0]["code"] == "090"
    assert data["bankAccounts"][0]["name"] == "Business Account"
    assert data["bankAccounts"][0]["sortCode"] == "123456"
    assert data["bankAccounts"][0]["accountNumber"] == "12345678"
    assert data["bankAccounts"][1]["code"] == "091"
    mock_manager.xero_list_bank_accounts.assert_called_once_with(
        domain="default-channel"
    )


@pytest.mark.django_db
@patch("saleor.graphql.order.queries.xero_bank_accounts.get_plugin_manager_promise")
def test_available_xero_bank_accounts_plugin_exception_returns_error(
    mock_manager_promise, staff_api_client, permission_manage_orders
):
    # given
    staff_api_client.user.user_permissions.add(permission_manage_orders)
    mock_manager = mock_manager_promise.return_value.get.return_value
    mock_manager.xero_list_bank_accounts.side_effect = Exception("Xero unreachable")

    # when
    response = staff_api_client.post_graphql(
        AVAILABLE_XERO_BANK_ACCOUNTS_QUERY, {"channelSlug": "default-channel"}
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["availableXeroBankAccounts"]
    assert len(data["errors"]) == 1
    assert data["errors"][0]["code"] == OrderErrorCode.INVALID.name
