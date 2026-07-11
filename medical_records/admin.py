from django.contrib import admin

from .models import (
    Allergy,
    LabReport,
    Medication,
    MedicalVisit,
    PatientRecord,
    Prescription,
    VitalSigns,
)


class AllergyInline(admin.TabularInline):
    model = Allergy
    extra = 0


class MedicationInline(admin.TabularInline):
    model = Medication
    extra = 0


@admin.register(PatientRecord)
class PatientRecordAdmin(admin.ModelAdmin):
    list_display = ("patient", "blood_group", "bmi", "smoking_status", "updated_at")
    search_fields = ("patient__email", "patient__name")
    inlines = [AllergyInline, MedicationInline]


class PrescriptionInline(admin.TabularInline):
    model = Prescription
    extra = 0


class LabReportInline(admin.TabularInline):
    model = LabReport
    extra = 0


class VitalSignsInline(admin.StackedInline):
    model = VitalSigns
    extra = 0


@admin.register(MedicalVisit)
class MedicalVisitAdmin(admin.ModelAdmin):
    list_display = ("patient", "doctor", "diagnosis", "follow_up_date", "created_at")
    list_filter = ("doctor", "created_at")
    search_fields = ("patient__email", "patient__name", "diagnosis")
    date_hierarchy = "created_at"
    inlines = [VitalSignsInline, PrescriptionInline, LabReportInline]


@admin.register(LabReport)
class LabReportAdmin(admin.ModelAdmin):
    list_display = ("title", "medical_visit", "uploaded_by", "uploaded_at")
    search_fields = ("title",)
