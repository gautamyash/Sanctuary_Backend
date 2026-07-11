from django.apps import AppConfig


class MedicalRecordsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "medical_records"
    verbose_name = "Medical Records (EMR)"

    def ready(self):
        # Auto-create a MedicalVisit when an appointment completes. The signal
        # body is fully defensive and never raises into the completion path.
        from . import signals  # noqa: F401
