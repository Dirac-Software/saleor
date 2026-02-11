import graphene

from .....checkout.models import Checkout
from ....tests.utils import get_graphql_content


def test_checkout_create(api_client, stock, graphql_address_data_with_vat, channel_USD):
    """Create checkout object using GraphQL API."""
    query = """
        mutation createCheckout($checkoutInput: CheckoutCreateInput!) {
        checkoutCreate(input: $checkoutInput) {
            created
            checkout {
                id
                token
                email
                quantity
                shippingAddress {
                    metadata {
                        key
                        value
                    }
                }
                billingAddress {
                    metadata {
                        key
                        value
                    }
                }
            lines {
                quantity
            }
            }
            errors {
                field
                message
                code
                variants
                addressType
            }
        }
    }
    """
    # given
    variant = stock.product_variant
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.id)
    test_email = "test@example.com"
    address = graphql_address_data_with_vat
    variables = {
        "checkoutInput": {
            "channel": channel_USD.slug,
            "lines": [{"quantity": 1, "variantId": variant_id}],
            "email": test_email,
            "shippingAddress": address,
            "billingAddress": address,
        }
    }
    assert not Checkout.objects.exists()

    # when
    response = api_client.post_graphql(query, variables)
    content = get_graphql_content(response)["data"]["checkoutCreate"]

    # then
    assert (
        content["checkout"]["shippingAddress"]["metadata"]
        == graphql_address_data_with_vat["metadata"]
    )
    assert (
        content["checkout"]["billingAddress"]["metadata"]
        == graphql_address_data_with_vat["metadata"]
    )

    checkout = Checkout.objects.get(email=test_email)

    stored_metadata_with_vat = {"public": "public_value", "vat_number": "PL1234567890"}
    assert checkout.billing_address.metadata == stored_metadata_with_vat
    assert checkout.shipping_address.metadata == stored_metadata_with_vat

    assert content["created"] is True
