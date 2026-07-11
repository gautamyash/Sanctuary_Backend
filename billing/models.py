"""
Revenue Management, Billing & Invoicing models — Feature 6.

An independent app. It reacts to appointment/EMR events but never controls
them. All monetary values use DecimalField (never floating point).
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

MONEY = dict(max_digits=10, decimal_places=2, default=Decimal("0.00"))
PERCENT = dict(max_digits=5, decimal_places=2, default=Decimal("0.00"))


class ServiceCategory(models.Model):
    name = models.CharField(max_length=80, unique=True)
    description = models.CharField(max_length=255, blank=True, default="")
    color = models.CharField(max_length=9, default="#003d9b")
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "service categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class MedicalService(models.Model):
    category = models.ForeignKey(
        ServiceCategory, on_delete=models.PROTECT, related_name="services"
    )
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=160)
    description = models.CharField(max_length=255, blank=True, default="")
    price = models.DecimalField(**MONEY)
    duration = models.PositiveIntegerField(default=0, help_text="Minutes")
    tax_percentage = models.DecimalField(**PERCENT)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category__name", "name"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        PENDING = "pending"
        PAID = "paid"
        CANCELLED = "cancelled"
        REFUNDED = "refunded"

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid"
        PARTIAL = "partial"
        PAID = "paid"
        REFUNDED = "refunded"

    invoice_number = models.CharField(max_length=24, unique=True)
    appointment = models.OneToOneField(
        "appointments.Appointment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoice",
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="invoices"
    )
    doctor = models.ForeignKey(
        "doctors.Doctor",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoices",
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    subtotal = models.DecimalField(**MONEY)
    discount = models.DecimalField(**MONEY)
    tax = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)
    amount_paid = models.DecimalField(**MONEY)
    balance = models.DecimalField(**MONEY)
    payment_status = models.CharField(
        max_length=10,
        choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
    )
    coupon = models.ForeignKey(
        "billing.Coupon",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoices",
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-issued_at"]
        indexes = [
            models.Index(fields=["patient", "status"]),
            models.Index(fields=["payment_status"]),
            models.Index(fields=["invoice_number"]),
        ]

    def __str__(self):
        return self.invoice_number


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="items"
    )
    service = models.ForeignKey(
        MedicalService,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoice_items",
    )
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(**MONEY)
    discount = models.DecimalField(**MONEY)
    tax = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.description} x{self.quantity}"


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash"
        UPI = "upi"
        CARD = "card"
        NET_BANKING = "net_banking"
        INSURANCE = "insurance"
        WALLET = "wallet"
        CHEQUE = "cheque"

    class Status(models.TextChoices):
        PENDING = "pending"
        SUCCESS = "success"
        FAILED = "failed"
        REFUNDED = "refunded"

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="payments"
    )
    method = models.CharField(max_length=12, choices=Method.choices)
    reference = models.CharField(max_length=120, blank=True, default="")
    amount = models.DecimalField(**MONEY)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.SUCCESS
    )
    paid_at = models.DateTimeField(auto_now_add=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="received_payments",
    )
    notes = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.amount} via {self.method} ({self.invoice.invoice_number})"


class Refund(models.Model):
    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="refunds"
    )
    payment = models.ForeignKey(
        Payment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="refunds",
    )
    amount = models.DecimalField(**MONEY)
    reason = models.CharField(max_length=255, blank=True, default="")
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="processed_refunds",
    )
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-processed_at"]

    def __str__(self):
        return f"Refund {self.amount} ({self.invoice.invoice_number})"


class Coupon(models.Model):
    class DiscountType(models.TextChoices):
        PERCENTAGE = "percentage"
        FIXED = "fixed"

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120, blank=True, default="")
    discount_type = models.CharField(
        max_length=10, choices=DiscountType.choices, default=DiscountType.PERCENTAGE
    )
    value = models.DecimalField(**MONEY)
    minimum_amount = models.DecimalField(**MONEY)
    expiry = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.code


class InsuranceProvider(models.Model):
    name = models.CharField(max_length=120, unique=True)
    contact = models.CharField(max_length=120, blank=True, default="")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class InsuranceClaim(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "submitted"
        APPROVED = "approved"
        REJECTED = "rejected"

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="claims"
    )
    provider = models.ForeignKey(
        InsuranceProvider, on_delete=models.PROTECT, related_name="claims"
    )
    claim_number = models.CharField(max_length=48, blank=True, default="")
    approved_amount = models.DecimalField(**MONEY)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.SUBMITTED
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Claim {self.claim_number or self.id} ({self.provider})"
