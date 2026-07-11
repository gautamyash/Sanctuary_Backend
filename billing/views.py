"""
Billing endpoints (Feature 6). Additive and independent of booking, queue,
attendance, waitlist, and EMR logic.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from authorization.permissions import PermissionRequired
from authorization.services import PermissionService

from .models import Invoice, InvoiceItem, MedicalService, Payment, Refund
from .serializers import (
    InvoiceSerializer,
    MedicalServiceSerializer,
    PaymentSerializer,
    RefundSerializer,
)
from .services import BillingError, BillingService

ZERO = Decimal("0.00")


def _invoice_for(pk, user, permission_code=None):
    """Invoices `user` may access: any invoice if they hold
    `permission_code` under the RBAC system (replaces the previous
    is_staff-wide check, same "any invoice" behavior), otherwise only
    their own (unchanged patient ownership check)."""
    qs = Invoice.objects.select_related("doctor", "patient", "coupon").prefetch_related(
        "items", "payments", "refunds"
    )
    if permission_code and PermissionService.has_permission(user, permission_code):
        return qs.filter(pk=pk).first()
    return qs.filter(pk=pk, patient=user).first()


def _serialize(invoice, request):
    return InvoiceSerializer(invoice, context={"request": request}).data


class MyInvoicesView(APIView):
    """GET /api/billing/my-invoices/ — the caller's invoices (search supported)."""

    def get(self, request):
        qs = (
            Invoice.objects.filter(patient=request.user)
            .select_related("doctor")
            .prefetch_related("items", "payments", "refunds")
        )
        p = request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("payment_status"):
            qs = qs.filter(payment_status=p["payment_status"])
        if p.get("q"):
            qs = qs.filter(invoice_number__icontains=p["q"])
        return Response(
            {"results": InvoiceSerializer(qs, many=True, context={"request": request}).data}
        )


class InvoiceDetailView(APIView):
    """GET /api/billing/invoices/{id}/"""

    def get(self, request, pk):
        invoice = _invoice_for(pk, request.user, "billing.view")
        if invoice is None:
            return Response({"detail": "Not found."}, status=404)
        return Response(_serialize(invoice, request))


class AddItemView(APIView):
    """POST /api/billing/invoices/{id}/items/ — staff add a service or line item."""

    permission_classes = [PermissionRequired]
    permission_code = "billing.edit"

    def post(self, request, pk):
        invoice = get_object_or_404(Invoice, pk=pk)
        service_id = request.data.get("service")
        quantity = int(request.data.get("quantity", 1) or 1)
        try:
            if service_id:
                service = get_object_or_404(MedicalService, pk=service_id, active=True)
                BillingService.add_service(invoice, service, quantity=quantity)
            else:
                description = request.data.get("description")
                if not description:
                    return Response(
                        {"detail": "description or service is required."}, status=400
                    )
                BillingService.add_item(
                    invoice,
                    description=description,
                    unit_price=request.data.get("unit_price", 0),
                    quantity=quantity,
                    discount=request.data.get("discount", 0),
                    tax_percentage=request.data.get("tax_percentage", 0),
                )
        except BillingError as e:
            return Response({"detail": str(e)}, status=400)
        invoice.refresh_from_db()
        return Response(_serialize(invoice, request), status=status.HTTP_201_CREATED)


class RecordPaymentView(APIView):
    """POST /api/billing/invoices/{id}/payments/ — staff record a payment."""

    permission_classes = [PermissionRequired]
    permission_code = "billing.payment"

    def post(self, request, pk):
        invoice = _invoice_for(pk, request.user, "billing.payment")
        if invoice is None:
            return Response({"detail": "Not found."}, status=404)
        method = request.data.get("method")
        if method not in Payment.Method.values:
            return Response({"detail": "Invalid payment method."}, status=400)
        try:
            payment = BillingService.record_payment(
                invoice,
                method=method,
                amount=request.data.get("amount", 0),
                received_by=request.user,
                reference=request.data.get("reference", ""),
                notes=request.data.get("notes", ""),
            )
        except BillingError as e:
            return Response({"detail": str(e)}, status=400)
        invoice.refresh_from_db()
        return Response(
            {
                "payment": PaymentSerializer(payment).data,
                "receipt": BillingService.generate_receipt(payment),
                "invoice": _serialize(invoice, request),
            },
            status=status.HTTP_201_CREATED,
        )


