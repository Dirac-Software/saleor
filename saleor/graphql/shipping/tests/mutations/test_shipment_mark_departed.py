import graphene
import pytest
from django.utils import timezone

from .....shipping import ShipmentType
from .....shipping.models import Shipment
from ....tests.utils import assert_no_permission, get_graphql_content

SHIPMENT_MARK_DEPARTED_MUTATION = """
mutation ShipmentMarkDeparted($id: ID!, $input: ShipmentMarkDepartedInput) {
    shipmentMarkDeparted(id: $id, input: $input) {
        shipment {
            id
            departedAt
            shipmentType
        }
        errors {
            field
            message
        }
    }
}
"""


def test_mark_outbound_shipment_as_departed(
    staff_api_client, permission_manage_orders, shipment_factory, fulfillment
):
    """Test marking an outbound shipment as departed."""
    # given
    shipment = shipment_factory(
        shipment_type=ShipmentType.OUTBOUND,
        departed_at=None,
    )
    shipment.fulfillments.add(fulfillment)

    shipment_id = graphene.Node.to_global_id("Shipment", shipment.id)
    variables = {"id": shipment_id}

    # when
    response = staff_api_client.post_graphql(
        SHIPMENT_MARK_DEPARTED_MUTATION,
        variables,
        permissions=[permission_manage_orders],
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["shipmentMarkDeparted"]
    assert not data["errors"]
    assert data["shipment"]["id"] == shipment_id
    assert data["shipment"]["departedAt"] is not None
    assert data["shipment"]["shipmentType"] == ShipmentType.OUTBOUND.upper()

    shipment.refresh_from_db()
    assert shipment.departed_at is not None


def test_mark_departed_with_custom_timestamp(
    staff_api_client, permission_manage_orders, shipment_factory, fulfillment
):
    """Test marking shipment as departed with a custom timestamp."""
    # given
    shipment = shipment_factory(
        shipment_type=ShipmentType.OUTBOUND,
        departed_at=None,
    )
    shipment.fulfillments.add(fulfillment)

    shipment_id = graphene.Node.to_global_id("Shipment", shipment.id)
    custom_time = timezone.now() - timezone.timedelta(hours=2)
    variables = {
        "id": shipment_id,
        "input": {"departedAt": custom_time.isoformat()},
    }

    # when
    response = staff_api_client.post_graphql(
        SHIPMENT_MARK_DEPARTED_MUTATION,
        variables,
        permissions=[permission_manage_orders],
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["shipmentMarkDeparted"]
    assert not data["errors"]

    shipment.refresh_from_db()
    assert shipment.departed_at is not None
    # Check it's roughly the same time (within 1 second to account for precision)
    assert abs((shipment.departed_at - custom_time).total_seconds()) < 1


def test_cannot_mark_inbound_shipment_as_departed(
    staff_api_client, permission_manage_orders, shipment_factory
):
    """Test that inbound shipments cannot be marked as departed."""
    # given
    shipment = shipment_factory(
        shipment_type=ShipmentType.INBOUND,
        departed_at=None,
    )

    shipment_id = graphene.Node.to_global_id("Shipment", shipment.id)
    variables = {"id": shipment_id}

    # when
    response = staff_api_client.post_graphql(
        SHIPMENT_MARK_DEPARTED_MUTATION,
        variables,
        permissions=[permission_manage_orders],
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["shipmentMarkDeparted"]
    assert len(data["errors"]) == 1
    assert "inbound" in data["errors"][0]["message"].lower()
    assert "outbound" in data["errors"][0]["message"].lower()

    shipment.refresh_from_db()
    assert shipment.departed_at is None


def test_cannot_mark_already_departed_shipment(
    staff_api_client, permission_manage_orders, shipment_factory, fulfillment
):
    """Test that already departed shipments cannot be marked again."""
    # given
    departed_time = timezone.now() - timezone.timedelta(days=1)
    shipment = shipment_factory(
        shipment_type=ShipmentType.OUTBOUND,
        departed_at=departed_time,
    )
    shipment.fulfillments.add(fulfillment)

    shipment_id = graphene.Node.to_global_id("Shipment", shipment.id)
    variables = {"id": shipment_id}

    # when
    response = staff_api_client.post_graphql(
        SHIPMENT_MARK_DEPARTED_MUTATION,
        variables,
        permissions=[permission_manage_orders],
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["shipmentMarkDeparted"]
    assert len(data["errors"]) == 1
    assert "already marked as departed" in data["errors"][0]["message"].lower()

    shipment.refresh_from_db()
    assert shipment.departed_at == departed_time  # Unchanged


def test_mark_departed_requires_permission(
    staff_api_client, shipment_factory, fulfillment
):
    """Test that marking shipment as departed requires permission."""
    # given
    shipment = shipment_factory(
        shipment_type=ShipmentType.OUTBOUND,
        departed_at=None,
    )
    shipment.fulfillments.add(fulfillment)

    shipment_id = graphene.Node.to_global_id("Shipment", shipment.id)
    variables = {"id": shipment_id}

    # when
    response = staff_api_client.post_graphql(
        SHIPMENT_MARK_DEPARTED_MUTATION,
        variables,
    )

    # then
    assert_no_permission(response)


@pytest.fixture
def shipment_factory(owned_warehouse, nonowned_warehouse):
    """Factory for creating test shipments."""
    from decimal import Decimal

    from django.utils import timezone

    from .....shipping import IncoTerm

    def create_shipment(**kwargs):
        defaults = {
            "source": owned_warehouse.address,
            "destination": nonowned_warehouse.address,
            "shipment_type": ShipmentType.OUTBOUND,
            "carrier": "TEST-CARRIER",
            "tracking_url": "TEST-123",
            "shipping_cost_amount": Decimal("100.00"),
            "currency": "USD",
            "inco_term": IncoTerm.DDP,
            "departed_at": timezone.now(),
        }
        defaults.update(kwargs)
        return Shipment.objects.create(**defaults)

    return create_shipment
