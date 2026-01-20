from datetime import UTC, datetime

from ....utils.products_data import handle_channel_data


def test_handle_channel_data_converts_timezone_aware_datetime():
    """Test that timezone-aware datetimes are converted to timezone-naive for Excel."""
    # given
    pk = 1
    timezone_aware_dt = datetime(2026, 1, 20, 12, 0, 0, tzinfo=UTC)
    data = {
        "channel__slug": "usd-channel",
        "published_at": timezone_aware_dt,
        "available_for_purchase_at": timezone_aware_dt,
        "other_field": "test_value",
    }
    result_data = {pk: {}}
    fields = {
        "slug": "channel__slug",
        "published": "published_at",
        "available for purchase": "available_for_purchase_at",
        "other": "other_field",
    }

    # when
    result_data, _ = handle_channel_data(pk, data, result_data, fields)

    # then
    # Check that datetime values are timezone-naive
    assert result_data[pk]["usd-channel (channel published)"] == datetime(  # noqa: DTZ001
        2026, 1, 20, 12, 0, 0
    )
    assert result_data[pk]["usd-channel (channel published)"].tzinfo is None

    assert result_data[pk]["usd-channel (channel available for purchase)"] == datetime(  # noqa: DTZ001
        2026, 1, 20, 12, 0, 0
    )
    assert (
        result_data[pk]["usd-channel (channel available for purchase)"].tzinfo is None
    )

    # Check that non-datetime values are unchanged
    assert result_data[pk]["usd-channel (channel other)"] == "test_value"


def test_handle_channel_data_preserves_timezone_naive_datetime():
    """Test that timezone-naive datetimes are preserved as-is."""
    # given
    pk = 1
    timezone_naive_dt = datetime(2026, 1, 20, 12, 0, 0)  # noqa: DTZ001
    data = {
        "channel__slug": "usd-channel",
        "published_at": timezone_naive_dt,
    }
    result_data = {pk: {}}
    fields = {
        "slug": "channel__slug",
        "published": "published_at",
    }

    # when
    result_data, _ = handle_channel_data(pk, data, result_data, fields)

    # then
    assert result_data[pk]["usd-channel (channel published)"] == timezone_naive_dt
    assert result_data[pk]["usd-channel (channel published)"].tzinfo is None


def test_handle_channel_data_handles_none_datetime():
    """Test that None datetime values are handled correctly."""
    # given
    pk = 1
    data = {
        "channel__slug": "usd-channel",
        "published_at": None,
    }
    result_data = {pk: {}}
    fields = {
        "slug": "channel__slug",
        "published": "published_at",
    }

    # when
    result_data, _ = handle_channel_data(pk, data, result_data, fields)

    # then
    assert result_data[pk]["usd-channel (channel published)"] is None


def test_handle_channel_data_skips_channel_pk_field():
    """Test that channel_pk field is skipped."""
    # given
    pk = 1
    data = {
        "channel__slug": "usd-channel",
        "channel_id": 123,
        "published_at": datetime(2026, 1, 20, 12, 0, 0),  # noqa: DTZ001
    }
    result_data = {pk: {}}
    fields = {
        "slug": "channel__slug",
        "channel_pk": "channel_id",
        "published": "published_at",
    }

    # when
    result_data, _ = handle_channel_data(pk, data, result_data, fields)

    # then
    # channel_pk should not be in the result
    assert "usd-channel (channel channel pk)" not in result_data[pk]
    # but other fields should be
    assert "usd-channel (channel published)" in result_data[pk]
