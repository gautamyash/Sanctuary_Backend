"""
Reusable notification module — additive, independent app.

Serves both the Patient and the Doctor mobile apps off the same model,
since both are just `User` accounts (a linked Doctor is still a User). No
other app writes here yet; future work can call
`Notification.objects.create(...)` from appointment/queue/billing events
without this app ever needing to know about them.
"""

from django.conf import settings
from django.db import models


class Notification(models.Model):
    class NotificationType(models.TextChoices):
        APPOINTMENT = "appointment"
        LAB_RESULT = "lab_result"
        PRESCRIPTION = "prescription"
        MESSAGE = "message"
        BILLING = "billing"
        SYSTEM = "system"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    notification_type = models.CharField(
        max_length=16,
        choices=NotificationType.choices,
        default=NotificationType.SYSTEM,
    )
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=500, blank=True, default="")
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read"]),
            models.Index(fields=["recipient", "created_at"]),
        ]

    def __str__(self):
        return f"{self.notification_type}: {self.title} -> {self.recipient}"
