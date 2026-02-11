from unittest.mock import Mock, patch

import pytest

from ..rate_limit import (
    RateLimiter,
    get_client_ip,
    get_contact_form_rate_limiter,
)


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


def test_rate_limiter_cache_key_format():
    # given
    limiter = RateLimiter(
        key_prefix="contact_form", max_requests=5, window_seconds=3600
    )

    # when
    cache_key = limiter._get_cache_key("192.168.1.1")

    # then
    assert cache_key == "ratelimit:contact_form:192.168.1.1"


def test_rate_limiter_handles_cache_failure_gracefully():
    # given
    limiter = RateLimiter(
        key_prefix="test_cache_fail", max_requests=3, window_seconds=60
    )

    # when - Cache fails
    with patch(
        "saleor.core.utils.rate_limit.cache.get", side_effect=Exception("Cache error")
    ):
        is_allowed, retry_after = limiter.is_allowed("user1")

    # then - Should allow request when cache fails (fail open)
    assert is_allowed is True
    assert retry_after is None


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
