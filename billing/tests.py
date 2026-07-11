"""
Regression tests for Feature 6 — Revenue Management, Billing & Invoicing.

Covers invoice generation & idempotency, Decimal totals/tax, coupons,
partial/full payment, refund, PDF, numbering, analytics, EMR + appointment
integration, and explicit "existing systems unchanged" checks.
"""

import tempfile
from decimal import Decimal
from datetime import time, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from appointments.models import Appointment, VisitType, WaitlistEntry
from authorization.models import Role, UserRole
from billing.models import (
    Coupon,
    Invoice,
    InvoiceItem,
    MedicalService,
    Payment,
    ServiceCategory,
)
from billing.services import BillingError, BillingService, money
from doctors.models import Doctor, DoctorSchedule, Specialty
from medical_records.models import MedicalVisit

MEDIA = tempfile.mkdtemp()


class Base(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.specialty = Specialty.objects.create(name="Cardiology")
        cls.doctor = Doctor.objects.create(
            name="Dr. Mitchell", specialty=cls.specialty, hospital="St. Mary's",
            fee=Decimal("500.00"),
        )
        for wd in range(7):
            DoctorSchedule.objects.create(
                doctor=cls.doctor, weekday=wd,
                start_time=time(8, 0), end_time=time(20, 0), slot_minutes=30,
            )
        cls.patient = User.objects.create_user(
            email="pat@example.com", password="x", name="Pat"
        )
        cls.other = User.objects.create_user(
            email="other@example.com", password="x", name="Other"
        )
        # RBAC replaces the old is_staff-based "staff" persona: Admin holds
        # every permission except system.admin, matching the previous
        # is_staff=True "can do all staff-level actions" intent.
        cls.staff = User.objects.create_user(
            email="doc@example.com", password="x", name="Doc"
        )
        UserRole.objects.create(user=cls.staff, role=Role.objects.get(name="Admin"))
        # Use non-colliding fixtures (the seed migration already creates
        # "Laboratory" + a "CBC" service in the test DB).
        cls.category = ServiceCategory.objects.create(name="TestLab")
        cls.cbc = MedicalService.objects.create(
            category=cls.category, code="TEST-CBC", name="Test CBC",
            price=Decimal("300.00"), tax_percentage=Decimal("10.00"),
        )
        cls.visit_type = VisitType.objects.create(name="Follow-up", default_duration=20)

    def _appt(self, patient=None, status=Appointment.Status.CONFIRMED, day=None):
        return Appointment.objects.create(
            doctor=self.doctor, patient=patient or self.patient,
            date=day or timezone.localdate(), time=time(10, 0),
            estimated_duration=30, status=status,
        )

    def _complete(self, patient=None):
        appt = self._appt(patient=patient)
        appt.status = Appointment.Status.COMPLETED
        appt.save(update_fields=["status", "updated_at"])
        return Invoice.objects.get(appointment=appt)


# --------------------------------------------------------------------------- #
class InvoiceGenerationTests(Base):
    def test_completion_creates_draft_invoice_with_consultation(self):
        invoice = self._complete()
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertEqual(invoice.items.count(), 1)
        self.assertEqual(invoice.total, Decimal("500.00"))
        self.assertTrue(invoice.invoice_number.startswith("INV-"))

    def test_invoice_creation_is_idempotent(self):
        appt = self._appt()
        appt.status = Appointment.Status.COMPLETED
        appt.save(update_fields=["status", "updated_at"])
        appt.save(update_fields=["status", "updated_at"])
        self.assertEqual(Invoice.objects.filter(appointment=appt).count(), 1)

    def test_invoice_numbering_increments(self):
        i1 = self._complete()
        i2 = self._complete(patient=self.other)
        year = timezone.now().year
        self.assertEqual(i1.invoice_number, f"INV-{year}-000001")
        self.assertEqual(i2.invoice_number, f"INV-{year}-000002")


class TotalsTaxTests(Base):
    def test_tax_and_totals_use_decimal(self):
        invoice = BillingService.create_invoice(patient=self.patient, doctor=self.doctor)
        BillingService.add_service(invoice, self.cbc, quantity=2)
        invoice.refresh_from_db()
        # 2 x 300 = 600 subtotal; 10% tax = 60; total 660
        self.assertEqual(invoice.subtotal, Decimal("600.00"))
        self.assertEqual(invoice.tax, Decimal("60.00"))
        self.assertEqual(invoice.total, Decimal("660.00"))
        self.assertIsInstance(invoice.total, Decimal)


class CouponTests(Base):
    def test_percentage_coupon(self):
        invoice = BillingService.create_invoice(patient=self.patient)
        BillingService.add_item(invoice, "Service", unit_price=Decimal("1000"), quantity=1)
        coupon = Coupon.objects.create(
            code="SAVE10", discount_type=Coupon.DiscountType.PERCENTAGE,
            value=Decimal("10"), minimum_amount=Decimal("100"),
        )
        BillingService.apply_coupon(invoice, "SAVE10")
        invoice.refresh_from_db()
        self.assertEqual(invoice.discount, Decimal("100.00"))
        self.assertEqual(invoice.total, Decimal("900.00"))

    def test_invalid_coupon_raises(self):
        invoice = BillingService.create_invoice(patient=self.patient)
        with self.assertRaises(BillingError):
            BillingService.apply_coupon(invoice, "NOPE")

    def test_minimum_amount_enforced(self):
        invoice = BillingService.create_invoice(patient=self.patient)
        BillingService.add_item(invoice, "Small", unit_price=Decimal("50"), quantity=1)
        Coupon.objects.create(
            code="BIG", discount_type=Coupon.DiscountType.FIXED,
            value=Decimal("20"), minimum_amount=Decimal("500"),
        )
        with self.assertRaises(BillingError):
            BillingService.apply_coupon(invoice, "BIG")


class PaymentRefundTests(Base):
    def test_partial_then_full_payment(self):
        invoice = self._complete()  # total 500
        BillingService.record_payment(invoice, Payment.Method.CASH, Decimal("200"))
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_status, Invoice.PaymentStatus.PARTIAL)
        self.assertEqual(invoice.balance, Decimal("300.00"))
        BillingService.record_payment(invoice, Payment.Method.UPI, Decimal("300"))
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_status, Invoice.PaymentStatus.PAID)
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.balance, Decimal("0.00"))
        self.assertIsNotNone(invoice.paid_at)

    def test_refund(self):
        invoice = self._complete()
        BillingService.record_payment(invoice, Payment.Method.CARD, Decimal("500"))
        BillingService.refund(invoice, Decimal("500"), reason="cancelled")
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.REFUNDED)
        self.assertEqual(invoice.payment_status, Invoice.PaymentStatus.REFUNDED)
        self.assertEqual(invoice.amount_paid, Decimal("0.00"))

    def test_refund_cannot_exceed_paid(self):
        invoice = self._complete()
        BillingService.record_payment(invoice, Payment.Method.CASH, Decimal("100"))
        with self.assertRaises(BillingError):
            BillingService.refund(invoice, Decimal("500"))


