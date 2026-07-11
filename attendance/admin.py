from django.contrib import admin

from .models import AppointmentRiskPrediction, ReminderLog


@admin.register(AppointmentRiskPrediction)
class AppointmentRiskPredictionAdmin(admin.ModelAdmin):
    list_display = (
        "appointment",
        "patient",
        "doctor",
        "risk_level",
        "risk_score",
        "confidence",
        "prediction_source",
        "confirmed",
        "actual_outcome",
        "was_correct",
        "predicted_at",
    )
    list_filter = ("risk_level", "prediction_source", "confirmed", "was_correct")
    search_fields = ("patient__email", "patient__name", "doctor__name")
    readonly_fields = ("created_at", "updated_at", "predicted_at")


@admin.register(ReminderLog)
class ReminderLogAdmin(admin.ModelAdmin):
    list_display = (
        "appointment",
        "type",
        "sent_at",
        "delivered",
        "opened",
        "responded",
        "response",
    )
    list_filter = ("type", "delivered", "opened", "responded", "response")
    search_fields = ("appointment__patient__email",)
    date_hierarchy = "sent_at"