class ApplyCouponView(APIView):
    """POST /api/billing/invoices/{id}/coupon/ — owner or staff apply a coupon."""

    def post(self, request, pk):
        invoice = _invoice_for(pk, request.user, "billing.edit")
        if invoice is None:
            return Response({"detail": "Not found."}, status=404)
        code = request.data.get("code", "")
        try:
            BillingService.apply_coupon(invoice, code)
        except BillingError as e:
            return Response({"detail": str(e)}, status=400)
        invoice.refresh_from_db()
        return Response(_serialize(invoice, request))


class RefundView(APIView):
    """POST /api/billing/invoices/{id}/refund/ — staff process a refund."""

    permission_classes = [PermissionRequired]
    permission_code = "billing.refund"

    def post(self, request, pk):
        invoice = get_object_or_404(Invoice, pk=pk)
        try:
            refund = BillingService.refund(
                invoice,
                amount=request.data.get("amount", 0),
                reason=request.data.get("reason", ""),
                processed_by=request.user,
            )
        except BillingError as e:
            return Response({"detail": str(e)}, status=400)
        invoice.refresh_from_db()
        return Response(
            {
                "refund": RefundSerializer(refund).data,
                "invoice": _serialize(invoice, request),
            },
            status=status.HTTP_201_CREATED,
        )


class InvoicePdfView(APIView):
    """GET /api/billing/invoices/{id}/pdf/ — professional PDF invoice."""

    def get(self, request, pk):
        invoice = _invoice_for(pk, request.user, "billing.view")
        if invoice is None:
            return Response({"detail": "Not found."}, status=404)
        pdf = BillingService.generate_pdf(invoice)
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = (
            f'inline; filename="{invoice.invoice_number}.pdf"'
        )
        return resp


class ServiceCatalogView(APIView):
    """GET /api/billing/services/ — active service catalog."""

    def get(self, request):
        services = MedicalService.objects.filter(active=True).select_related("category")
        return Response(MedicalServiceSerializer(services, many=True).data)


class BillingAnalyticsView(APIView):
    """GET /api/billing/analytics/ — staff-only revenue metrics."""

    permission_classes = [PermissionRequired]
    permission_code = "billing.analytics"

    def get(self, request):
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)

        def collected(since=None):
            qs = Payment.objects.filter(status=Payment.Status.SUCCESS)
            if since:
                qs = qs.filter(paid_at__date__gte=since)
            return qs.aggregate(s=Sum("amount"))["s"] or ZERO

        today_revenue = Payment.objects.filter(
            status=Payment.Status.SUCCESS, paid_at__date=today
        ).aggregate(s=Sum("amount"))["s"] or ZERO
        weekly_revenue = collected(week_start)
        monthly_revenue = collected(month_start)

        pending_payments = (
            Invoice.objects.exclude(
                status__in=[Invoice.Status.DRAFT, Invoice.Status.CANCELLED]
            )
            .filter(
                payment_status__in=[
                    Invoice.PaymentStatus.UNPAID,
                    Invoice.PaymentStatus.PARTIAL,
                ]
            )
            .aggregate(s=Sum("balance"))["s"]
            or ZERO
        )
        refunds = Refund.objects.aggregate(s=Sum("amount"))["s"] or ZERO

        billed = (
            Invoice.objects.exclude(
                status__in=[Invoice.Status.DRAFT, Invoice.Status.CANCELLED]
            ).aggregate(s=Sum("total"))["s"]
            or ZERO
        )
        net_collected = (collected() - refunds)
        collection_rate = (
            float(round(net_collected / billed * 100, 1)) if billed > ZERO else 0.0
        )
        average_invoice = (
            Invoice.objects.exclude(
                status__in=[Invoice.Status.DRAFT, Invoice.Status.CANCELLED]
            ).aggregate(a=Avg("total"))["a"]
            or ZERO
        )

        # Chart aids (additive): payment methods + top services.
        by_method = list(
            Payment.objects.filter(status=Payment.Status.SUCCESS)
            .values("method")
            .annotate(total=Sum("amount"))
            .order_by("-total")
        )
        top_services = list(
            InvoiceItem.objects.values("description")
            .annotate(total=Sum("total"))
            .order_by("-total")[:5]
        )

        return Response(
            {
                "today_revenue": float(today_revenue),
                "weekly_revenue": float(weekly_revenue),
                "monthly_revenue": float(monthly_revenue),
                "pending_payments": float(pending_payments),
                "refunds": float(refunds),
                "collection_rate": collection_rate,
                "average_invoice": float(round(average_invoice, 2)),
                "payment_methods": [
                    {"method": m["method"], "total": float(m["total"] or 0)}
                    for m in by_method
                ],
                "top_services": [
                    {"description": s["description"], "total": float(s["total"] or 0)}
                    for s in top_services
                ],
            }
        )
