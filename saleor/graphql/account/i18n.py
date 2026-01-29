from django.core.exceptions import ValidationError

from ...account.forms import get_address_form
from ...account.models import Address
from ...account.validators import validate_possible_number
from ...core.exceptions import PermissionDenied
from ...permission.auth_filters import AuthorizationFilters
from ...permission.enums import (
    AccountPermissions,
    BasePermissionEnum,
    CheckoutPermissions,
    OrderPermissions,
    ProductPermissions,
    SitePermissions,
)
from ...permission.utils import all_permissions_required
from ..core import ResolveInfo

EU_COUNTRIES = (
    "AT",
    "BE",
    "BG",
    "HR",
    "CY",
    "CZ",
    "DK",
    "EE",
    "FI",
    "FR",
    "DE",
    "GR",
    "HU",
    "IE",
    "IT",
    "LV",
    "LT",
    "LU",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SK",
    "SI",
    "ES",
    "SE",
)

SKIP_ADDRESS_VALIDATION_PERMISSION_MAP: dict[str, list[BasePermissionEnum]] = {
    "addressCreate": [AccountPermissions.MANAGE_USERS],
    "addressUpdate": [AccountPermissions.MANAGE_USERS],
    "customerBulkUpdate": [AccountPermissions.MANAGE_USERS],
    "draftOrderCreate": [OrderPermissions.MANAGE_ORDERS],
    "draftOrderUpdate": [OrderPermissions.MANAGE_ORDERS],
    "orderUpdate": [OrderPermissions.MANAGE_ORDERS],
    "orderBulkCreate": [OrderPermissions.MANAGE_ORDERS_IMPORT],
    "createWarehouse": [ProductPermissions.MANAGE_PRODUCTS],
    "updateWarehouse": [ProductPermissions.MANAGE_PRODUCTS],
    "shopAddressUpdate": [SitePermissions.MANAGE_SETTINGS],
    "checkoutCreate": [
        CheckoutPermissions.HANDLE_CHECKOUTS,
        AuthorizationFilters.AUTHENTICATED_APP,
    ],
    "checkoutShippingAddressUpdate": [
        CheckoutPermissions.HANDLE_CHECKOUTS,
        AuthorizationFilters.AUTHENTICATED_APP,
    ],
    "checkoutBillingAddressUpdate": [
        CheckoutPermissions.HANDLE_CHECKOUTS,
        AuthorizationFilters.AUTHENTICATED_APP,
    ],
    "accountAddressCreate": [
        AuthorizationFilters.AUTHENTICATED_APP,
        AccountPermissions.IMPERSONATE_USER,
    ],
    "accountUpdate": [
        AuthorizationFilters.AUTHENTICATED_APP,
        AccountPermissions.IMPERSONATE_USER,
    ],
    "accountAddressUpdate": [
        AccountPermissions.MANAGE_USERS,
        AuthorizationFilters.AUTHENTICATED_APP,
    ],
}


