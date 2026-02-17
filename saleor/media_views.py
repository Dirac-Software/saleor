import logging

from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.http import FileResponse, Http404, HttpResponse
from django.views.decorators.http import require_http_methods

from saleor.csv.models import ExportFile
from saleor.invoice.models import Invoice
from saleor.product.models import PriceList

PRICE_LIST_SIGNED_URL_MAX_AGE = 604800  # 7 days

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "HEAD"])
def serve_export_file(request, file_id):
    """Serve export file with authentication - user must own it or be staff."""
    try:
        export_file = ExportFile.objects.using(
            settings.DATABASE_CONNECTION_REPLICA_NAME
        ).get(pk=file_id)
    except ExportFile.DoesNotExist:
        raise Http404("Export file not found") from None

    user = getattr(request, "user", None)

    if user is None or not user.is_authenticated:
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
def serve_export_file_signed(request, signed_id):
    """Serve export file with signed URL - no authentication required."""
    signer = TimestampSigner()

    try:
        # Validate signature and check expiration (7 days = 604800 seconds)
        file_id = signer.unsign(signed_id, max_age=604800)
    except SignatureExpired:
        return HttpResponse("Link expired", status=410)
    except BadSignature:
        return HttpResponse("Invalid link", status=400)

    try:
        export_file = ExportFile.objects.using(
            settings.DATABASE_CONNECTION_REPLICA_NAME
        ).get(pk=file_id)
    except ExportFile.DoesNotExist:
        raise Http404("Export file not found") from None

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

    user = getattr(request, "user", None)

    if user is None or not user.is_authenticated:
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


@require_http_methods(["GET", "HEAD"])
def serve_price_list_signed(request, pk, signed_id):
    signer = TimestampSigner()
    try:
        price_list_id = signer.unsign(signed_id, max_age=PRICE_LIST_SIGNED_URL_MAX_AGE)
    except SignatureExpired:
        logger.debug("serve_price_list_signed: link expired for signed_id=%s", signed_id)
        return HttpResponse("Link expired", status=410)
    except BadSignature:
        logger.debug("serve_price_list_signed: invalid signature for signed_id=%s", signed_id)
        return HttpResponse("Invalid link", status=400)

    logger.debug("serve_price_list_signed: resolved price_list_id=%s", price_list_id)

    try:
        price_list = PriceList.objects.using(
            settings.DATABASE_CONNECTION_REPLICA_NAME
        ).get(pk=price_list_id)
    except PriceList.DoesNotExist:
        raise Http404("Price list not found") from None

    if not price_list.excel_file:
        raise Http404("File not available")

    filename = price_list.excel_file.name.split("/")[-1]
    logger.debug("serve_price_list_signed: serving file %s", filename)
    response = FileResponse(
        price_list.excel_file.open("rb"),
        as_attachment=True,
        filename=filename,
    )
    response["Content-Type"] = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return response
