"""
Attendance Intelligence models (Feature 4).

An independent prediction layer that sits on top of the existing Appointment
model. Nothing here feeds back into booking, scheduling, waitlist, queue, or
duration-prediction logic — these tables are pure derived/observational state.
"""

from django.conf import settings
from django.db import models


class RiskLevel(models.TextChoices):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PredictionSource(models.TextChoices):
    RULE_BASED = "rule_based"
    ML = "ml"
    MANUAL = "manual"


class AppointmentRiskPrediction(models.Model):
    """The latest no-show risk snapshot for a single appointment.

    One row per appointment (OneToOne). Recomputed by the prediction engine on
    every lifecycle event (create, reschedule, confirm, cancel, check-in).
    """

    appointment = models.OneToOneField(
        "appointments.Appointment",
        on_delete=models.CASCADE,
        related_name="risk_prediction",
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="risk_predictions",
    )
    doctor = models.ForeignKey(
        "doctors.Doctor",
        on_delete=models.CASCADE,
        related_name="risk_predictions",
    )
    #: 0-100 point score produced by the rule/ML engine.
    risk_score = models.FloatField(default=0)
    risk_level = models.CharField(
        max_length=8, choices=RiskLevel.choices, default=RiskLevel.LOW
    )
    #: 0..1 model confidence.
    confidence = models.FloatField(default=0.8)
    prediction_source = models.CharField(
        max_length=12,
        choices=PredictionSource.choices,
        default=PredictionSource.RULE_BASED,
    )
    #: Human-readable drivers behind the score (additive to the spec fields so
    #: the /risk/ endpoint can echo them without recomputation).
    reasons = models.JSONField(default=list, blank=True)
    predicted_at = models.DateTimeField(auto_now=True)
    confirmed = models.BooleanField(default=False)

    #: Reconciliation for the learning engine / analytics accuracy metric.
    #: attended | no_show | None (unresolved).
    actual_outcome = models.CharField(max_length=12, blank=True, default="")
    was_correct = models.BooleanField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-predicted_at"]
        indexes = [
            models.Index(fields=["risk_level"]),
            models.Index(fields=["predicted_at"]),
            models.Index(fields=["doctor"]),
            models.Index(fields=["patient"]),
        ]

    def __str__(self):
        return f"Risk<{self.appointment_id} {self.risk_level} {round(self.risk_score)}>"


class ReminderLog(models.Model):
    """Log of a reminder/confirmation message sent for an appointment.

    Log-only today (see NotificationService). Designed for Expo Push delivery
    tracking: delivered/opened/responded flags are populated by webhooks later.
    """

    class Type(models.TextChoices):
        H24 = "24_hour", "24 hour"
        H6 = "6_hour", "6 hour"
        H2 = "2_hour", "2 hour"
        CONFIRMATION = "confirmation", "Confirmation request"
        FOLLOWUP = "followup", "No-show follow-up"

    class Response(models.TextChoices):
        CONFIRMED = "confirmed"
        RESCHEDULE = "reschedule"
        CANCEL = "cancel"
        IGNORED = "ignored"

    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.CASCADE,
        related_name="reminder_logs",
    )
    type = models.CharField(max_length=16, choices=Type.choices)
    sent_at = models.DateTimeField(auto_now_add=True)
    delivered = models.BooleanField(default=False)
    opened = models.BooleanField(default=False)
    responded = models.BooleanField(default=False)
    response = models.CharField(
        max_length=12, choices=Response.choices, blank=True, default=""
    )

    class Meta:
        ordering = ["-sent_at"]
        indexes = [
            models.Index(fields=["appointment", "type"]),
            models.Index(fields=["type", "sent_at"]),
        ]

    def __str__(self):
        return f"Reminder<{self.appointment_id} {self.type}>"
