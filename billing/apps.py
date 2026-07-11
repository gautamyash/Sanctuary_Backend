from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "billing"
    verbose_name = "Revenue & Billing"

    def ready(self):
        # Billing reacts to appointment completion and EMR events but never
        # controls them. Signal bodies are fully defensive.
        from . import signals  # noqa: F401
