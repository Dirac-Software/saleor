import graphene
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email

from ....core.notify import AdminNotifyEvent, NotifyHandler
from ....core.utils.rate_limit import get_client_ip, get_contact_form_rate_limiter
from ....graphql.core import ResolveInfo
from ....graphql.core.doc_category import DOC_CATEGORY_SHOP
from ....graphql.core.mutations import BaseMutation
from ....graphql.plugins.dataloaders import get_plugin_manager_promise
from ....graphql.site.dataloaders import get_site_promise


class ContactFormInput(graphene.InputObjectType):
    business_name = graphene.String(description="Business name")
    contact_name = graphene.String(description="Contact person name")
    email = graphene.String(description="Contact email address")
    phone = graphene.String(description="Phone number")
    order_number = graphene.String(description="Order number (if applicable)")
    enquiry = graphene.String(description="Enquiry message")


class ContactFormError(graphene.ObjectType):
    field = graphene.String(description="Name of the field that caused the error")
    message = graphene.String(required=True, description="Error message for the field")


class ContactFormSubmit(BaseMutation):
    success = graphene.Boolean(
        required=True, description="Whether the submission was successful"
    )

    class Arguments:
        input = ContactFormInput(
            required=True, description="Fields for the contact form"
        )

    class Meta:
        description = "Submit a contact form enquiry. Sends an email to staff members."
        doc_category = DOC_CATEGORY_SHOP
        error_type_class = ContactFormError
        error_type_field = "errors"

    @classmethod
    def validate_input(cls, input_data):
        """Validate the contact form input."""
        errors = []

        # Maximum length limits to prevent abuse
        MAX_LENGTHS = {
            "business_name": 200,
            "contact_name": 200,
            "email": 254,  # RFC 5321 max email length
            "phone": 50,
            "order_number": 100,
            "enquiry": 5000,
        }

        # Validate email format
        try:
            validate_email(input_data.get("email", ""))
        except DjangoValidationError:
            errors.append(
                ContactFormError(field="email", message="Invalid email address")
            )

        # Validate required fields
        required_fields = ["business_name", "contact_name", "email", "enquiry"]
        for field in required_fields:
            if not input_data.get(field):
                errors.append(
                    ContactFormError(
                        field=field,
                        message=f"{field.replace('_', ' ').title()} is required",
                    )
                )

        # Validate minimum enquiry length
        enquiry = input_data.get("enquiry", "")
        if len(enquiry) < 10:
            errors.append(
                ContactFormError(
                    field="enquiry", message="Enquiry must be at least 10 characters"
                )
            )

        # Validate maximum lengths for all fields
        for field, max_length in MAX_LENGTHS.items():
            value = input_data.get(field, "")
            if value and len(value) > max_length:
                errors.append(
                    ContactFormError(
                        field=field,
                        message=f"{field.replace('_', ' ').title()} must not exceed {max_length} characters",
                    )
                )

        return errors

    @classmethod
    def perform_mutation(cls, _root, info: ResolveInfo, /, **data):
        input_data = data.get("input")
        # input is required in Arguments, so this should never be None
        assert input_data is not None

        # Check rate limit
        rate_limiter = get_contact_form_rate_limiter()
        ip_address = get_client_ip(info.context)
        is_allowed, retry_after = rate_limiter.is_allowed(ip_address)

        if not is_allowed:
            # retry_after is always an int when is_allowed is False
            assert retry_after is not None
            error_message = (
                f"Too many submissions. Please try again in {retry_after} seconds."
            )
            return ContactFormSubmit(
                success=False,
                errors=[ContactFormError(field=None, message=error_message)],
            )

        # Validate input
        validation_errors = cls.validate_input(input_data)
        if validation_errors:
            return ContactFormSubmit(success=False, errors=validation_errors)

        # Get site and plugin manager
        site = get_site_promise(info.context).get()
        manager = get_plugin_manager_promise(info.context).get()

        # Prepare payload for email
        def generate_payload():
            return {
                "business_name": input_data.get("business_name"),
                "contact_name": input_data.get("contact_name"),
                "email": input_data.get("email"),
                "phone": input_data.get("phone", "N/A"),
                "order_number": input_data.get("order_number", "N/A"),
                "enquiry": input_data.get("enquiry"),
                "site_name": site.name,
                "domain": site.domain,
            }

        # Trigger notification
        handler = NotifyHandler(generate_payload)
        manager.notify(
            AdminNotifyEvent.CONTACT_FORM_SUBMISSION,
            payload_func=handler.payload,
        )

        return ContactFormSubmit(success=True, errors=[])
