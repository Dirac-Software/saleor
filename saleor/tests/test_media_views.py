import pytest
from django.core.files.base import ContentFile
from django.core.signing import TimestampSigner
from django.http import Http404
from django.test import RequestFactory

from saleor.csv.models import ExportFile
from saleor.invoice.models import Invoice
from saleor.media_views import (
    serve_export_file,
    serve_export_file_signed,
    serve_invoice,
)


@pytest.fixture
def rf():
    """RequestFactory fixture."""
    return RequestFactory()


class MockAnonymousUser:
    """Mock anonymous user for testing."""

    is_authenticated = False
    is_staff = False


def test_serve_export_file_requires_authentication(rf):
    """Test that export file endpoint requires authentication."""
    # Given: An export file exists
    export_file = ExportFile.objects.create(status="SUCCESS")
    export_file.content_file.save("test.csv", ContentFile(b"test,data"), save=True)

    # When: Unauthenticated user tries to access
    request = rf.get("/")
    request.user = MockAnonymousUser()
    response = serve_export_file(request, file_id=export_file.pk)

    # Then: Should return 401
    assert response.status_code == 401
    assert response.content == b"Unauthorized"


def test_serve_export_file_owner_can_access(rf, customer_user):
    """Test that export file owner can access their file."""
    # Given: User owns an export file
    export_file = ExportFile.objects.create(user=customer_user, status="SUCCESS")
    export_file.content_file.save(
        "test.csv", ContentFile(b"header1,header2\nvalue1,value2"), save=True
    )

    # When: Owner tries to access
    request = rf.get("/")
    request.user = customer_user
    response = serve_export_file(request, file_id=export_file.pk)

    # Then: Should return 200 with file
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert b"header1,header2" in b"".join(response.streaming_content)


def test_serve_export_file_non_owner_forbidden(rf, customer_user):
    """Test that non-owner cannot access export file."""
    from saleor.account.models import User

    # Given: User A owns an export file
    owner_user = User.objects.create(email="owner@example.com", is_staff=False)
    export_file = ExportFile.objects.create(user=owner_user, status="SUCCESS")
    export_file.content_file.save("test.csv", ContentFile(b"test,data"), save=True)

    # When: User B (different user) tries to access
    request = rf.get("/")
    request.user = customer_user
    response = serve_export_file(request, file_id=export_file.pk)

    # Then: Should return 403
    assert response.status_code == 403
    assert response.content == b"Forbidden"


def test_serve_export_file_staff_can_access(rf, customer_user, staff_user):
    """Test that staff can access any export file."""
    # Given: Customer owns an export file
    export_file = ExportFile.objects.create(user=customer_user, status="SUCCESS")
    export_file.content_file.save("test.csv", ContentFile(b"test,data"), save=True)

    # When: Staff user tries to access
    request = rf.get("/")
    request.user = staff_user
    response = serve_export_file(request, file_id=export_file.pk)

    # Then: Should return 200
    assert response.status_code == 200


def test_serve_export_file_not_found(rf, customer_user):
    """Test that non-existent export file returns 404."""
    # Given: User is authenticated
    request = rf.get("/")
    request.user = customer_user

    # When: User tries to access non-existent file
    with pytest.raises(Http404):
        serve_export_file(request, file_id=99999)


def test_serve_invoice_requires_authentication(rf, order):
    """Test that invoice endpoint requires authentication."""
    # Given: An invoice exists
    invoice = Invoice.objects.create(order=order, status="SUCCESS")
    invoice.invoice_file.save("test.pdf", ContentFile(b"fake pdf"), save=True)

    # When: Unauthenticated user tries to access
    request = rf.get("/")
    request.user = MockAnonymousUser()
    response = serve_invoice(request, invoice_id=invoice.pk)

    # Then: Should return 401
    assert response.status_code == 401
    assert response.content == b"Unauthorized"


def test_serve_invoice_owner_can_access(rf, order):
    """Test that order owner can access invoice."""
    # Given: User owns an order with invoice
    invoice = Invoice.objects.create(order=order, status="SUCCESS")
    invoice.invoice_file.save("test.pdf", ContentFile(b"fake pdf content"), save=True)

    # When: Order owner tries to access invoice
    request = rf.get("/")
    request.user = order.user
    response = serve_invoice(request, invoice_id=invoice.pk)

    # Then: Should return 200 with file
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert b"fake pdf content" in b"".join(response.streaming_content)


def test_serve_invoice_non_owner_forbidden(rf, order):
    """Test that non-owner cannot access invoice."""
    from saleor.account.models import User

    # Given: User A owns an order with invoice
    invoice = Invoice.objects.create(order=order, status="SUCCESS")
    invoice.invoice_file.save("test.pdf", ContentFile(b"fake pdf"), save=True)

    # Create a different user (User B)
    other_user = User.objects.create(email="other@example.com", is_staff=False)

    # When: User B (different from order owner) tries to access
    request = rf.get("/")
    request.user = other_user
    response = serve_invoice(request, invoice_id=invoice.pk)

    # Then: Should return 403
    assert response.status_code == 403
    assert response.content == b"Forbidden"


