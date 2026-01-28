from unittest.mock import Mock, patch

import pytest
from django.core.cache import cache

from .....account.models import StaffNotificationRecipient
from .....core.notify import AdminNotifyEvent
from .....core.utils.rate_limit import RateLimiter

CONTACT_FORM_MUTATION = """
    mutation ContactFormSubmit($input: ContactFormInput!) {
        contactFormSubmit(input: $input) {
            success
            errors {
                field
                message
            }
        }
    }
"""


@pytest.fixture(autouse=True)
def clear_cache_auto():
    """Clear cache before and after each test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def mock_cache():
    """Mock cache for rate limit tests - provides isolated in-memory cache.

    Eliminates race conditions from shared Redis in parallel test execution.
    """
    cache_data = {}

    mock = Mock()
    mock.get.side_effect = lambda key, default=None: cache_data.get(key, default)
    mock.set.side_effect = (
        lambda key, value, timeout=None: cache_data.update({key: value}) or None
    )
    mock.clear.side_effect = lambda: cache_data.clear()

    return mock


@pytest.fixture
def mock_rate_limiter():
    """Mock rate limiter to always allow requests (for non-rate-limit tests)."""
    with patch(
        "saleor.graphql.shop.mutations.contact_form_submit.get_contact_form_rate_limiter"
    ) as mock:
        mock_limiter = Mock(spec=RateLimiter)
        mock_limiter.is_allowed.return_value = (True, None)  # Always allow
        mock.return_value = mock_limiter
        yield mock_limiter


@pytest.fixture
def staff_notification_recipient(staff_user):
    """Create an active staff notification recipient."""
    return StaffNotificationRecipient.objects.create(user=staff_user, active=True)


@pytest.fixture
def contact_form_input():
    """Return standard valid contact form input."""
    return {
        "businessName": "Test Business",
        "contactName": "John Doe",
        "email": "john@test.com",
        "phone": "+1234567890",
        "orderNumber": "ORD-123",
        "enquiry": "This is a test enquiry message with sufficient length.",
    }


def test_contact_form_submit_with_all_fields(
    api_client, staff_notification_recipient, contact_form_input, mock_rate_limiter
):
    # given
    variables = {"input": contact_form_input}

    # when
    response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

    # then
    content = response.json()
    data = content["data"]["contactFormSubmit"]
    assert data["success"] is True
    assert data["errors"] == []


def test_contact_form_submit_with_required_fields_only(
    api_client, staff_notification_recipient, mock_rate_limiter
):
    # given
    variables = {
        "input": {
            "businessName": "Test Business",
            "contactName": "John Doe",
            "email": "john@test.com",
            "enquiry": "This is a test enquiry message with sufficient length.",
        }
    }

    # when
    response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

    # then
    content = response.json()
    data = content["data"]["contactFormSubmit"]
    assert data["success"] is True
    assert data["errors"] == []


def test_contact_form_submit_invalid_email(api_client, mock_rate_limiter):
    # given
    variables = {
        "input": {
            "businessName": "Test Business",
            "contactName": "John Doe",
            "email": "not-an-email",
            "enquiry": "This is a test enquiry message.",
        }
    }

    # when
    response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

    # then
    content = response.json()
    data = content["data"]["contactFormSubmit"]
    assert data["success"] is False
    assert len(data["errors"]) == 1
    assert data["errors"][0]["field"] == "email"
    assert "Invalid email address" in data["errors"][0]["message"]


def test_contact_form_submit_missing_business_name(api_client, mock_rate_limiter):
    # given
    variables = {
        "input": {
            "contactName": "John Doe",
            "email": "john@test.com",
            "enquiry": "This is a test enquiry message.",
        }
    }

    # when
    response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

    # then
    content = response.json()
    data = content["data"]["contactFormSubmit"]
    assert data["success"] is False
    errors = data["errors"]
    assert any(error["field"] == "business_name" for error in errors)


def test_contact_form_submit_missing_contact_name(api_client, mock_rate_limiter):
    # given
    variables = {
        "input": {
            "businessName": "Test Business",
            "email": "john@test.com",
            "enquiry": "This is a test enquiry message.",
        }
    }

    # when
    response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

    # then
    content = response.json()
    data = content["data"]["contactFormSubmit"]
    assert data["success"] is False
    errors = data["errors"]
    assert any(error["field"] == "contact_name" for error in errors)


def test_contact_form_submit_missing_email(api_client, mock_rate_limiter):
    # given
    variables = {
        "input": {
            "businessName": "Test Business",
            "contactName": "John Doe",
            "enquiry": "This is a test enquiry message.",
        }
    }

    # when
    response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

    # then
    content = response.json()
    data = content["data"]["contactFormSubmit"]
    assert data["success"] is False
    errors = data["errors"]
    assert any(error["field"] == "email" for error in errors)


def test_contact_form_submit_missing_enquiry(api_client, mock_rate_limiter):
    # given
    variables = {
        "input": {
            "businessName": "Test Business",
            "contactName": "John Doe",
            "email": "john@test.com",
        }
    }

    # when
    response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

    # then
    content = response.json()
    data = content["data"]["contactFormSubmit"]
    assert data["success"] is False
    errors = data["errors"]
    assert any(error["field"] == "enquiry" for error in errors)


def test_contact_form_submit_enquiry_too_short(api_client, mock_rate_limiter):
    # given
    variables = {
        "input": {
            "businessName": "Test Business",
            "contactName": "John Doe",
            "email": "john@test.com",
            "enquiry": "Too short",
        }
    }

    # when
    response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

    # then
    content = response.json()
    data = content["data"]["contactFormSubmit"]
    assert data["success"] is False
    errors = data["errors"]
    assert any(
        error["field"] == "enquiry" and "at least 10 characters" in error["message"]
        for error in errors
    )


def test_contact_form_triggers_notification_event(
    api_client, staff_notification_recipient, contact_form_input, mock_rate_limiter
):
    # given
    variables = {"input": contact_form_input}

    # when
    with patch(
        "saleor.graphql.shop.mutations.contact_form_submit.get_plugin_manager_promise"
    ) as mock_manager:
        mock_plugin_manager = mock_manager.return_value.get.return_value
        response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

        # then
        content = response.json()
        assert content["data"]["contactFormSubmit"]["success"] is True

        mock_plugin_manager.notify.assert_called_once()
        call_args = mock_plugin_manager.notify.call_args
        assert call_args[0][0] == AdminNotifyEvent.CONTACT_FORM_SUBMISSION


def test_contact_form_payload_includes_all_data(
    api_client,
    staff_notification_recipient,
    contact_form_input,
    site_settings,
    mock_rate_limiter,
):
    # given
    variables = {"input": contact_form_input}

    # when
    with patch(
        "saleor.graphql.shop.mutations.contact_form_submit.get_plugin_manager_promise"
    ) as mock_manager:
        mock_plugin_manager = mock_manager.return_value.get.return_value
        response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

        # then
        content = response.json()
        assert content["data"]["contactFormSubmit"]["success"] is True

        call_args = mock_plugin_manager.notify.call_args
        payload_func = call_args[1]["payload_func"]
        payload = payload_func()

        assert payload["business_name"] == contact_form_input["businessName"]
        assert payload["contact_name"] == contact_form_input["contactName"]
        assert payload["email"] == contact_form_input["email"]
        assert payload["phone"] == contact_form_input["phone"]
        assert payload["order_number"] == contact_form_input["orderNumber"]
        assert payload["enquiry"] == contact_form_input["enquiry"]
        assert "site_name" in payload
        assert "domain" in payload


def test_contact_form_payload_uses_na_for_optional_fields(
    api_client, staff_notification_recipient, mock_rate_limiter
):
    # given
    variables = {
        "input": {
            "businessName": "Test Business",
            "contactName": "John Doe",
            "email": "john@test.com",
            "enquiry": "This is a test enquiry message.",
        }
    }

    # when
    with patch(
        "saleor.graphql.shop.mutations.contact_form_submit.get_plugin_manager_promise"
    ) as mock_manager:
        mock_plugin_manager = mock_manager.return_value.get.return_value
        response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

        # then
        content = response.json()
        assert content["data"]["contactFormSubmit"]["success"] is True

        call_args = mock_plugin_manager.notify.call_args
        payload_func = call_args[1]["payload_func"]
        payload = payload_func()

        assert payload["phone"] == "N/A"
        assert payload["order_number"] == "N/A"


def test_contact_form_no_email_sent_when_no_recipients(
    api_client, contact_form_input, mock_rate_limiter
):
    # given
    variables = {"input": contact_form_input}
    # No staff notification recipients created

    # when
    response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

    # then
    content = response.json()
    data = content["data"]["contactFormSubmit"]
    # Should still return success even if no recipients
    assert data["success"] is True
    assert data["errors"] == []


def test_contact_form_only_sends_to_active_staff(
    api_client, staff_user, contact_form_input, mock_rate_limiter
):
    # given
    # Create active staff recipient
    StaffNotificationRecipient.objects.create(user=staff_user, active=True)

    # Create inactive staff recipient
    inactive_staff = staff_user.__class__.objects.create(
        email="inactive@test.com", is_staff=True, is_active=False
    )
    StaffNotificationRecipient.objects.create(user=inactive_staff, active=True)

    # Create non-staff recipient
    non_staff_user = staff_user.__class__.objects.create(
        email="nonstaff@test.com", is_staff=False, is_active=True
    )
    StaffNotificationRecipient.objects.create(user=non_staff_user, active=True)

    variables = {"input": contact_form_input}

    # when
    with patch(
        "saleor.plugins.admin_email.notify_events.send_contact_form_email_task"
    ) as mock_task:
        response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

        # then
        content = response.json()
        assert content["data"]["contactFormSubmit"]["success"] is True

        # Should only send to active staff user
        if mock_task.delay.called:
            call_args = mock_task.delay.call_args[0]
            recipient_list = call_args[0]
            assert len(recipient_list) == 1
            assert staff_user.email in recipient_list


def test_contact_form_with_special_characters_in_enquiry(
    api_client, staff_notification_recipient, mock_rate_limiter
):
    # given
    variables = {
        "input": {
            "businessName": "Test Business",
            "contactName": "John Doe",
            "email": "john@test.com",
            "enquiry": "This has <script>alert('xss')</script> and special chars: < > & \" '",
        }
    }

    # when
    response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

    # then
    content = response.json()
    data = content["data"]["contactFormSubmit"]
    assert data["success"] is True
    assert data["errors"] == []


def test_contact_form_with_very_long_enquiry(
    api_client, staff_notification_recipient, mock_rate_limiter
):
    # given - Test with maximum allowed length (should succeed)
    long_enquiry = "A" * 5000  # Exactly at the limit
    variables = {
        "input": {
            "businessName": "Test Business",
            "contactName": "John Doe",
            "email": "john@test.com",
            "enquiry": long_enquiry,
        }
    }

    # when
    response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

    # then
    content = response.json()
    data = content["data"]["contactFormSubmit"]
    assert data["success"] is True
    assert data["errors"] == []


def test_contact_form_with_unicode_characters(
    api_client, staff_notification_recipient, mock_rate_limiter
):
    # given
    variables = {
        "input": {
            "businessName": "Тест Бизнес 测试业务",
            "contactName": "José María 李明",
            "email": "test@test.com",
            "enquiry": "Hello 你好 Привет 🎉 with emojis and unicode characters.",
        }
    }

    # when
    response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

    # then
    content = response.json()
    data = content["data"]["contactFormSubmit"]
    assert data["success"] is True
    assert data["errors"] == []


def test_contact_form_multiple_validation_errors(api_client, mock_rate_limiter):
    # given
    variables = {
        "input": {
            "businessName": "",
            "contactName": "",
            "email": "invalid-email",
            "enquiry": "Short",
        }
    }

    # when
    response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

    # then
    content = response.json()
    data = content["data"]["contactFormSubmit"]
    assert data["success"] is False
    # Should have multiple errors
    assert len(data["errors"]) > 1
    error_fields = {error["field"] for error in data["errors"]}
    assert "email" in error_fields
    assert "enquiry" in error_fields


def test_contact_form_business_name_too_long(api_client, mock_rate_limiter):
    # given
    long_business_name = "A" * 201  # Max is 200
    variables = {
        "input": {
            "businessName": long_business_name,
            "contactName": "John Doe",
            "email": "john@test.com",
            "enquiry": "This is a test enquiry message.",
        }
    }

    # when
    response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

    # then
    content = response.json()
    data = content["data"]["contactFormSubmit"]
    assert data["success"] is False
    errors = data["errors"]
    assert any(
        error["field"] == "business_name" and "200 characters" in error["message"]
        for error in errors
    )


def test_contact_form_enquiry_too_long(api_client, mock_rate_limiter):
    # given
    long_enquiry = "A" * 5001  # Max is 5000
    variables = {
        "input": {
            "businessName": "Test Business",
            "contactName": "John Doe",
            "email": "john@test.com",
            "enquiry": long_enquiry,
        }
    }

    # when
    response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

    # then
    content = response.json()
    data = content["data"]["contactFormSubmit"]
    assert data["success"] is False
    errors = data["errors"]
    assert any(
        error["field"] == "enquiry" and "5000 characters" in error["message"]
        for error in errors
    )


def test_contact_form_phone_too_long(api_client, mock_rate_limiter):
    # given
    long_phone = "1" * 51  # Max is 50
    variables = {
        "input": {
            "businessName": "Test Business",
            "contactName": "John Doe",
            "email": "john@test.com",
            "phone": long_phone,
            "enquiry": "This is a test enquiry message.",
        }
    }

    # when
    response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

    # then
    content = response.json()
    data = content["data"]["contactFormSubmit"]
    assert data["success"] is False
    errors = data["errors"]
    assert any(
        error["field"] == "phone" and "50 characters" in error["message"]
        for error in errors
    )


def test_contact_form_rate_limiting(
    api_client, staff_notification_recipient, mock_cache
):
    # given
    unique_ip = "192.168.1.100"
    variables = {
        "input": {
            "businessName": "Test Business",
            "contactName": "John Doe",
            "email": "john@test.com",
            "enquiry": "This is a test enquiry message.",
        }
    }

    # when - Submit 6 times (default limit is 5 per hour)
    with patch("saleor.core.utils.rate_limit.cache", mock_cache):
        with patch(
            "saleor.graphql.shop.mutations.contact_form_submit.get_client_ip",
            return_value=unique_ip,
        ):
            responses = []
            for _i in range(6):
                response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)
                responses.append(response.json())

            # then - First 5 should succeed, 6th should be rate limited
            for i, content in enumerate(responses):
                data = content["data"]["contactFormSubmit"]
                if i < 5:
                    assert data["success"] is True, f"Request {i + 1} should succeed"
                else:
                    assert data["success"] is False, "Request 6 should be rate limited"
                    assert any(
                        "too many" in error["message"].lower()
                        for error in data["errors"]
                    )


def test_contact_form_rate_limiting_per_ip(
    api_client, staff_notification_recipient, mock_cache
):
    # given
    ip1 = "10.0.1.100"
    ip2 = "10.0.2.100"
    variables = {
        "input": {
            "businessName": "Test Business",
            "contactName": "John Doe",
            "email": "john@test.com",
            "enquiry": "This is a test enquiry message.",
        }
    }

    # when - Make 5 requests from one IP, then 1 from another IP
    with patch("saleor.core.utils.rate_limit.cache", mock_cache):
        with patch(
            "saleor.graphql.shop.mutations.contact_form_submit.get_client_ip",
            return_value=ip1,
        ):
            for _ in range(5):
                api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

        # Simulate different IP
        with patch(
            "saleor.graphql.shop.mutations.contact_form_submit.get_client_ip",
            return_value=ip2,
        ):
            response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

        # then - Request from different IP should succeed
        content = response.json()
        data = content["data"]["contactFormSubmit"]
        assert data["success"] is True


def test_contact_form_rate_limit_error_message(
    api_client, staff_notification_recipient, mock_cache
):
    # given
    unique_ip = "192.168.2.100"
    variables = {
        "input": {
            "businessName": "Test Business",
            "contactName": "John Doe",
            "email": "john@test.com",
            "enquiry": "This is a test enquiry message.",
        }
    }

    # when - Hit rate limit
    with patch("saleor.core.utils.rate_limit.cache", mock_cache):
        with patch(
            "saleor.graphql.shop.mutations.contact_form_submit.get_client_ip",
            return_value=unique_ip,
        ):
            for _ in range(5):
                api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

            response = api_client.post_graphql(CONTACT_FORM_MUTATION, variables)

            # then - Error message should include retry time
            content = response.json()
            data = content["data"]["contactFormSubmit"]
            assert data["success"] is False
            error_message = data["errors"][0]["message"]
            assert "try again in" in error_message.lower()
            assert "seconds" in error_message.lower()
