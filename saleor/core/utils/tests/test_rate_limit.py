import time
import uuid
from unittest.mock import Mock, patch

import pytest
from django.core.cache import cache

from ..rate_limit import (
    RateLimiter,
    RateLimitExceeded,
    get_client_ip,
    get_contact_form_rate_limiter,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before and after each test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def unique_prefix():
    """Generate a unique prefix for each test to avoid cache collisions."""
    return f"test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def isolated_cache():
    """Provide an isolated in-memory cache for tests - no shared Redis."""
    cache_data = {}

    mock = Mock()
    mock.get.side_effect = lambda key, default=None: cache_data.get(key, default)
    mock.set.side_effect = (
        lambda key, value, timeout=None: cache_data.update({key: value}) or None
    )
    mock.clear.side_effect = lambda: cache_data.clear()

    return mock


def test_rate_limiter_allows_requests_under_limit(unique_prefix):
    # given
    limiter = RateLimiter(key_prefix=unique_prefix, max_requests=3, window_seconds=60)

    # when/then - First 3 requests should be allowed
    for _ in range(3):
        is_allowed, retry_after = limiter.is_allowed("user1")
        assert is_allowed is True
        assert retry_after is None


def test_rate_limiter_blocks_requests_over_limit(isolated_cache):
    # given
    limiter = RateLimiter(key_prefix="test_blocks", max_requests=3, window_seconds=60)

    # when - Make 4 requests (limit is 3)
    with patch("saleor.core.utils.rate_limit.cache", isolated_cache):
        for _ in range(3):
            limiter.is_allowed("user1")

        # then - 4th request should be blocked
        is_allowed, retry_after = limiter.is_allowed("user1")
        assert is_allowed is False
        assert retry_after is not None
        assert retry_after > 0


def test_rate_limiter_different_identifiers_independent(unique_prefix):
    # given
    limiter = RateLimiter(key_prefix=unique_prefix, max_requests=2, window_seconds=60)

    # when - User1 makes 2 requests, user2 makes 1 request
    limiter.is_allowed("user1")
    limiter.is_allowed("user1")
    is_allowed, _ = limiter.is_allowed("user2")

    # then - User2 should still be allowed despite user1 being at limit
    assert is_allowed is True


def test_rate_limiter_sliding_window(isolated_cache):
    # given
    limiter = RateLimiter(key_prefix="test_sliding", max_requests=2, window_seconds=1)

    with patch("saleor.core.utils.rate_limit.cache", isolated_cache):
        # when - Make 2 requests
        limiter.is_allowed("user1")
        limiter.is_allowed("user1")

        # then - 3rd request should be blocked
        is_allowed, _ = limiter.is_allowed("user1")
        assert is_allowed is False

        # when - Wait for window to expire
        time.sleep(1.1)

        # then - Should be allowed again
        is_allowed, _ = limiter.is_allowed("user1")
        assert is_allowed is True


def test_rate_limiter_cache_key_format():
    # given
    limiter = RateLimiter(
        key_prefix="contact_form", max_requests=5, window_seconds=3600
    )

    # when
    cache_key = limiter._get_cache_key("192.168.1.1")

    # then
    assert cache_key == "ratelimit:contact_form:192.168.1.1"


def test_rate_limiter_handles_cache_failure_gracefully(unique_prefix):
    # given
    limiter = RateLimiter(key_prefix=unique_prefix, max_requests=3, window_seconds=60)

    # when - Cache fails
    with patch(
        "saleor.core.utils.rate_limit.cache.get", side_effect=Exception("Cache error")
    ):
        is_allowed, retry_after = limiter.is_allowed("user1")

    # then - Should allow request when cache fails (fail open)
    assert is_allowed is True
    assert retry_after is None


def test_rate_limiter_check_or_raise_allows_when_under_limit(unique_prefix):
    # given
    limiter = RateLimiter(key_prefix=unique_prefix, max_requests=3, window_seconds=60)

    # when/then - Should not raise
    limiter.check_or_raise("user1")
    limiter.check_or_raise("user1")
    limiter.check_or_raise("user1")


def test_rate_limiter_check_or_raise_raises_when_over_limit(unique_prefix):
    # given
    limiter = RateLimiter(
        key_prefix=f"{unique_prefix}_raise_over", max_requests=2, window_seconds=60
    )

    # when - Make 2 requests (at limit)
    user_id = f"{unique_prefix}_user_raise_over"
    limiter.check_or_raise(user_id)
    limiter.check_or_raise(user_id)

    # then - 3rd request should raise
    with pytest.raises(RateLimitExceeded) as exc_info:
        limiter.check_or_raise(user_id)

    assert "Rate limit exceeded" in str(exc_info.value)
    assert exc_info.value.retry_after > 0


def test_rate_limiter_check_or_raise_custom_message(unique_prefix):
    # given
    limiter = RateLimiter(
        key_prefix=f"{unique_prefix}_custom_msg", max_requests=1, window_seconds=60
    )
    user_id = f"{unique_prefix}_user_custom_msg"
    limiter.check_or_raise(user_id)

    # when/then
    with pytest.raises(RateLimitExceeded) as exc_info:
        limiter.check_or_raise(user_id, error_message="Custom error")

    assert str(exc_info.value) == "Custom error"


def test_get_client_ip_from_remote_addr():
    # given
    request = Mock()
    request.META = {"REMOTE_ADDR": "192.168.1.100"}

    # when
    ip = get_client_ip(request)

    # then
    assert ip == "192.168.1.100"


def test_get_client_ip_from_x_forwarded_for():
    # given
    request = Mock()
    request.META = {
        "HTTP_X_FORWARDED_FOR": "203.0.113.1, 198.51.100.1, 192.168.1.1",
        "REMOTE_ADDR": "192.168.1.1",
    }

    # when
    ip = get_client_ip(request)

    # then - Should use first IP in X-Forwarded-For
    assert ip == "203.0.113.1"


def test_get_client_ip_handles_missing_meta():
    # given
    request = Mock()
    request.META = {}

    # when
    ip = get_client_ip(request)

    # then
    assert ip == "unknown"


def test_get_client_ip_strips_whitespace():
    # given
    request = Mock()
    request.META = {
        "HTTP_X_FORWARDED_FOR": "  203.0.113.1  , 198.51.100.1",
    }

    # when
    ip = get_client_ip(request)

    # then
    assert ip == "203.0.113.1"


def test_get_contact_form_rate_limiter_default_config():
    # when
    limiter = get_contact_form_rate_limiter()

    # then
    assert limiter.key_prefix == "contact_form"
    assert limiter.max_requests == 5  # Default
    assert limiter.window_seconds == 3600  # Default (1 hour)


@pytest.mark.django_db
def test_get_contact_form_rate_limiter_custom_config(settings):
    # given
    settings.CONTACT_FORM_RATE_LIMIT_MAX = 10
    settings.CONTACT_FORM_RATE_LIMIT_WINDOW = 7200

    # when
    limiter = get_contact_form_rate_limiter()

    # then
    assert limiter.max_requests == 10
    assert limiter.window_seconds == 7200


def test_retry_after_calculation(isolated_cache):
    # given
    limiter = RateLimiter(key_prefix="test_retry", max_requests=2, window_seconds=60)
    user_id = "test_user"

    # when - Hit the limit
    with patch("saleor.core.utils.rate_limit.cache", isolated_cache):
        limiter.is_allowed(user_id)
        time.sleep(0.1)
        limiter.is_allowed(user_id)

        # then - Check retry_after is reasonable
        _, retry_after = limiter.is_allowed(user_id)
        assert retry_after is not None
        assert 55 <= retry_after <= 60  # Should be close to window size


def test_rate_limiter_expired_timestamps_removed(isolated_cache):
    # given
    limiter = RateLimiter(key_prefix="test_expired", max_requests=2, window_seconds=1)

    with patch("saleor.core.utils.rate_limit.cache", isolated_cache):
        # when - Make requests, wait for expiry, make more requests
        limiter.is_allowed("user1")
        limiter.is_allowed("user1")

        # Verify at limit
        is_allowed, _ = limiter.is_allowed("user1")
        assert is_allowed is False

        # Wait for window to expire
        time.sleep(1.1)

        # Make 2 more requests (old ones should be expired)
        is_allowed1, _ = limiter.is_allowed("user1")
        is_allowed2, _ = limiter.is_allowed("user1")

        # then - Both new requests should succeed
        assert is_allowed1 is True
        assert is_allowed2 is True