class I18nMixin:
    """A mixin providing methods necessary to fulfill the internationalization process.

    It is to be used with BaseMutation or ModelMutation.
    """

    @classmethod
    def construct_instance(cls, instance, cleaned_data):
        pass

    @classmethod
    def clean_instance(cls, _info: ResolveInfo, _instance):
        pass

    @classmethod
    def _validate_address_form(
        cls,
        address_data: dict,
        address_type: str | None = None,
        instance=None,
        format_check=True,
        required_check=True,
        enable_normalization=True,
    ):
        phone = address_data.get("phone", None)
        params = {"address_type": address_type} if address_type else {}
        if phone:
            try:
                validate_possible_number(phone, address_data.get("country"))
            except ValidationError as exc:
                if format_check:
                    raise ValidationError(
                        {
                            "phone": ValidationError(
                                f"'{phone}' is not a valid phone number.",
                                code=exc.code,
                                params=params,
                            )
                        }
                    ) from exc

        address_form = get_address_form(
            address_data,
            address_data.get("country"),
            instance=instance,
            enable_normalization=enable_normalization,
        )
        validation_skipped = False
        if not address_form.is_valid():
            validation_skipped = True
            errors = cls.attach_params_to_address_form_errors(
                address_form, params, format_check, required_check
            )
            if errors:
                raise ValidationError(errors)

        if address_form.cleaned_data["metadata"] is None:
            address_form.cleaned_data["metadata"] = {}
        if address_form.cleaned_data["private_metadata"] is None:
            address_form.cleaned_data["private_metadata"] = {}
        address_form.cleaned_data["validation_skipped"] = validation_skipped

        return address_form

    @classmethod
    def attach_params_to_address_form_errors(
        cls,
        address_form,
        params: dict[str, str],
        values_check=True,
        required_check=True,
    ):
        address_errors_dict = address_form.errors.as_data()
        errors_dict = {}
        for field, errors in address_errors_dict.items():
            for error in errors:
                if not error.params:
                    error.params = params
                else:
                    error.params.update(params)
                if error.code != "required":
                    if values_check:
                        errors_dict[field] = errors
                    else:
                        address_form.cleaned_data[field] = address_form.data[field]
                if error.code == "required":
                    field_value = address_form.data.get(field)
                    if required_check:
                        errors_dict[field] = errors
                    elif field_value is not None:
                        address_form.cleaned_data[field] = field_value

        return errors_dict

    @classmethod
    def _extract_vat_from_metadata(cls, address_data: dict) -> str | None:
        """Extract VAT number from address metadata.

        Args:
            address_data: Dictionary containing address information

        Returns:
            VAT number string if found in metadata, None otherwise

        """
        metadata = address_data.get("metadata", [])
        if isinstance(metadata, list):
            for item in metadata:
                if item.get("key") == "vat_number":
                    return item.get("value")
        elif isinstance(metadata, dict):
            return metadata.get("vat_number")
        return None

    @classmethod
    def _validate_vat_number(
        cls,
        vat_number: str | None,
        country_code: str,
        address_type: str | None,
        require_vat: bool = False,
    ) -> None:
        """Validate VAT number via VIES API.

        Args:
            vat_number: VAT number from address metadata (may be None)
            country_code: Two-letter country code
            address_type: Type of address (billing/shipping) for error messages
            require_vat: If True, VAT is required (for customer checkout/orders).
                        If False, VAT is only validated if provided (for internal ops).

        Raises:
            ValidationError: If VAT is missing (when require_vat=True),
                           if VIES validation fails, or if VIES API is unavailable

        """
        import requests

        from ...account.error_codes import AccountErrorCode
        from ...account.vat_validation import (
            normalize_vat_number,
            validate_vat_vies,
        )

        params = {"address_type": address_type} if address_type else {}

        # VAT is required only for customer-facing operations (checkout/orders)
        if require_vat and not vat_number:
            raise ValidationError(
                {
                    "vat_number": ValidationError(
                        "VAT number is required.",
                        code=AccountErrorCode.REQUIRED.value,
                        params=params,
                    )
                }
            )

        # If no VAT provided and not required, skip validation
        if not vat_number:
            return

        # Normalize VAT number (strip whitespace, uppercase)
        normalized_vat = normalize_vat_number(vat_number)

        # Validate via VIES only - no manual format checks
        try:
            vies_result = validate_vat_vies(normalized_vat, country_code)

            if not vies_result.get("valid"):
                raise ValidationError(
                    {
                        "vat_number": ValidationError(
                            "VAT number could not be verified with VIES.",
                            code=AccountErrorCode.VAT_INVALID.value,
                            params=params,
                        )
                    }
                ) from None
        except (requests.RequestException, requests.Timeout):
            # VIES API unavailable - reject per user requirement (strict mode)
            raise ValidationError(
                {
                    "vat_number": ValidationError(
                        "VAT validation service is temporarily unavailable. "
                        "Please try again later.",
                        code=AccountErrorCode.VAT_SERVICE_UNAVAILABLE.value,
                        params=params,
                    )
                }
            ) from None

    @classmethod
    def validate_address(
        cls,
        address_data: dict,
        *,
        address_type: str | None = None,
        instance: Address | None = None,
        info=None,
        format_check=True,
        required_check=True,
        enable_normalization=True,
        require_vat: bool = False,
    ) -> Address:
        if address_data.get("country") is None:
            params = {"address_type": address_type} if address_type else {}
            raise ValidationError(
                {
                    "country": ValidationError(
                        "This field is required.", code="required", params=params
                    )
                }
            )

        if address_data.get("skip_validation"):
            cls.can_skip_address_validation(info)
            format_check = False

        address_form = cls._validate_address_form(
            address_data,
            address_type,
            format_check=format_check,
            required_check=required_check,
            enable_normalization=enable_normalization,
        )
        address_data = address_form.cleaned_data

        # VAT Validation (only if not skipping validation)
        # Only require VAT if required_check is True (respect validation rules)
        if not address_data.get("skip_validation"):
            country_code = address_data.get("country")
            if country_code and country_code in EU_COUNTRIES:
                vat_number = cls._extract_vat_from_metadata(address_data)
                cls._validate_vat_number(
                    vat_number,
                    country_code,
                    address_type,
                    require_vat=require_vat and required_check,
                )

        if not instance:
            instance = Address()

        cls.construct_instance(instance, address_data)
        cls.clean_instance(info, instance)
        return instance

    @classmethod
    def can_skip_address_validation(cls, info: ResolveInfo | None):
        required_permissions = None
        if info:
            mutation_name = info.field_name
            required_permissions = SKIP_ADDRESS_VALIDATION_PERMISSION_MAP.get(
                mutation_name
            )

        if not required_permissions:
            raise ValidationError(
                {
                    "skip_validation": ValidationError(
                        "This mutation doesn't allow to skip address validation.",
                        code="invalid",
                    )
                }
            )
        if info and not all_permissions_required(info.context, required_permissions):
            raise PermissionDenied(
                f"To skip address validation, you need following permissions: "
                f"{', '.join(perm.name for perm in required_permissions)}.",
            )
