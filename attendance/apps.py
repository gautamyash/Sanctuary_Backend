from django.apps import AppConfig


class AttendanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "attendance"
    verbose_name = "Attendance Intelligence"

    def ready(self):
        # Connect the auto-prediction signal handlers. Import here so the app
        # registry is fully populated first. Signal bodies are fully defensive
        # and never raise into the booking/scheduling paths they observe.
        from . import signals  # noqa: F401
