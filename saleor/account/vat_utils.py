from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Address


def should_apply_vat_exemption(billing_address: "Address | None") -> bool:
    """Check if order should be VAT exempt based on billing address VAT number.

    Returns True if billing address has a vat_number in metadata.
    This implements EU reverse charge mechanism for B2B transactions.

    VAT number validation happens on input, so we only check existence here.
    """
    if not billing_address:
        return False

    vat_number = billing_address.metadata.get("vat_number")
    if not vat_number or not vat_number.strip():
        return False

    return True
