from django.conf import settings
from django.db import models
from django.db.models import Q


class VisitType(models.Model):
    """Kind of consultation; drives the duration prediction engine."""

    name = models.CharField(max_length=100, unique=True)
    default_duration = models.PositiveIntegerField(help_text="Minutes")
    description = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.default_duration}m)"


class DoctorDayBookingLock(models.Model):
    """Lightweight per-doctor/day row used to serialize concurrent bookings.

    This model exists solely to provide a stable lock target for the upcoming
    booking concurrency work; it does not participate in any current booking
    validation or query paths.
    """

    doctor = models.ForeignKey(
        "doctors.Doctor",
        on_delete=models.CASCADE,
        related_name="booking_locks",
    )
    date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["doctor", "date"],
                name="unique_doctor_day_booking_lock",
            )
        ]

    def __str__(self):
        return f"{self.doctor} / {self.date}"


class Appointment(models.Model):
    class Status(models.TextChoices):
        CONFIRMED = "confirmed"
        PENDING = "pending"
        COMPLETED = "completed"
        CANCELLED = "cancelled"

    ACTIVE_STATUSES = (Status.CONFIRMED, Status.PENDING)

    doctor = models.ForeignKey(
        "doctors.Doctor", on_delete=models.CASCADE, related_name="appointments"
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="appointments",
    )
    date = models.DateField()
    time = models.TimeField()
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.CONFIRMED
    )
    reason = models.CharField(max_length=255, blank=True)

    # AI duration prediction (Feature 2)
    visit_type = models.ForeignKey(
        VisitType,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="appointments",
    )
    estimated_duration = models.PositiveIntegerField(
        default=30, help_text="Predicted consultation length in minutes"
    )
    actual_duration = models.PositiveIntegerField(null=True, blank=True)
    consultation_started_at = models.DateTimeField(null=True, blank=True)
    consultation_completed_at = models.DateTimeField(null=True, blank=True)

    # Real-Time Queue Optimization (Feature 3) — additive, read by the
    # independent queue layer. These never affect booking validation.
    patient_checked_in_at = models.DateTimeField(null=True, blank=True)
    queue_position = models.PositiveIntegerField(null=True, blank=True)

    # AI No-Show Prediction & Attendance Intelligence (Feature 4) — additive,
    # written only by the independent attendance layer. These never affect
    # booking validation, scheduling, waitlist, queue, or duration prediction.
    class Attendance(models.TextChoices):
        UNKNOWN = "unknown"
        CONFIRMED = "confirmed"
        CHECKED_IN = "checked_in"
        COMPLETED = "completed"
        NO_SHOW = "no_show"

    attendance_status = models.CharField(
        max_length=12,
        choices=Attendance.choices,
        default=Attendance.UNKNOWN,
        help_text="Attendance lifecycle tracked by the no-show layer.",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_patient_response = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Most recent patient reminder response.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-time"]
        indexes = [
            models.Index(fields=["doctor", "date"]),
            models.Index(fields=["patient", "status"]),
        ]
        constraints = [
            # The scalability keystone: the DATABASE guarantees a doctor's
            # slot can only be actively booked once, no matter how many
            # clients race for it.
            models.UniqueConstraint(
                fields=["doctor", "date", "time"],
                condition=Q(status__in=["confirmed", "pending"]),
                name="unique_active_slot_per_doctor",
            )
        ]

    def __str__(self):
        return f"{self.patient} → {self.doctor} on {self.date} {self.time}"


class WaitlistEntry(models.Model):
    """A patient waiting for a slot with a doctor on a given day.

    Lifecycle: waiting → offered → accepted | expired,
    or waiting/offered → cancelled (patient left / declined).
    After someone accepts a freed slot, other entries simply remain
    `waiting` for future cancellations on that day.
    """

    class Status(models.TextChoices):
        WAITING = "waiting"
        OFFERED = "offered"
        ACCEPTED = "accepted"
        EXPIRED = "expired"
        CANCELLED = "cancelled"

    ACTIVE_STATUSES = (Status.WAITING, Status.OFFERED)
    OFFER_WINDOW_MINUTES = 10

    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="waitlist_entries",
    )
    doctor = models.ForeignKey(
        "doctors.Doctor", on_delete=models.CASCADE, related_name="waitlist_entries"
    )
    date = models.DateField()
    preferred_time = models.TimeField(null=True, blank=True)
    #: the concrete freed slot currently offered to this patient
    offered_time = models.TimeField(null=True, blank=True)
    #: length in minutes of the freed slot being offered
    offered_duration = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.WAITING
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    offered_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "waitlist entries"
        ordering = ["joined_at"]
        indexes = [
            models.Index(fields=["doctor", "date", "status"]),
            models.Index(fields=["patient", "status"]),
            models.Index(fields=["status", "expires_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["patient", "doctor", "date"],
                condition=Q(status__in=["waiting", "offered"]),
                name="unique_active_waitlist_entry",
            )
        ]

    def __str__(self):
        return f"{self.patient} waiting for {self.doctor} on {self.date} [{self.status}]"


class AppointmentDurationPrediction(models.Model):
    """Snapshot of a duration prediction, later reconciled with the
    actual consultation length to measure and improve accuracy."""

    class Source(models.TextChoices):
        RULE_BASED = "rule_based"
        ML = "ml"
        MANUAL = "manual"

    doctor = models.ForeignKey(
        "doctors.Doctor",
        on_delete=models.CASCADE,
        related_name="duration_predictions",
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="duration_predictions",
    )
    visit_type = models.ForeignKey(
        VisitType,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="duration_predictions",
    )
    appointment = models.OneToOneField(
        Appointment,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="duration_prediction",
    )
    predicted_minutes = models.PositiveIntegerField()
    actual_minutes = models.PositiveIntegerField(null=True, blank=True)
    confidence = models.FloatField(default=0.8)
    prediction_source = models.CharField(
        max_length=12, choices=Source.choices, default=Source.RULE_BASED
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["doctor", "visit_type"]),
        ]

    def __str__(self):
        return (
            f"{self.doctor} / {self.visit_type}: "
            f"{self.predicted_minutes}m predicted, {self.actual_minutes}m actual"
        )