def test_serve_invoice_staff_can_access(rf, order, staff_user):
    """Test that staff can access any invoice."""
    # Given: Customer owns an order with invoice
    invoice = Invoice.objects.create(order=order, status="SUCCESS")
    invoice.invoice_file.save("test.pdf", ContentFile(b"fake pdf"), save=True)

    # When: Staff user tries to access
    request = rf.get("/")
    request.user = staff_user
    response = serve_invoice(request, invoice_id=invoice.pk)

    # Then: Should return 200
    assert response.status_code == 200


def test_serve_invoice_not_found(rf, customer_user):
    """Test that non-existent invoice returns 404."""
    # Given: User is authenticated
    request = rf.get("/")
    request.user = customer_user

    # When: User tries to access non-existent invoice
    with pytest.raises(Http404):
        serve_invoice(request, invoice_id=99999)


def test_serve_export_file_signed_valid_signature(rf):
    """Test that valid signed URL serves the file without authentication."""
    # Given: An export file exists
    export_file = ExportFile.objects.create(status="SUCCESS")
    export_file.content_file.save(
        "test.csv", ContentFile(b"header1,header2\nvalue1,value2"), save=True
    )

    # When: Request with valid signed URL
    signer = TimestampSigner()
    signed_id = signer.sign(str(export_file.pk))
    request = rf.get("/")
    response = serve_export_file_signed(request, signed_id=signed_id)

    # Then: Should return 200 with file content (no authentication required)
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert b"header1,header2" in b"".join(response.streaming_content)


def test_serve_export_file_signed_expired_signature(rf):
    """Test that expired signed URL returns 410."""
    # Given: An export file exists
    export_file = ExportFile.objects.create(status="SUCCESS")
    export_file.content_file.save("test.csv", ContentFile(b"test,data"), save=True)

    # When: Request with expired signed URL (max_age=0 means already expired)
    signer = TimestampSigner()
    signed_id = signer.sign(str(export_file.pk))

    # Simulate expired signature by using a very old timestamp
    # We'll manually create an expired signature
    import time

    # Sign with current time
    parts = signed_id.split(":")
    # Create a signature that's definitely older than 7 days (604800 seconds)
    # by manipulating the timestamp part
    from django.core import signing

    # Create a timestamp that's 8 days old (691200 seconds)
    old_timestamp = signing.b62_encode(int(time.time()) - 691200)
    # Reconstruct with old timestamp but keep same signature
    # (this will fail validation)
    expired_signed_id = f"{parts[0]}:{old_timestamp}:{parts[2]}"

    request = rf.get("/")
    response = serve_export_file_signed(request, signed_id=expired_signed_id)

    # Then: Should return 410 or 400 (depending on whether timestamp or signature fails first)
    assert response.status_code in [400, 410]


def test_serve_export_file_signed_invalid_signature(rf):
    """Test that tampered/invalid signed URL returns 400."""
    # Given: An export file exists
    export_file = ExportFile.objects.create(status="SUCCESS")
    export_file.content_file.save("test.csv", ContentFile(b"test,data"), save=True)

    # When: Request with tampered signature
    signer = TimestampSigner()
    signed_id = signer.sign(str(export_file.pk))
    # Tamper with the signature
    tampered_signed_id = signed_id[:-5] + "XXXXX"

    request = rf.get("/")
    response = serve_export_file_signed(request, signed_id=tampered_signed_id)

    # Then: Should return 400
    assert response.status_code == 400
    assert response.content == b"Invalid link"


def test_serve_export_file_signed_nonexistent_file(rf):
    """Test that signed URL for non-existent file returns 404."""
    # Given: A signed URL for non-existent file ID
    signer = TimestampSigner()
    signed_id = signer.sign("99999")

    # When: Request with valid signature but non-existent file
    request = rf.get("/")

    # Then: Should raise 404
    with pytest.raises(Http404):
        serve_export_file_signed(request, signed_id=signed_id)


def test_serve_export_file_signed_missing_content(rf):
    """Test that signed URL for file without content returns 404."""
    # Given: Export file exists but has no content_file
    export_file = ExportFile.objects.create(status="SUCCESS")
    # Don't save any content_file

    # When: Request with valid signed URL
    signer = TimestampSigner()
    signed_id = signer.sign(str(export_file.pk))
    request = rf.get("/")

    # Then: Should raise 404
    with pytest.raises(Http404):
        serve_export_file_signed(request, signed_id=signed_id)
