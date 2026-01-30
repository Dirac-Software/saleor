from unittest import mock

from django.core.files import File
from freezegun import freeze_time

from ...core.notification.utils import get_site_context
from ...core.notify import AdminNotifyEvent
from .. import notifications
from ..notifications import get_default_export_payload


@freeze_time("2018-05-31 12:00:01")
@mock.patch("saleor.plugins.manager.PluginsManager.gift_card_export_completed")
@mock.patch("saleor.plugins.manager.PluginsManager.product_export_completed")
@mock.patch("saleor.plugins.manager.PluginsManager.notify")
def test_send_export_download_link_notification(
    mocked_notify,
    mocked_product_export_completed,
    mocked_gift_card_export_completed,
    site_settings,
    user_export_file,
    tmpdir,
    media_root,
):
    # given
    file_mock = mock.MagicMock(spec=File)
    file_mock.name = "temp_file.csv"
    data_type = "products"

    user_export_file.content_file = file_mock
    user_export_file.save()

    # when
    notifications.send_export_download_link_notification(user_export_file, data_type)

    # then
    assert mocked_notify.call_count == 1
    call_args = mocked_notify.call_args_list[0]
    called_args = call_args.args
    called_kwargs = call_args.kwargs
    assert called_args[0] == AdminNotifyEvent.CSV_EXPORT_SUCCESS
    assert len(called_kwargs) == 1

    actual_payload = called_kwargs["payload_func"]()

    # Verify payload structure and values (except CSV link which may be signed)
    assert actual_payload["export"] == get_default_export_payload(user_export_file)
    assert actual_payload["recipient_email"] == user_export_file.user.email
    assert actual_payload["data_type"] == data_type
    # Check that csv_link contains export_files path
    assert "export_files" in actual_payload["csv_link"]
    # Verify site context keys are present
    for key in get_site_context().keys():
        assert key in actual_payload

    mocked_gift_card_export_completed.assert_not_called()
    mocked_product_export_completed.assert_called_with(user_export_file)


@freeze_time("2018-05-31 12:00:01")
@mock.patch("saleor.plugins.manager.PluginsManager.gift_card_export_completed")
@mock.patch("saleor.plugins.manager.PluginsManager.product_export_completed")
@mock.patch("saleor.plugins.manager.PluginsManager.notify")
def test_send_export_failed_info(
    mocked_notify,
    mocked_product_export_completed,
    mocked_gift_card_export_completed,
    site_settings,
    user_export_file,
    tmpdir,
    media_root,
):
    # given
    file_mock = mock.MagicMock(spec=File)
    file_mock.name = "temp_file.csv"
    data_type = "gift cards"

    user_export_file.content_file = file_mock
    user_export_file.save()

    # when
    notifications.send_export_failed_info(user_export_file, data_type)

    # then
    expected_payload = {
        "export": get_default_export_payload(user_export_file),
        "recipient_email": user_export_file.user.email,
        "data_type": data_type,
        **get_site_context(),
    }

    assert mocked_notify.call_count == 1
    call_args = mocked_notify.call_args_list[0]
    called_args = call_args.args
    called_kwargs = call_args.kwargs
    assert called_args[0] == AdminNotifyEvent.CSV_EXPORT_FAILED
    assert len(called_kwargs) == 1
    assert called_kwargs["payload_func"]() == expected_payload

    mocked_gift_card_export_completed.assert_called_once_with(user_export_file)
    mocked_product_export_completed.assert_not_called()
