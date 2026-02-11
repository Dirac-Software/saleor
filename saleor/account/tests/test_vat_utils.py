from ..models import Address
from ..vat_utils import should_apply_vat_exemption


def test_should_apply_vat_exemption_with_valid_vat():
    address = Address(metadata={"vat_number": "DE123456789"})
    assert should_apply_vat_exemption(address) is True


def test_should_apply_vat_exemption_without_vat():
    address = Address(metadata={})
    assert should_apply_vat_exemption(address) is False


def test_should_apply_vat_exemption_with_empty_vat():
    address = Address(metadata={"vat_number": ""})
    assert should_apply_vat_exemption(address) is False


def test_should_apply_vat_exemption_with_whitespace_vat():
    address = Address(metadata={"vat_number": "   "})
    assert should_apply_vat_exemption(address) is False


def test_should_apply_vat_exemption_without_address():
    assert should_apply_vat_exemption(None) is False
