from django.db import models


class DoctorQueueState(models.Model):
    """Snapshot of a doctor's live queue for a single day.

    One row per (doctor, date). Rebuilt by QueueService.recalculate_queue
    on every queue lifecycle event (check-in, start, complete, cancel,
    reschedule). This is a pure read/derived layer over Appointment data —
    it never feeds back into booking or scheduling.
    """

    doctor = models.ForeignKey(
        "doctors.Doctor", on_delete=models.CASCADE, related_name="queue_states"
    )
    date = models.DateField()
    current_appointment = models.ForeignKey(
        "appointments.Appointment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Consultation currently in progress, if any.",
    )
    queue_started_at = models.DateTimeField(
        null=True, blank=True, help_text="When the doctor began the day's first consultation."
    )
    current_delay_minutes = models.IntegerField(
        default=0, help_text="Signed minutes behind (>0) or ahead (<0) of schedule."
    )
    estimated_finish_time = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["doctor", "date"], name="unique_queue_state_per_doctor_day"
            )
        ]
        indexes = [
            models.Index(fields=["doctor", "date"]),
        ]
        ordering = ["-date", "doctor_id"]

    def __str__(self):
        return f"Queue<{self.doctor} {self.date} delay={self.current_delay_minutes}m>"
