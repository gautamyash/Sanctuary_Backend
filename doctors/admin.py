from django.contrib import admin

from .models import Doctor, DoctorSchedule, Specialty


@admin.register(Specialty)
class SpecialtyAdmin(admin.ModelAdmin):
    list_display = ("name", "icon")
    search_fields = ("name",)


class DoctorScheduleInline(admin.TabularInline):
    model = DoctorSchedule
    extra = 1


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "specialty",
        "hospital",
        "rating",
        "fee",
        "is_active",
    )
    list_filter = ("specialty", "is_active")
    search_fields = ("name", "hospital")
    inlines = [DoctorScheduleInline]
