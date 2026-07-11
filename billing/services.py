"""
BillingService — the revenue engine (Feature 6).

All monetary math uses Decimal with 2-place, half-up rounding. Invoice
creation and payment recording run in transactions. The engine reacts to
appointment/EMR events but never controls them.
"""

from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from appointments.analytics import track
from appointments.notifications import NotificationService

from .models import Coupon, Invoice, InvoiceItem, Payment, Refund

TWO = Decimal("0.01")
ZERO = Decimal("0.00")


class BillingError(Exception):
    """Domain error (invalid coupon, bad amount, ...) -> HTTP 400."""


def money(value) -> Decimal:
    return Decimal(value).quantize(TWO, rounding=ROUND_HALF_UP)


class BillingService:
    # ------------------------------------------------------------------ #
    # Invoice numbering
    # ------------------------------------------------------------------ #
    @staticmethod
    def _next_invoice_number(year: int) -> str:
        prefix = f"INV-{year}-"
        last = (
            Invoice.objects.filter(invoice_number__startswith=prefix)
            .order_by("-invoice_number")
            .first()
        )
        seq = int(last.invoice_number.rsplit("-", 1)[-1]) + 1 if last else 1
        return f"{prefix}{seq:06d}"

    # ------------------------------------------------------------------ #
    # Creation & line items
    # ------------------------------------------------------------------ #
    @staticmethod
    @transaction.atomic
    def create_invoice(patient, doctor=None, appointment=None, notes="",
                       status=Invoice.Status.DRAFT):
        number = BillingService._next_invoice_number(timezone.now().year)
        invoice = Invoice.objects.create(
            invoice_number=number,
            patient=patient,
            doctor=doctor,
            appointment=appointment,
            notes=notes,
            status=status,
        )
        track("invoice_created", invoice=invoice.id, number=invoice.invoice_number)
        NotificationService.send_invoice_generated(invoice)
        return invoice

    @staticmethod
    def add_item(invoice, description, unit_price, quantity=1, discount=ZERO,
                 tax_percentage=ZERO, service=None) -> InvoiceItem:
        qty = int(quantity)
        unit = money(unit_price)
        disc = money(discount)
        taxable = unit * qty - disc
        if taxable < ZERO:
            taxable = ZERO
        tax = money(taxable * Decimal(tax_percentage) / Decimal(100))
        total = taxable + tax
        item = InvoiceItem.objects.create(
            invoice=invoice,
            service=service,
            description=description,
            quantity=qty,
            unit_price=unit,
            discount=disc,
            tax=tax,
            total=money(total),
        )
        track("service_added", invoice=invoice.id, item=item.id)
        BillingService.calculate_totals(invoice)
        return item

    @staticmethod
    def add_service(invoice, service, quantity=1, discount=ZERO) -> InvoiceItem:
        return BillingService.add_item(
            invoice,
            description=service.name,
            unit_price=service.price,
            quantity=quantity,
            discount=discount,
            tax_percentage=service.tax_percentage,
            service=service,
        )

    @staticmethod
    def remove_item(invoice, item_id) -> None:
        InvoiceItem.objects.filter(invoice=invoice, pk=item_id).delete()
        BillingService.calculate_totals(invoice)

    # ------------------------------------------------------------------ #
    # Totals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _coupon_discount(invoice, base: Decimal) -> Decimal:
        coupon = invoice.coupon
        if not coupon or not coupon.active:
            return ZERO
        if coupon.expiry and coupon.expiry < timezone.localdate():
            return ZERO
        if base < coupon.minimum_amount:
            return ZERO
        if coupon.discount_type == Coupon.DiscountType.PERCENTAGE:
            disc = base * coupon.value / Decimal(100)
        else:
            disc = coupon.value
        return money(min(disc, base))

    @staticmethod
    def calculate_totals(invoice) -> Invoice:
        items = list(invoice.items.all())
        subtotal = sum((i.unit_price * i.quantity for i in items), ZERO)
        item_discount = sum((i.discount for i in items), ZERO)
        tax = sum((i.tax for i in items), ZERO)
        taxed_total = subtotal - item_discount + tax  # == sum(item.total)

        coupon_discount = BillingService._coupon_discount(
            invoice, subtotal - item_discount
        )
        total = taxed_total - coupon_discount
        if total < ZERO:
            total = ZERO

        paid = sum(
            (p.amount for p in invoice.payments.filter(
                status=Payment.Status.SUCCESS)),
            ZERO,
        )
        refunded = sum((r.amount for r in invoice.refunds.all()), ZERO)
        net_paid = paid - refunded
        balance = total - net_paid

        invoice.subtotal = money(subtotal)
        invoice.discount = money(item_discount + coupon_discount)
        invoice.tax = money(tax)
        invoice.total = money(total)
        invoice.amount_paid = money(net_paid)
        invoice.balance = money(balance)

        if refunded > ZERO and net_paid <= ZERO:
            invoice.payment_status = Invoice.PaymentStatus.REFUNDED
        elif net_paid <= ZERO:
            invoice.payment_status = Invoice.PaymentStatus.UNPAID
        elif balance > ZERO:
            invoice.payment_status = Invoice.PaymentStatus.PARTIAL
        else:
            invoice.payment_status = Invoice.PaymentStatus.PAID

        if invoice.status not in (
            Invoice.Status.CANCELLED,
            Invoice.Status.REFUNDED,
        ):
            if invoice.payment_status == Invoice.PaymentStatus.PAID:
                invoice.status = Invoice.Status.PAID
                invoice.paid_at = invoice.paid_at or timezone.now()
            elif net_paid > ZERO or items:
                if invoice.status == Invoice.Status.DRAFT and net_paid > ZERO:
                    invoice.status = Invoice.Status.PENDING
        invoice.save()
        return invoice

    # ------------------------------------------------------------------ #
    # Coupons, payments, refunds
    # ------------------------------------------------------------------ #
    @staticmethod
    def apply_coupon(invoice, code: str) -> Invoice:
        try:
            coupon = Coupon.objects.get(code__iexact=code, active=True)
        except Coupon.DoesNotExist:
            raise BillingError("Invalid or inactive coupon.")
        if coupon.expiry and coupon.expiry < timezone.localdate():
            raise BillingError("This coupon has expired.")
        base = invoice.subtotal - sum(
            (i.discount for i in invoice.items.all()), ZERO
        )
        if base < coupon.minimum_amount:
            raise BillingError(
                f"Minimum invoice amount for this coupon is {coupon.minimum_amount}."
            )
        invoice.coupon = coupon
        invoice.save(update_fields=["coupon"])
        BillingService.calculate_totals(invoice)
        track("coupon_used", invoice=invoice.id, coupon=coupon.code)
        return invoice

    @staticmethod
    @transaction.atomic
    def record_payment(invoice, method, amount, received_by=None,
                       reference="", notes="") -> Payment:
        amount = money(amount)
        if amount <= ZERO:
            raise BillingError("Payment amount must be positive.")
        locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
        payment = Payment.objects.create(
            invoice=locked,
            method=method,
            amount=amount,
            reference=reference,
            notes=notes,
            received_by=received_by,
            status=Payment.Status.SUCCESS,
        )
        BillingService.calculate_totals(locked)
        track(
            "payment_received",
            invoice=locked.id,
            payment=payment.id,
            amount=str(amount),
            method=method,
        )
        NotificationService.send_payment_received(payment)
        if locked.payment_status == Invoice.PaymentStatus.PAID:
            track("invoice_paid", invoice=locked.id)
        return payment

    @staticmethod
    @transaction.atomic
    def refund(invoice, amount, reason="", processed_by=None, payment=None) -> Refund:
        amount = money(amount)
        if amount <= ZERO:
            raise BillingError("Refund amount must be positive.")
        # Refresh so amount_paid reflects any payments recorded on a locked copy.
        invoice.refresh_from_db()
        if amount > invoice.amount_paid:
            raise BillingError("Refund exceeds the amount paid.")
        refund = Refund.objects.create(
            invoice=invoice,
            payment=payment,
            amount=amount,
            reason=reason,
            processed_by=processed_by,
        )
        if payment is not None:
            payment.status = Payment.Status.REFUNDED
            payment.save(update_fields=["status"])
        BillingService.calculate_totals(invoice)
        if invoice.amount_paid <= ZERO:
            invoice.status = Invoice.Status.REFUNDED
            invoice.payment_status = Invoice.PaymentStatus.REFUNDED
            invoice.save(update_fields=["status", "payment_status"])
        track("refund_processed", invoice=invoice.id, amount=str(amount))
        NotificationService.send_refund_processed(refund)
        return refund

    @staticmethod
    def generate_receipt(payment) -> dict:
        return {
            "receipt_number": f"RCPT-{payment.id:06d}",
            "invoice_number": payment.invoice.invoice_number,
            "amount": str(payment.amount),
            "method": payment.method,
            "paid_at": payment.paid_at.isoformat(),
            "balance": str(payment.invoice.balance),
        }

    # ------------------------------------------------------------------ #
    # PDF (ReportLab)
    # ------------------------------------------------------------------ #
    @staticmethod
    def generate_pdf(invoice) -> bytes:
        from .pdf import render_invoice_pdf

        return render_invoice_pdf(invoice)
