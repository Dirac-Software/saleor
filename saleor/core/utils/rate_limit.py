"""Rate limiting utilities for API endpoints."""

import logging

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Exception raised when rate limit is exceeded."""

    def __init__(self, message: str, retry_after: int):
        self.message = message
        self.retry_after = retry_after
        super().__init__(message)


class RateLimiter:
    """Rate limiter using sliding window counter algorithm.

    Tracks request timestamps in cache and enforces rate limits based on
    configurable windows and thresholds.
    """

    def __init__(
        self,
        key_prefix: str,
        max_requests: int,
        window_seconds: int,
        cache_name: str = "default",
    ):
        """Initialize rate limiter.

        Args:
            key_prefix: Prefix for cache keys (e.g., 'contact_form')
            max_requests: Maximum number of requests allowed in the time window
            window_seconds: Time window in seconds
            cache_name: Django cache backend to use

        """
        self.key_prefix = key_prefix
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.cache_name = cache_name

    def _get_cache_key(self, identifier: str) -> str:
        """Generate cache key for the identifier."""
        return f"ratelimit:{self.key_prefix}:{identifier}"

    def is_allowed(self, identifier: str) -> tuple[bool, int | None]:
        """Check if request is allowed under rate limit.

        Args:
            identifier: Unique identifier (e.g., IP address, user ID)

        Returns:
            Tuple of (is_allowed, retry_after_seconds)
            - is_allowed: True if request is allowed
            - retry_after: Seconds until rate limit resets (None if allowed)

        """
        cache_key = self._get_cache_key(identifier)
        now = timezone.now().timestamp()

        try:
            # Get existing request timestamps (make a copy to avoid mutation issues)
            timestamps = cache.get(cache_key) or []
            timestamps = list(timestamps)  # Ensure we have a mutable copy

            # Remove timestamps outside the current window
            valid_timestamps = [
                ts for ts in timestamps if now - ts < self.window_seconds
            ]

            # Check if limit would be exceeded
            if len(valid_timestamps) >= self.max_requests:
                # Calculate when the oldest request will expire
                oldest_timestamp = min(valid_timestamps)
                retry_after = int(self.window_seconds - (now - oldest_timestamp)) + 1
                return False, retry_after

            # Add current timestamp and update cache
            valid_timestamps.append(now)
            cache.set(cache_key, valid_timestamps, timeout=self.window_seconds)

            return True, None

        except Exception as e:
            # If cache fails, log error and allow the request through
            # This ensures cache failures don't break the application
            logger.exception(
                "Rate limiter cache error for key %s: %s",
                cache_key,
                str(e),
            )
            return True, None

    def check_or_raise(self, identifier: str, error_message: str | None = None):
        """Check rate limit and raise exception if exceeded.

        Args:
            identifier: Unique identifier (e.g., IP address)
            error_message: Custom error message (optional)

        Raises:
            RateLimitExceeded: If rate limit is exceeded

        """
        is_allowed, retry_after = self.is_allowed(identifier)

        if not is_allowed:
            # retry_after is always an int when is_allowed is False
            assert retry_after is not None
            if error_message is None:
                error_message = (
                    f"Rate limit exceeded. Maximum {self.max_requests} requests "
                    f"per {self.window_seconds} seconds. Try again in {retry_after} seconds."
                )

            raise RateLimitExceeded(error_message, retry_after)


def get_client_ip(request) -> str:
    """Extract client IP address from request.

    Handles proxy headers (X-Forwarded-For) correctly.

    Args:
        request: Django/GraphQL request object

    Returns:
        Client IP address as string

    """
    # Check for X-Forwarded-For header (used by proxies/load balancers)
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        # Take the first IP in the chain (client's IP)
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        # Fall back to REMOTE_ADDR
        ip = request.META.get("REMOTE_ADDR", "unknown")

    return ip


# Default rate limiters for common use cases
def get_contact_form_rate_limiter() -> RateLimiter:
    """Get rate limiter for contact form submissions.

    Configured via Django settings:
    - CONTACT_FORM_RATE_LIMIT_MAX (default: 5)
    - CONTACT_FORM_RATE_LIMIT_WINDOW (default: 3600 seconds = 1 hour)
    """
    max_requests = getattr(settings, "CONTACT_FORM_RATE_LIMIT_MAX", 5)
    window_seconds = getattr(settings, "CONTACT_FORM_RATE_LIMIT_WINDOW", 3600)

    return RateLimiter(
        key_prefix="contact_form",
        max_requests=max_requests,
        window_seconds=window_seconds,
    )
