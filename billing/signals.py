"""
Billing reacts to appointment completion and EMR events (Feature 6).

All handlers are defensive and never raise into the flow that triggered them,
and invoice creation is idempotent per appointment.
"""

import logging

from django.db import IntegrityError, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from appointments.models import Appointment
from medical_records.models import LabReport

from .models import Invoice, MedicalService
from .services import BillingService

logger = logging.getLogger("sanctuary.billing")


@receiver(post_save, sender=Appointment, dispatch_uid="billing_auto_invoice")
def create_invoice_on_completion(sender, instance, created, update_fields=None, **kwargs):
    if created:
        return
    fields = set(update_fields) if update_fields else None
    if fields is not None and "status" not in fields:
        return
    if instance.status != Appointment.Status.COMPLETED:
        return
    try:
        # Idempotent: one invoice per appointment.
        if Invoice.objects.filter(appointment=instance).exists():
            return
        with transaction.atomic():
            invoice = BillingService.create_invoice(
                patient=instance.patient,
                doctor=instance.doctor,
                appointment=instance,
            )
            fee = getattr(instance.doctor, "fee", 0) or 0
            BillingService.add_item(
                invoice,
                description=f"Consultation — {instance.doctor.name}",
                unit_price=fee,
                quantity=1,
            )
    except IntegrityError:
        # Lost a race to create the invoice — the other creation stands.
        pass
    except Exception:  # noqa: BLE001 — never break appointment completion
        logger.exception("auto-invoice failed for appointment %s", instance.pk)


@receiver(post_save, sender=LabReport, dispatch_uid="billing_labreport_item")
def add_lab_report_item(sender, instance, created, **kwargs):
    """When a lab report is uploaded, add a billable item to the appointment's
    invoice (if one exists). Priced from a matching service, else left at 0 for
    reception to price."""
    if not created:
        return
    try:
        appointment = instance.medical_visit.appointment
        invoice = Invoice.objects.filter(appointment=appointment).first()
        if invoice is None:
            return
        service = MedicalService.objects.filter(
            name__iexact=instance.title, active=True
        ).first()
        if service is not None:
            BillingService.add_service(invoice, service)
        else:
            BillingService.add_item(
                invoice,
                description=f"Lab report: {instance.title}",
                unit_price=0,
                quantity=1,
            )
    except Exception:  # noqa: BLE001 — never break EMR uploads
        logger.exception("lab-report billing item failed for report %s", instance.pk)
