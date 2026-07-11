from django.contrib import admin

from .models import DoctorQueueState


@admin.register(DoctorQueueState)
class DoctorQueueStateAdmin(admin.ModelAdmin):
    list_display = (
        "doctor",
        "date",
        "current_appointment",
        "current_delay_minutes",
        "estimated_finish_time",
        "updated_at",
    )
    list_filter = ("date", "doctor")
    search_fields = ("doctor__name",)
    date_hierarchy = "date"
