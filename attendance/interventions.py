"""
Intervention & learning helpers for the attendance layer (Feature 4).

These react to appointment outcomes without touching booking/scheduling:
  * record_outcome / record_completion — the learning engine's reconciliation
    of predicted risk against what actually happened (accuracy tracking).
  * mark_no_show — marks a missed appointment, notifies, and backfills the
    freed slot through the EXISTING Smart Waitlist auto-fill.
  * maybe_backfill_waitlist — guarded high/critical-risk backfill that never
    double-offers a slot the existing cancel flow already handled.
"""

from django.db import transaction
from django.utils import timezone

from appointments.analytics import track
from appointments.models import Appointment, WaitlistEntry
from appointments.notifications import NotificationService
from appointments.services import auto_fill_slot

from .models import AppointmentRiskPrediction, ReminderLog, RiskLevel

HIGH_RISK_LEVELS = (RiskLevel.HIGH, RiskLevel.CRITICAL)


def _current_prediction(appointment):
    """Fetch the persisted risk row fresh from the DB.

    We deliberately avoid the cached ``appointment.risk_prediction`` reverse
    accessor: it can hold a stale snapshot from when the appointment was first
    created, whereas the DB row reflects the latest recomputation.
    """
    return AppointmentRiskPrediction.objects.filter(
        appointment=appointment
    ).first()


def record_outcome(appointment, outcome: str) -> None:
    """Reconcile the stored prediction with the actual outcome.

    Must be called while the prediction still reflects the pre-terminal risk
    (i.e. before any terminal recompute). ``outcome`` is "attended" or
    "no_show".
    """
    prediction = _current_prediction(appointment)
    if prediction is None:
        return
    predicted_high = prediction.risk_level in HIGH_RISK_LEVELS
    correct = predicted_high if outcome == "no_show" else not predicted_high
    prediction.actual_outcome = outcome
    prediction.was_correct = correct
    prediction.save(update_fields=["actual_outcome", "was_correct", "updated_at"])
    track(
        "prediction_correct" if correct else "prediction_incorrect",
        appointment=appointment.id,
        predicted=prediction.risk_level,
        outcome=outcome,
    )


def record_completion(appointment) -> None:
    """Learning-engine hook for a completed consultation (patient attended)."""
    record_outcome(appointment, "attended")
    if appointment.attendance_status != Appointment.Attendance.COMPLETED:
        appointment.attendance_status = Appointment.Attendance.COMPLETED
        appointment.save(update_fields=["attendance_status", "updated_at"])


def maybe_backfill_waitlist(appointment) -> WaitlistEntry | None:
    """If the (now-freed) appointment was HIGH/CRITICAL risk, offer the slot to
    the waitlist — reusing the existing auto-fill engine.

    Guard: if an offer for this exact freed slot is already outstanding, the
    existing cancel flow already handled it, so we do nothing (never
    double-offer a single slot)."""
    prediction = _current_prediction(appointment)
    if prediction is None or prediction.risk_level not in HIGH_RISK_LEVELS:
        return None

    already_offered = WaitlistEntry.objects.filter(
        doctor=appointment.doctor,
        date=appointment.date,
        offered_time=appointment.time,
        status=WaitlistEntry.Status.OFFERED,
    ).exists()
    if already_offered:
        return None

    entry = auto_fill_slot(
        appointment.doctor,
        appointment.date,
        appointment.time,
        appointment.estimated_duration or 30,
    )
    if entry is not None:
        track(
            "high_risk_backfill_triggered",
            appointment=appointment.id,
            entry=entry.id,
        )
    return entry


def mark_no_show(appointment) -> None:
    """Mark an appointment as a no-show: reconcile accuracy, notify, log, and
    backfill the freed slot from the waitlist if it was high risk."""
    # Production hardening: reconciling the prediction, flipping
    # attendance_status, and logging the follow-up reminder are three model
    # writes (AppointmentRiskPrediction, Appointment, ReminderLog) that make up
    # one logical "mark as no-show" operation — previously not atomic, so a
    # failure partway through could leave the appointment marked no-show
    # without its reminder log, or vice versa. The notification (log-only, no
    # DB write) and the waitlist backfill (its own existing atomic scope via
    # auto_fill_slot) stay outside, matching how every other lifecycle
    # endpoint in this codebase keeps side effects after the core write.
    with transaction.atomic():
        # Reconcile BEFORE mutating so the prediction still reflects
        # pre-no-show risk.
        record_outcome(appointment, "no_show")

        appointment.attendance_status = Appointment.Attendance.NO_SHOW
        appointment.save(update_fields=["attendance_status", "updated_at"])

        ReminderLog.objects.create(
            appointment=appointment, type=ReminderLog.Type.FOLLOWUP
        )

    track("no_show_detected", appointment=appointment.id)
    NotificationService.send_no_show_followup(appointment)
    maybe_backfill_waitlist(appointment)


def send_confirmation_request(appointment) -> ReminderLog:
    """Send (log) a confirmation request and record it."""
    NotificationService.send_confirmation_request(appointment)
    log = ReminderLog.objects.create(
        appointment=appointment, type=ReminderLog.Type.CONFIRMATION
    )
    track("confirmation_sent", appointment=appointment.id)
    return log