class PdfTests(Base):
    def test_pdf_bytes(self):
        invoice = self._complete()
        pdf = BillingService.generate_pdf(invoice)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 800)

    def test_pdf_endpoint(self):
        invoice = self._complete()
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("invoice-pdf", args=[invoice.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")


class ApiTests(Base):
    def test_my_invoices_and_detail(self):
        invoice = self._complete()
        self.client.force_authenticate(self.patient)
        lst = self.client.get(reverse("my-invoices"))
        self.assertEqual(len(lst.data["results"]), 1)
        det = self.client.get(reverse("invoice-detail", args=[invoice.id]))
        self.assertEqual(det.status_code, 200)
        self.assertEqual(det.data["invoice_number"], invoice.invoice_number)

    def test_cannot_view_others_invoice(self):
        invoice = self._complete()
        self.client.force_authenticate(self.other)
        self.assertEqual(
            self.client.get(reverse("invoice-detail", args=[invoice.id])).status_code,
            404,
        )

    def test_patient_cannot_record_payment(self):
        invoice = self._complete()
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("invoice-payments", args=[invoice.id]),
            {"method": "cash", "amount": "500"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_status, Invoice.PaymentStatus.UNPAID)

    def test_anonymous_user_cannot_record_payment(self):
        invoice = self._complete()
        resp = self.client.post(
            reverse("invoice-payments", args=[invoice.id]),
            {"method": "cash", "amount": "500"}, format="json",
        )
        # Genuinely anonymous (no force_authenticate): JWTAuthentication
        # finds no credentials, so request.successful_authenticator stays
        # None and DRF's APIView.permission_denied() raises NotAuthenticated
        # (401), not PermissionDenied (403) — regardless of which
        # permission class rejected the request.
        self.assertEqual(resp.status_code, 401)

    def test_staff_can_record_payment_and_receipt_is_generated(self):
        invoice = self._complete()
        self.client.force_authenticate(self.staff)
        resp = self.client.post(
            reverse("invoice-payments", args=[invoice.id]),
            {"method": "cash", "amount": "500"}, format="json",
        )
        self.assertEqual(resp.status_code, 201)
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_status, Invoice.PaymentStatus.PAID)
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertIn("receipt", resp.data)
        self.assertTrue(resp.data["receipt"]["receipt_number"].startswith("RCPT-"))

    def test_add_item_staff_only(self):
        invoice = self._complete()
        self.client.force_authenticate(self.patient)
        r = self.client.post(
            reverse("invoice-items", args=[invoice.id]),
            {"service": self.cbc.id}, format="json",
        )
        self.assertEqual(r.status_code, 403)
        self.client.force_authenticate(self.staff)
        r2 = self.client.post(
            reverse("invoice-items", args=[invoice.id]),
            {"service": self.cbc.id}, format="json",
        )
        self.assertEqual(r2.status_code, 201)


class AnalyticsTests(Base):
    def test_analytics_staff_only(self):
        self.client.force_authenticate(self.patient)
        self.assertEqual(
            self.client.get(reverse("billing-analytics")).status_code, 403
        )

    def test_analytics_payload(self):
        invoice = self._complete()
        BillingService.record_payment(invoice, Payment.Method.CASH, Decimal("500"))
        self.client.force_authenticate(self.staff)
        resp = self.client.get(reverse("billing-analytics"))
        self.assertEqual(resp.status_code, 200)
        for key in (
            "today_revenue", "weekly_revenue", "monthly_revenue",
            "pending_payments", "refunds", "collection_rate", "average_invoice",
        ):
            self.assertIn(key, resp.data)
        self.assertEqual(resp.data["today_revenue"], 500.0)


@override_settings(MEDIA_ROOT=MEDIA)
class EmrIntegrationTests(Base):
    def test_lab_report_adds_invoice_item(self):
        invoice = self._complete()  # draft invoice for the appointment
        visit = MedicalVisit.objects.get(appointment=invoice.appointment)
        before = invoice.items.count()
        self.client.force_authenticate(self.patient)
        upload = SimpleUploadedFile("r.pdf", b"%PDF-1.4 x", content_type="application/pdf")
        resp = self.client.post(
            reverse("record-visit-reports", args=[visit.id]),
            {"title": "CBC", "file": upload}, format="multipart",
        )
        self.assertEqual(resp.status_code, 201)
        invoice.refresh_from_db()
        self.assertEqual(invoice.items.count(), before + 1)


# --------------------------------------------------------------------------- #
class ExistingSystemsUnchangedTests(Base):
    def test_booking_unchanged(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("appointments"),
            {
                "doctor": self.doctor.id,
                "date": (timezone.localdate() + timedelta(days=1)).isoformat(),
                "time": "11:00", "reason": "Checkup",
            }, format="json",
        )
        self.assertEqual(resp.status_code, 201)

    def test_queue_checkin_unchanged(self):
        appt = self._appt()
        self.client.force_authenticate(self.patient)
        self.assertEqual(
            self.client.post(reverse("appointment-check-in", args=[appt.id])).status_code,
            200,
        )

    def test_attendance_confirm_unchanged(self):
        appt = self._appt(day=timezone.localdate() + timedelta(days=1))
        self.client.force_authenticate(self.patient)
        resp = self.client.post(reverse("appointment-confirm", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("risk_level", resp.data)

    def test_emr_records_me_unchanged(self):
        self.client.force_authenticate(self.patient)
        self.assertEqual(self.client.get(reverse("record-me")).status_code, 200)

    def test_waitlist_unchanged(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("waitlist"),
            {
                "doctor": self.doctor.id,
                "date": (timezone.localdate() + timedelta(days=2)).isoformat(),
            }, format="json",
        )
        self.assertEqual(resp.status_code, 201)
