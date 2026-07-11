"""
Auto-create a MedicalVisit when an appointment is completed (Feature 5).

Wired through a defensive post_save receiver so it never interferes with the
completion flow in the appointments app. Idempotent: only one visit per
appointment.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from appointments.analytics import track
from appointments.models import Appointment

from .models import MedicalVisit

logger = logging.getLogger("sanctuary.medical_records")


@receiver(
    post_save, sender=Appointment, dispatch_uid="medical_records_auto_visit"
)
def create_visit_on_completion(sender, instance, created, update_fields=None, **kwargs):
    if created:
        return
    fields = set(update_fields) if update_fields else None
    if fields is not None and "status" not in fields:
        return
    if instance.status != Appointment.Status.COMPLETED:
        return
    try:
        _, made = MedicalVisit.objects.get_or_create(
            appointment=instance,
            defaults={"doctor": instance.doctor, "patient": instance.patient},
        )
        if made:
            track("medical_visit_created", appointment=instance.id)
    except Exception:  # noqa: BLE001 — never break appointment completion
        logger.exception(
            "medical visit auto-creation failed for appointment %s", instance.pk
        )
