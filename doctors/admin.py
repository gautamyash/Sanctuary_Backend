from django.contrib import admin

from .models import (
    Certification,
    Doctor,
    DoctorLeave,
    DoctorSchedule,
    Education,
    Language,
    Specialty,
)


@admin.register(Specialty)
class SpecialtyAdmin(admin.ModelAdmin):
    list_display = ("name", "icon")
    search_fields = ("name",)


class DoctorScheduleInline(admin.TabularInline):
    model = DoctorSchedule
    extra = 1


class CertificationInline(admin.TabularInline):
    model = Certification
    extra = 0


class LanguageInline(admin.TabularInline):
    model = Language
    extra = 0


class EducationInline(admin.TabularInline):
    model = Education
    extra = 0


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "specialty",
        "hospital",
        "rating",
        "fee",
        "is_active",
        "user",
    )
    list_filter = ("specialty", "is_active")
    search_fields = ("name", "hospital")
    inlines = [DoctorScheduleInline, CertificationInline, LanguageInline, EducationInline]


@admin.register(DoctorLeave)
class DoctorLeaveAdmin(admin.ModelAdmin):
    list_display = ("doctor", "leave_type", "start_date", "end_date", "status", "approved_by")
    list_filter = ("leave_type", "status")
    search_fields = ("doctor__name",)
