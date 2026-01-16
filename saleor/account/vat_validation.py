"""VAT number validation using VIES (VAT Information Exchange System) API."""

import logging

import requests
from django.core.cache import cache

from ..core.http_client import HTTPClient

logger = logging.getLogger(__name__)

# VIES API endpoint
VIES_API_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number"

# Cache settings (hardcoded as per requirements)
VIES_TIMEOUT_SECONDS = 5
CACHE_TTL_VALID = 60 * 60 * 24  # 24 hours for valid VAT numbers
CACHE_TTL_INVALID = 60 * 60  # 1 hour for invalid VAT numbers


def normalize_vat_number(vat: str) -> str:
    """Normalize VAT number by stripping whitespace and converting to uppercase.

    Args:
        vat: Raw VAT number string

    Returns:
        Normalized VAT number (uppercase, no whitespace)

    """
    return vat.strip().upper().replace(" ", "")


def _get_cache_key(vat: str, country_code: str) -> str:
    """Generate cache key for VAT validation result."""
    return f"vat_validation:{country_code}:{vat}"


def get_cached_vat_validation(vat: str, country_code: str) -> dict | None:
    """Retrieve cached VIES validation result.

    Args:
        vat: Normalized VAT number
        country_code: Two-letter country code (e.g., 'DE', 'FR')

    Returns:
        Cached validation result dict or None if not cached

    """
    cache_key = _get_cache_key(vat, country_code)
    return cache.get(cache_key)


def cache_vat_validation(
    vat: str, country_code: str, result: dict, ttl_seconds: int
) -> None:
    """Cache VIES validation result.

    Args:
        vat: Normalized VAT number
        country_code: Two-letter country code
        result: Validation result dict from VIES API
        ttl_seconds: Time-to-live in seconds

    """
    cache_key = _get_cache_key(vat, country_code)
    cache.set(cache_key, result, ttl_seconds)


def validate_vat_vies(
    vat: str, country_code: str, timeout: int = VIES_TIMEOUT_SECONDS
) -> dict:
    """Validate VAT number using VIES (VAT Information Exchange System) API.

    This function calls the European Commission's VIES API to validate
    VAT numbers. The validation is performed synchronously and will block
    until the API responds or times out.

    Args:
        vat: Normalized VAT number (without country code prefix)
        country_code: Two-letter country code (e.g., 'DE', 'FR')
        timeout: Request timeout in seconds (default: 5)

    Returns:
        Dictionary with validation results:
            - valid (bool): Whether the VAT number is valid
            - name (str): Company name from VIES (if available)
            - address (str): Company address from VIES (if available)

    Raises:
        requests.Timeout: If VIES API doesn't respond within timeout
        requests.RequestException: If VIES API request fails

    Example:
        >>> validate_vat_vies("123456789", "DE")
        {
            "valid": True,
            "name": "Example Company GmbH",
            "address": "Street 123, 10115 Berlin"
        }

    """
    # Check cache first
    cached_result = get_cached_vat_validation(vat, country_code)
    if cached_result is not None:
        logger.info(
            "VAT validation cache hit", extra={"vat": vat, "country_code": country_code}
        )
        return cached_result

    # Remove country code prefix if present in the VAT number
    vat_number = vat
    if vat.startswith(country_code):
        vat_number = vat[len(country_code) :]

    # Prepare VIES API request
    params = {
        "countryCode": country_code,
        "vatNumber": vat_number,
    }

    logger.info(
        "Calling VIES API for VAT validation",
        extra={"vat": vat_number, "country_code": country_code},
    )

    try:
        # Call VIES API using hardened HTTP client
        response = HTTPClient.send_request(
            "GET", VIES_API_URL, params=params, timeout=timeout, allow_redirects=False
        )
        response.raise_for_status()

        # Parse response
        data = response.json()

        result = {
            "valid": data.get("valid", False),
            "name": data.get("name", ""),
            "address": data.get("address", ""),
        }

        # Cache the result
        ttl = CACHE_TTL_VALID if result["valid"] else CACHE_TTL_INVALID
        cache_vat_validation(vat, country_code, result, ttl)

        logger.info(
            "VIES API validation completed",
            extra={
                "vat": vat_number,
                "country_code": country_code,
                "valid": result["valid"],
            },
        )

        return result

    except requests.Timeout:
        logger.error(
            "VIES API timeout",
            extra={"vat": vat_number, "country_code": country_code, "timeout": timeout},
        )
        raise

    except requests.RequestException as e:
        logger.error(
            "VIES API request failed",
            extra={"vat": vat_number, "country_code": country_code, "error": str(e)},
        )
        raise
