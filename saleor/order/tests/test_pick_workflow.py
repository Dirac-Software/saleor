import pytest

from .. import PickStatus
from ..actions import (
    auto_create_pick_for_fulfillment,
    complete_pick,
    start_pick,
    update_pick_item,
)


def test_auto_creates_pick_when_fulfillment_waiting_for_approval(
    fulfillment, staff_user
):
    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)

    assert pick.fulfillment == fulfillment
    assert pick.status == PickStatus.NOT_STARTED
    assert pick.created_by == staff_user
    assert pick.created_at is not None
    assert pick.started_at is None
    assert pick.completed_at is None

    fulfillment_lines_count = fulfillment.lines.count()
    pick_items = list(pick.items.all())
    assert len(pick_items) == fulfillment_lines_count
    for pick_item in pick_items:
        assert pick_item.quantity_picked == 0


def test_auto_create_is_idempotent(fulfillment, staff_user):
    pick1 = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)
    pick2 = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)

    assert pick1.id == pick2.id


def test_start_pick_changes_status(fulfillment, staff_user):
    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)

    pick = start_pick(pick, user=staff_user)

    assert pick.status == PickStatus.IN_PROGRESS
    assert pick.started_at is not None
    assert pick.started_by == staff_user


def test_start_pick_validates_status(fulfillment, staff_user):
    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)
    start_pick(pick, user=staff_user)

    with pytest.raises(ValueError, match="cannot be started"):
        start_pick(pick, user=staff_user)


def test_update_pick_item_updates_quantity(fulfillment, staff_user):
    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)
    start_pick(pick, user=staff_user)
    pick_item = pick.items.first()
    quantity_to_pick = pick_item.quantity_to_pick

    pick_item = update_pick_item(
        pick_item,
        quantity_picked=quantity_to_pick - 1,
        user=staff_user,
        notes="Partially picked",
    )

    assert pick_item.quantity_picked == quantity_to_pick - 1
    assert pick_item.picked_by == staff_user
    assert pick_item.notes == "Partially picked"
    assert not pick_item.is_fully_picked
    assert pick_item.picked_at is None


def test_update_pick_item_marks_fully_picked(fulfillment, staff_user):
    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)
    start_pick(pick, user=staff_user)
    pick_item = pick.items.first()
    quantity_to_pick = pick_item.quantity_to_pick

    pick_item = update_pick_item(pick_item, quantity_picked=quantity_to_pick, user=staff_user)

    assert pick_item.quantity_picked == quantity_to_pick
    assert pick_item.is_fully_picked
    assert pick_item.picked_at is not None


def test_update_pick_item_validates_quantity_exceeds(fulfillment, staff_user):
    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)
    start_pick(pick, user=staff_user)
    pick_item = pick.items.first()
    quantity_to_pick = pick_item.quantity_to_pick

    with pytest.raises(ValueError, match="cannot exceed"):
        update_pick_item(pick_item, quantity_picked=quantity_to_pick + 5, user=staff_user)


def test_update_pick_item_validates_pick_in_progress(fulfillment, staff_user):
    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)
    pick_item = pick.items.first()

    with pytest.raises(ValueError, match="not in progress"):
        update_pick_item(pick_item, quantity_picked=1, user=staff_user)


def test_complete_pick_succeeds_when_all_items_picked(fulfillment, staff_user):
    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)
    start_pick(pick, user=staff_user)

    for pick_item in pick.items.all():
        update_pick_item(pick_item, quantity_picked=pick_item.quantity_to_pick, user=staff_user)

    pick = complete_pick(pick, user=staff_user)

    assert pick.status == PickStatus.COMPLETED
    assert pick.completed_at is not None
    assert pick.completed_by == staff_user


def test_complete_pick_validates_all_items_picked(fulfillment, staff_user):
    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)
    start_pick(pick, user=staff_user)
    pick_item = pick.items.first()
    update_pick_item(pick_item, quantity_picked=1, user=staff_user)

    with pytest.raises(ValueError, match="not fully picked"):
        complete_pick(pick, user=staff_user)


def test_complete_pick_validates_status(fulfillment, staff_user):
    pick = auto_create_pick_for_fulfillment(fulfillment, user=staff_user)

    with pytest.raises(ValueError, match="cannot be completed"):
        complete_pick(pick, user=staff_user)
