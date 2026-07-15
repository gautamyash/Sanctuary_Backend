from rest_framework import serializers

from .models import (
    Certification,
    Doctor,
    DoctorLeave,
    DoctorSchedule,
    Education,
    Language,
    Specialty,
)


class SpecialtySerializer(serializers.ModelSerializer):
    class Meta:
        model = Specialty
        fields = ("id", "name", "icon")


class CertificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Certification
        fields = ("id", "name", "issuing_body", "year")
        read_only_fields = ("id",)


class LanguageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Language
        fields = ("id", "name", "proficiency")
        read_only_fields = ("id",)


class EducationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Education
        fields = ("id", "institution", "degree", "year")
        read_only_fields = ("id",)


class DoctorSerializer(serializers.ModelSerializer):
    """Read shape — unchanged for existing consumers (mobile, admin
    list/detail) aside from the additive status/location/profile fields."""

    specialty = serializers.CharField(source="specialty.name", read_only=True)
    certifications = CertificationSerializer(many=True, read_only=True)
    languages = LanguageSerializer(many=True, read_only=True)
    education = EducationSerializer(many=True, read_only=True)

    class Meta:
        model = Doctor
        fields = (
            "id",
            "name",
            "specialty",
            "hospital",
            "address",
            "distance_km",
            "rating",
            "reviews",
            "years_experience",
            "fee",
            "about",
            "photo",
            "color",
            "on_duty",
            "on_leave",
            "wing",
            "floor",
            "room",
            "bio",
            "profile_photo",
            "consultation_duration",
            "license_number",
            "certifications",
            "languages",
            "education",
        )


class DoctorWriteSerializer(serializers.ModelSerializer):
    """Create/update shape for the admin Doctor Management module.
    `specialty` is writable here (by id) — the read serializer keeps it as a
    display-only name so existing GET consumers are unaffected."""

    class Meta:
        model = Doctor
        fields = (
            "id",
            "name",
            "specialty",
            "hospital",
            "address",
            "distance_km",
            "rating",
            "reviews",
            "years_experience",
            "fee",
            "about",
            "photo",
            "color",
            "is_active",
            "on_duty",
            "on_leave",
            "wing",
            "floor",
            "room",
            "user",
            "bio",
            "profile_photo",
            "consultation_duration",
            "license_number",
        )
        read_only_fields = ("id",)
        extra_kwargs = {
            "user": {"required": False},
            "bio": {"required": False},
            "profile_photo": {"required": False},
            "consultation_duration": {"required": False},
            "license_number": {"required": False},
        }


class DoctorScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = DoctorSchedule
        fields = ("id", "doctor", "weekday", "start_time", "end_time", "slot_minutes")
        read_only_fields = ("id", "doctor")


class DoctorLeaveSerializer(serializers.ModelSerializer):
    """Admin/staff shape (Doctor Management module + review workflow).
    `leave_type`/`status`/`notes` are additive and optional so the existing
    Next.js admin panel keeps working even if it never sends them.
    `approved_by`/`approved_at` are set by the review view, never by the
    client."""

    class Meta:
        model = DoctorLeave
        fields = (
            "id",
            "doctor",
            "start_date",
            "end_date",
            "reason",
            "leave_type",
            "status",
            "notes",
            "approved_by",
            "approved_at",
            "created_at",
        )
        read_only_fields = ("id", "doctor", "approved_by", "approved_at", "created_at")


# ---------------------------------------------------------------------------
# Doctor self-service (mobile app) serializers.
#
# Entirely separate from the admin serializers above: these never expose or
# accept a `doctor`/`doctor_id` field from the client. The view always
# resolves the doctor from `request.user.doctor_profile`, so a doctor can
# never read or write another doctor's data.
# ---------------------------------------------------------------------------


class DoctorMeSerializer(serializers.ModelSerializer):
    """GET/PATCH /api/doctors/me/ — the authenticated doctor's own profile."""

    specialty = serializers.CharField(source="specialty.name", read_only=True)
    certifications = CertificationSerializer(many=True, read_only=True)
    languages = LanguageSerializer(many=True, read_only=True)
    education = EducationSerializer(many=True, read_only=True)

    class Meta:
        model = Doctor
        fields = (
            "id",
            "name",
            "specialty",
            "hospital",
            "rating",
            "reviews",
            "years_experience",
            "fee",
            "about",
            "bio",
            "photo",
            "profile_photo",
            "consultation_duration",
            "on_duty",
            "on_leave",
            "wing",
            "floor",
            "room",
            "certifications",
            "languages",
            "education",
        )
        read_only_fields = (
            "id",
            "name",
            "specialty",
            "hospital",
            "rating",
            "reviews",
            "years_experience",
            "fee",
            "photo",
            "on_duty",
            "on_leave",
            "wing",
            "floor",
            "room",
        )


class DoctorMeScheduleSerializer(serializers.ModelSerializer):
    """/api/doctors/me/schedule/ — reuses DoctorSchedule; `doctor` is never
    accepted from the client, the view sets it from request.user.doctor_profile."""

    class Meta:
        model = DoctorSchedule
        fields = ("id", "weekday", "start_time", "end_time", "slot_minutes")
        read_only_fields = ("id",)


class DoctorMeLeaveSerializer(serializers.ModelSerializer):
    """/api/doctors/me/leaves/ — a doctor may request leave (leave_type,
    dates, reason) but can never set status/notes/approved_by/approved_at
    themselves; those are staff-only via the existing admin leave endpoints."""

    class Meta:
        model = DoctorLeave
        fields = (
            "id",
            "leave_type",
            "start_date",
            "end_date",
            "reason",
            "status",
            "notes",
            "approved_by",
            "approved_at",
            "created_at",
        )
        read_only_fields = (
            "id",
            "status",
            "notes",
            "approved_by",
            "approved_at",
            "created_at",
        )
