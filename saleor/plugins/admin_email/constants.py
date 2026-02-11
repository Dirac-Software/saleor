import os

from django.conf import settings

DEFAULT_EMAIL_TEMPLATES_PATH = os.path.join(
    settings.PROJECT_ROOT, "saleor/plugins/admin_email/default_email_templates"
)

STAFF_ORDER_CONFIRMATION_TEMPLATE_FIELD = "staff_order_confirmation_template"
SET_STAFF_PASSWORD_TEMPLATE_FIELD = "set_staff_password_template"
CSV_EXPORT_SUCCESS_TEMPLATE_FIELD = "csv_export_success_template"
CSV_EXPORT_FAILED_TEMPLATE_FIELD = "csv_export_failed_template"
STAFF_PASSWORD_RESET_TEMPLATE_FIELD = "staff_password_reset_template"
CONTACT_FORM_SUBMISSION_TEMPLATE_FIELD = "contact_form_submission_template"
PENDING_ADJUSTMENTS_TEMPLATE_FIELD = "pending_adjustments_template"


TEMPLATE_FIELDS = [
    STAFF_ORDER_CONFIRMATION_TEMPLATE_FIELD,
    SET_STAFF_PASSWORD_TEMPLATE_FIELD,
    CSV_EXPORT_SUCCESS_TEMPLATE_FIELD,
    CSV_EXPORT_FAILED_TEMPLATE_FIELD,
    STAFF_PASSWORD_RESET_TEMPLATE_FIELD,
    CONTACT_FORM_SUBMISSION_TEMPLATE_FIELD,
    PENDING_ADJUSTMENTS_TEMPLATE_FIELD,
]

SET_STAFF_PASSWORD_DEFAULT_TEMPLATE = "set_password.html"
CSV_EXPORT_SUCCESS_DEFAULT_TEMPLATE = "export_success.html"
CSV_EXPORT_FAILED_TEMPLATE_DEFAULT_TEMPLATE = "export_failed.html"
STAFF_ORDER_CONFIRMATION_DEFAULT_TEMPLATE = "staff_confirm_order.html"
STAFF_PASSWORD_RESET_DEFAULT_TEMPLATE = "password_reset.html"
CONTACT_FORM_SUBMISSION_DEFAULT_TEMPLATE = "contact_form.html"
PENDING_ADJUSTMENTS_DEFAULT_TEMPLATE = "pending_adjustments.html"

STAFF_ORDER_CONFIRMATION_SUBJECT_FIELD = "staff_order_confirmation_subject"
SET_STAFF_PASSWORD_SUBJECT_FIELD = "set_staff_password_subject"
CSV_EXPORT_SUCCESS_SUBJECT_FIELD = "csv_export_success_subject"
CSV_EXPORT_FAILED_SUBJECT_FIELD = "csv_export_failed_subject"
STAFF_PASSWORD_RESET_SUBJECT_FIELD = "staff_password_reset_subject"
CONTACT_FORM_SUBMISSION_SUBJECT_FIELD = "contact_form_submission_subject"
PENDING_ADJUSTMENTS_SUBJECT_FIELD = "pending_adjustments_subject"


STAFF_ORDER_CONFIRMATION_DEFAULT_SUBJECT = "Order {{ order.number }} details"
SET_STAFF_PASSWORD_DEFAULT_SUBJECT = "You're invited to join Saleor"
CSV_EXPORT_SUCCESS_DEFAULT_SUBJECT = "Your exported {{ data_type }} data is ready"
CSV_EXPORT_FAILED_DEFAULT_SUBJECT = "Exporting {{ data_type }} data failed"
STAFF_PASSWORD_RESET_DEFAULT_SUBJECT = "Reset your Saleor password"
CONTACT_FORM_SUBMISSION_DEFAULT_SUBJECT = (
    "New Contact Form Submission from {{ business_name }}"
)
PENDING_ADJUSTMENTS_DEFAULT_SUBJECT = "ACTION REQUIRED: {{ count }} pending inventory adjustment(s) for Receipt #{{ receipt_id }}"


PLUGIN_ID = "mirumee.notifications.admin_email"
