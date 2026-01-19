from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.views.decorators.http import require_http_methods

from saleor.csv.models import ExportFile
from saleor.invoice.models import Invoice


class _AnonymousUser:
    """Fallback for unauthenticated requests."""

    is_authenticated = False
    is_staff = False


@require_http_methods(["GET", "HEAD"])
def serve_export_file(request, file_id):
    """Serve export file with authentication - user must own it or be staff."""
    try:
        export_file = ExportFile.objects.using(
            settings.DATABASE_CONNECTION_REPLICA_NAME
        ).get(pk=file_id)
    except ExportFile.DoesNotExist:
        raise Http404("Export file not found") from None

    # Check permission: must be the owner or staff
    user = getattr(request, "user", _AnonymousUser())

    if not user.is_authenticated:
        return HttpResponse("Unauthorized", status=401)

    is_owner = export_file.user and export_file.user == user
    is_staff = user.is_staff

    if not (is_owner or is_staff):
        return HttpResponse("Forbidden", status=403)

    if not export_file.content_file:
        raise Http404("File not available")

    response = FileResponse(
        export_file.content_file.open("rb"),
        as_attachment=True,
    )

    # Set content type
    response["Content-Type"] = "text/csv"

    return response


@require_http_methods(["GET", "HEAD"])
def serve_invoice(request, invoice_id):
    """Serve invoice with authentication - user must own the order or be staff."""
    try:
        invoice = (
            Invoice.objects.using(settings.DATABASE_CONNECTION_REPLICA_NAME)
            .select_related("order", "order__user")
            .get(pk=invoice_id)
        )
    except Invoice.DoesNotExist:
        raise Http404("Invoice not found") from None

    # Check permission: must own the order or be staff
    user = getattr(request, "user", _AnonymousUser())

    if not user.is_authenticated:
        return HttpResponse("Unauthorized", status=401)

    is_order_owner = invoice.order and invoice.order.user and invoice.order.user == user
    is_staff = user.is_staff

    if not (is_order_owner or is_staff):
        return HttpResponse("Forbidden", status=403)

    if not invoice.invoice_file:
        raise Http404("File not available")

    response = FileResponse(
        invoice.invoice_file.open("rb"),
        as_attachment=True,
    )

    # Set content type
    response["Content-Type"] = "application/pdf"

    return response
