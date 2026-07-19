from django.conf import settings
from rest_framework import serializers

from authorization.services import PermissionService
from doctors.serializers import DoctorSerializer

from .models import (
    Allergy,
    LabReport,
    Medication,
    MedicalVisit,
    PatientRecord,
    Prescription,
    VitalSigns,
)


class AllergySerializer(serializers.ModelSerializer):
    class Meta:
        model = Allergy
        fields = ("id", "name", "severity", "notes")


class MedicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Medication
        fields = (
            "id",
            "name",
            "dosage",
            "frequency",
            "start_date",
            "end_date",
            "active",
        )


class PrescriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Prescription
        fields = (
            "id",
            "medicine",
            "dosage",
            "frequency",
            "duration",
            "instructions",
        )


class VitalSignsSerializer(serializers.ModelSerializer):
    class Meta:
        model = VitalSigns
        fields = (
            "temperature",
            "pulse",
            "blood_pressure",
            "oxygen",
            "respiration",
            "blood_sugar",
            "weight",
            "height",
        )


class LabReportSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = LabReport
        fields = ("id", "title", "file_url", "uploaded_at")

    def get_file_url(self, obj):
        if not obj.file:
            return None
        url = obj.file.url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class MedicalVisitSerializer(serializers.ModelSerializer):
    doctor_detail = DoctorSerializer(source="doctor", read_only=True)
    vitals = VitalSignsSerializer(read_only=True)
    prescriptions = PrescriptionSerializer(many=True, read_only=True)
    reports = LabReportSerializer(many=True, read_only=True)
    date = serializers.DateField(source="appointment.date", read_only=True)
    time = serializers.TimeField(source="appointment.time", read_only=True)
    # Phase: Admin Medical Visit Management — visit_type and status live on
    # the related Appointment, not on MedicalVisit itself (no such fields
    # exist on this model, and none are added here); these two read-only
    # fields just surface what already exists there, the same way
    # date/time above already do, so the Admin Panel's Medical Visits card
    # can show "Visit Type" and "Status" without inventing any schema.
    visit_type = serializers.CharField(
        source="appointment.visit_type.name", read_only=True, default=None
    )
    status = serializers.CharField(source="appointment.status", read_only=True)
    # Doctor-only internal documentation: visible to the visit's own
    # patient (existing behavior, unchanged) or to a user holding
    # "emr.edit"; hidden otherwise.
    clinical_notes = serializers.SerializerMethodField()

    class Meta:
        model = MedicalVisit
        fields = (
            "id",
            "appointment",
            "doctor",
            "doctor_detail",
            "date",
            "time",
            "visit_type",
            "status",
            "chief_complaint",
            "diagnosis",
            "clinical_notes",
            "follow_up_date",
            "created_at",
            "vitals",
            "prescriptions",
            "reports",
        )
        read_only_fields = fields

    def get_clinical_notes(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None:
            return None
        if obj.patient_id == getattr(user, "id", None):
            return obj.clinical_notes
        if PermissionService.has_permission(user, "emr.edit"):
            return obj.clinical_notes
        return None


class DoctorVisitSerializer(MedicalVisitSerializer):
    """Additive subclass used only by the doctor self-service EMR endpoints
    (/api/doctors/me/visits/...). MedicalVisitSerializer itself is unchanged
    and keeps serving the patient- and admin-scoped views exactly as before;
    this adds the one field a treating doctor needs that a patient viewing
    their own visit does not — who the visit belongs to — via the existing
    accounts.serializers.UserSerializer."""

    patient = serializers.SerializerMethodField()

    class Meta(MedicalVisitSerializer.Meta):
        fields = MedicalVisitSerializer.Meta.fields + ("patient",)
        read_only_fields = fields

    def get_patient(self, obj):
        from accounts.serializers import UserSerializer

        return UserSerializer(obj.patient).data

    def get_clinical_notes(self, obj):
        # The treating doctor always sees their own documentation in full —
        # the patient-vs-emr.edit gate on the base serializer doesn't apply
        # here since IsLinkedDoctor + doctor-ownership scoping already
        # guarantees this is the doctor who owns the visit.
        return obj.clinical_notes


class PatientRecordSerializer(serializers.ModelSerializer):
    allergies = AllergySerializer(many=True, read_only=True)
    medications = MedicationSerializer(many=True, read_only=True)

    class Meta:
        model = PatientRecord
        fields = (
            "id",
            "blood_group",
            "height_cm",
            "weight_kg",
            "bmi",
            "smoking_status",
            "alcohol",
            "pregnant",
            "emergency_contact",
            "allergies",
            "medications",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "bmi", "created_at", "updated_at")


# ---- write serializers ---------------------------------------------------- #


class PatientRecordUpdateSerializer(serializers.ModelSerializer):
    """Input for PATCH /api/records/patients/{patient_id}/ (admin/staff edit,
    gated on "emr.edit"). Phase: Admin Patient Edit workflow — extended from
    `blood_group`-only to every PatientRecord field the Admin Panel's Edit
    Patient dialog needs, all of which already existed on the model and were
    already readable via PatientRecordSerializer; this only makes them
    writable here too. No schema change, no new endpoint. `bmi` stays
    excluded — it's derived (PatientRecord.save() computes it from
    height_cm/weight_kg) and read-only everywhere, including here."""

    class Meta:
        model = PatientRecord
        fields = (
            "blood_group",
            "height_cm",
            "weight_kg",
            "smoking_status",
            "alcohol",
            "pregnant",
            "emergency_contact",
        )
        extra_kwargs = {f: {"required": False} for f in fields}


class DoctorNotesSerializer(serializers.ModelSerializer):
    """Additive clinical documentation on a visit."""

    class Meta:
        model = MedicalVisit
        fields = (
            "chief_complaint",
            "diagnosis",
            "clinical_notes",
            "follow_up_date",
        )
        extra_kwargs = {f: {"required": False} for f in fields}


class PrescriptionWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Prescription
        fields = ("medicine", "dosage", "frequency", "duration", "instructions")


class LabReportUploadSerializer(serializers.ModelSerializer):
    MAX_UPLOAD_SIZE = getattr(settings, "LAB_REPORT_MAX_UPLOAD_SIZE", 10 * 1024 * 1024)
    ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}
    ALLOWED_MIME_TYPES = {
        "application/pdf": {"pdf"},
        "image/jpeg": {"jpg", "jpeg"},
        "image/png": {"png"},
    }

    class Meta:
        model = LabReport
        fields = ("title", "file")

    def validate_file(self, value):
        if value is None:
            raise serializers.ValidationError("A file is required.")

        if getattr(value, "size", 0) == 0:
            raise serializers.ValidationError("The uploaded file is empty.")

        if getattr(value, "size", 0) > self.MAX_UPLOAD_SIZE:
            raise serializers.ValidationError(
                f"The uploaded file exceeds the maximum size of {self.MAX_UPLOAD_SIZE // (1024 * 1024)} MB."
            )

        name = getattr(value, "name", "") or ""
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if extension not in self.ALLOWED_EXTENSIONS:
            raise serializers.ValidationError(
                "Unsupported file type. Allowed formats: pdf, jpg, jpeg, png."
            )

        content_type = (getattr(value, "content_type", "") or "").lower()
        allowed_extensions = self.ALLOWED_MIME_TYPES.get(content_type)
        if not allowed_extensions or extension not in allowed_extensions:
            raise serializers.ValidationError(
                "The file content type is not supported for the supplied file."
            )

        return value


class AllergyWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Allergy
        fields = ("name", "severity", "notes")


class MedicationWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Medication
        fields = (
            "name",
            "dosage",
            "frequency",
            "start_date",
            "end_date",
            "active",
        )
