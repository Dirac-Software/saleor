from collections.abc import Callable
from typing import TYPE_CHECKING

from ...account.models import StaffNotificationRecipient
from ..email_common import get_email_subject, get_email_template_or_default
from . import constants
from .tasks import (
    send_contact_form_email_task,
    send_email_with_link_to_download_file_task,
    send_export_failed_email_task,
    send_set_staff_password_email_task,
    send_staff_order_confirmation_email_task,
    send_staff_password_reset_email_task,
)

if TYPE_CHECKING:
    from .plugin import AdminEmailPlugin


def send_set_staff_password_email(
    payload_func: Callable[[], dict], config: dict, plugin: "AdminEmailPlugin"
):
    template = get_email_template_or_default(
        plugin,
        constants.SET_STAFF_PASSWORD_TEMPLATE_FIELD,
        constants.SET_STAFF_PASSWORD_DEFAULT_TEMPLATE,
        constants.DEFAULT_EMAIL_TEMPLATES_PATH,
    )
    if not template:
        # Empty template means that we don't want to trigger a given event.
        return
    payload = payload_func()
    recipient_email = payload["recipient_email"]
    subject = get_email_subject(
        plugin.configuration,
        constants.SET_STAFF_PASSWORD_SUBJECT_FIELD,
        constants.SET_STAFF_PASSWORD_DEFAULT_SUBJECT,
    )
    send_set_staff_password_email_task.delay(
        recipient_email, payload, config, subject, template
    )


def send_csv_export_success(
    payload_func: Callable[[], dict], config: dict, plugin: "AdminEmailPlugin"
):
    template = get_email_template_or_default(
        plugin,
        constants.CSV_EXPORT_SUCCESS_TEMPLATE_FIELD,
        constants.CSV_EXPORT_SUCCESS_DEFAULT_TEMPLATE,
        constants.DEFAULT_EMAIL_TEMPLATES_PATH,
    )
    if not template:
        # Empty template means that we don't want to trigger a given event.
        return
    payload = payload_func()
    recipient_email = payload.get("recipient_email")
    if not recipient_email:
        return
    subject = get_email_subject(
        plugin.configuration,
        constants.CSV_EXPORT_SUCCESS_SUBJECT_FIELD,
        constants.CSV_EXPORT_SUCCESS_DEFAULT_SUBJECT,
    )
    send_email_with_link_to_download_file_task.delay(
        recipient_email, payload, config, subject, template
    )


def send_staff_order_confirmation(
    payload_func: Callable[[], dict], config: dict, plugin: "AdminEmailPlugin"
):
    template = get_email_template_or_default(
        plugin,
        constants.STAFF_ORDER_CONFIRMATION_TEMPLATE_FIELD,
        constants.STAFF_ORDER_CONFIRMATION_DEFAULT_TEMPLATE,
        constants.DEFAULT_EMAIL_TEMPLATES_PATH,
    )
    if not template:
        # Empty template means that we don't want to trigger a given event.
        return
    payload = payload_func()
    recipient_list = payload.get("recipient_list")
    subject = get_email_subject(
        plugin.configuration,
        constants.STAFF_ORDER_CONFIRMATION_SUBJECT_FIELD,
        constants.STAFF_ORDER_CONFIRMATION_DEFAULT_SUBJECT,
    )
    send_staff_order_confirmation_email_task.delay(
        recipient_list, payload, config, subject, template
    )


def send_csv_export_failed(
    payload_func: Callable[[], dict], config: dict, plugin: "AdminEmailPlugin"
):
    template = get_email_template_or_default(
        plugin,
        constants.CSV_EXPORT_FAILED_TEMPLATE_FIELD,
        constants.CSV_EXPORT_FAILED_TEMPLATE_DEFAULT_TEMPLATE,
        constants.DEFAULT_EMAIL_TEMPLATES_PATH,
    )
    if not template:
        # Empty template means that we don't want to trigger a given event.
        return
    payload = payload_func()
    recipient_email = payload.get("recipient_email")
    if not recipient_email:
        return
    subject = get_email_subject(
        plugin.configuration,
        constants.CSV_EXPORT_FAILED_SUBJECT_FIELD,
        constants.CSV_EXPORT_FAILED_DEFAULT_SUBJECT,
    )
    send_export_failed_email_task.delay(
        recipient_email, payload, config, subject, template
    )


def send_staff_reset_password(
    payload_func: Callable[[], dict], config: dict, plugin: "AdminEmailPlugin"
):
    template = get_email_template_or_default(
        plugin,
        constants.STAFF_PASSWORD_RESET_TEMPLATE_FIELD,
        constants.STAFF_PASSWORD_RESET_DEFAULT_TEMPLATE,
        constants.DEFAULT_EMAIL_TEMPLATES_PATH,
    )
    if not template:
        # Empty template means that we don't want to trigger a given event.
        return
    payload = payload_func()
    recipient_email = payload.get("recipient_email")
    if not recipient_email:
        return
    subject = get_email_subject(
        plugin.configuration,
        constants.STAFF_PASSWORD_RESET_SUBJECT_FIELD,
        constants.STAFF_PASSWORD_RESET_DEFAULT_SUBJECT,
    )
    send_staff_password_reset_email_task.delay(
        recipient_email, payload, config, subject, template
    )


def send_contact_form_submission(
    payload_func: Callable[[], dict], config: dict, plugin: "AdminEmailPlugin"
):
    template = get_email_template_or_default(
        plugin,
        constants.CONTACT_FORM_SUBMISSION_TEMPLATE_FIELD,
        constants.CONTACT_FORM_SUBMISSION_DEFAULT_TEMPLATE,
        constants.DEFAULT_EMAIL_TEMPLATES_PATH,
    )
    if not template:
        # Empty template means that we don't want to trigger a given event.
        return

    # Get active staff notification recipients
    staff_notifications = StaffNotificationRecipient.objects.filter(
        active=True, user__is_active=True, user__is_staff=True
    )
    recipient_list = [notification.get_email() for notification in staff_notifications]

    if not recipient_list:
        # No recipients configured, skip sending
        return

    payload = payload_func()
    payload["recipient_list"] = recipient_list

    subject = get_email_subject(
        plugin.configuration,
        constants.CONTACT_FORM_SUBMISSION_SUBJECT_FIELD,
        constants.CONTACT_FORM_SUBMISSION_DEFAULT_SUBJECT,
    )
    send_contact_form_email_task.delay(
        recipient_list, payload, config, subject, template
    )
