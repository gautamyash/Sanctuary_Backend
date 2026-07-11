from django.contrib import admin

from .models import (
    Appointment,
    AppointmentDurationPrediction,
    VisitType,
    WaitlistEntry,
)


@admin.register(VisitType)
class VisitTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "default_duration", "active")
    list_filter = ("active",)
    search_fields = ("name",)


@admin.register(AppointmentDurationPrediction)
class AppointmentDurationPredictionAdmin(admin.ModelAdmin):
    list_display = (
        "doctor",
        "patient",
        "visit_type",
        "predicted_minutes",
        "actual_minutes",
        "confidence",
        "prediction_source",
        "created_at",
    )
    list_filter = ("prediction_source", "visit_type", "doctor")
    search_fields = ("patient__email", "doctor__name")


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = (
        "patient",
        "doctor",
        "date",
        "time",
        "visit_type",
        "estimated_duration",
        "actual_duration",
        "status",
    )
    list_filter = ("status", "date", "doctor", "visit_type")
    search_fields = ("patient__email", "patient__name", "doctor__name")
    date_hierarchy = "date"


@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(admin.ModelAdmin):
    list_display = (
        "patient",
        "doctor",
        "date",
        "preferred_time",
        "offered_time",
        "status",
        "joined_at",
        "expires_at",
    )
    list_filter = ("doctor", "date", "status")
    search_fields = ("patient__email", "patient__name", "doctor__name")
    date_hierarchy = "date"
