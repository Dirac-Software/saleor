from unittest.mock import patch

import pytest

from .....graphql.tests.utils import get_graphql_content
from .....order.error_codes import OrderErrorCode

AVAILABLE_XERO_TAX_CODES_QUERY = """
    query AvailableXeroTaxCodes($channelSlug: String!) {
        availableXeroTaxCodes(channelSlug: $channelSlug) {
            taxCodes {
                code
                name
                rate
            }
            errors {
                code
                message
            }
        }
    }
"""


@pytest.mark.django_db
@patch("saleor.graphql.order.queries.xero_tax_codes.get_plugin_manager_promise")
def test_available_xero_tax_codes_returns_codes(
    mock_manager_promise, staff_api_client, permission_manage_orders
):
    # given
    staff_api_client.user.user_permissions.add(permission_manage_orders)
    mock_manager = mock_manager_promise.return_value.get.return_value
    mock_manager.xero_list_tax_codes.return_value = [
        {"code": "OUTPUT2", "name": "20% (VAT on Income)", "rate": 0.2},
        {"code": "ZERORATEDINPUT", "name": "Zero Rated", "rate": 0.0},
    ]

    # when
    response = staff_api_client.post_graphql(
        AVAILABLE_XERO_TAX_CODES_QUERY, {"channelSlug": "default-channel"}
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["availableXeroTaxCodes"]
    assert not data["errors"]
    assert len(data["taxCodes"]) == 2
    assert data["taxCodes"][0]["code"] == "OUTPUT2"
    assert data["taxCodes"][0]["name"] == "20% (VAT on Income)"
    assert data["taxCodes"][0]["rate"] == pytest.approx(0.2)
    assert data["taxCodes"][1]["code"] == "ZERORATEDINPUT"
    assert data["taxCodes"][1]["rate"] == pytest.approx(0.0)
    mock_manager.xero_list_tax_codes.assert_called_once_with(domain="default-channel")


@pytest.mark.django_db
@patch("saleor.graphql.order.queries.xero_tax_codes.get_plugin_manager_promise")
def test_available_xero_tax_codes_plugin_exception_returns_error(
    mock_manager_promise, staff_api_client, permission_manage_orders
):
    # given
    staff_api_client.user.user_permissions.add(permission_manage_orders)
    mock_manager = mock_manager_promise.return_value.get.return_value
    mock_manager.xero_list_tax_codes.side_effect = Exception("Xero unreachable")

    # when
    response = staff_api_client.post_graphql(
        AVAILABLE_XERO_TAX_CODES_QUERY, {"channelSlug": "default-channel"}
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["availableXeroTaxCodes"]
    assert len(data["errors"]) == 1
    assert data["errors"][0]["code"] == OrderErrorCode.INVALID.name
