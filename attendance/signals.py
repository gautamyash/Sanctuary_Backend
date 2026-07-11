"""
Auto-prediction signal wiring (Feature 4).

The attendance layer observes the Appointment lifecycle through a single
post_save receiver instead of editing any booking/scheduling/queue view. Every
handler body is fully defensive: it can never raise into the code path that
triggered the save, so booking, cancellation, check-in and completion behave
exactly as before even if prediction fails.

Triggers covered (per spec): appointment created, rescheduled, patient
confirms, patient cancels, patient checks in — plus completion for the
learning engine.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from appointments.models import Appointment

from . import interventions
from .models import RiskLevel
from .services import NoShowPredictionService

logger = logging.getLogger("sanctuary.attendance")

# Lifecycle fields whose change should recompute risk. Saves that touch only
# queue_position / consultation timestamps are ignored so the queue layer's
# frequent snapshots don't churn predictions.
RELEVANT_FIELDS = {
    "status",
    "patient_checked_in_at",
    "confirmed_at",
    "attendance_status",
    "date",
    "time",
}

# Best-effort reentrancy guard so terminal handlers that re-save the
# appointment don't recurse.
_processing: set = set()


@receiver(post_save, sender=Appointment, dispatch_uid="attendance_auto_prediction")
def on_appointment_saved(sender, instance, created, update_fields=None, **kwargs):
    if instance.pk in _processing:
        return

    fields = set(update_fields) if update_fields else None
    _processing.add(instance.pk)
    try:
        if created:
            prediction = NoShowPredictionService.predict_and_store(instance)
            _maybe_request_confirmation(instance, prediction)
            return

        # Only react to lifecycle-relevant changes.
        if fields is not None and not (fields & RELEVANT_FIELDS):
            return

        # Completion → learning engine (reconcile accuracy). Do this before any
        # recompute so the stored prediction still reflects pre-terminal risk.
        if (
            instance.status == Appointment.Status.COMPLETED
            and instance.attendance_status != Appointment.Attendance.NO_SHOW
        ):
            interventions.record_completion(instance)
            return

        # Confirm / check-in / cancel / reschedule → recompute risk.
        NoShowPredictionService.predict_and_store(instance)
    except Exception:  # noqa: BLE001 — never break the observed save path
        logger.exception(
            "attendance auto-prediction failed for appointment %s", instance.pk
        )
    finally:
        _processing.discard(instance.pk)


def _maybe_request_confirmation(appointment, prediction):
    """On booking, proactively ask MEDIUM+ risk patients to confirm."""
    try:
        if prediction.risk_level in (
            RiskLevel.MEDIUM,
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        ):
            interventions.send_confirmation_request(appointment)
    except Exception:  # noqa: BLE001
        logger.exception(
            "confirmation request failed for appointment %s", appointment.pk
        )
