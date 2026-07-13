from rest_framework import serializers

from .models import Doctor, DoctorLeave, DoctorSchedule, Specialty


class SpecialtySerializer(serializers.ModelSerializer):
    class Meta:
        model = Specialty
        fields = ("id", "name", "icon")


class DoctorSerializer(serializers.ModelSerializer):
    """Read shape — unchanged for existing consumers (mobile, admin
    list/detail) aside from the additive status/location fields."""

    specialty = serializers.CharField(source="specialty.name", read_only=True)

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
        )
        read_only_fields = ("id",)


class DoctorScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = DoctorSchedule
        fields = ("id", "doctor", "weekday", "start_time", "end_time", "slot_minutes")
        read_only_fields = ("id", "doctor")


class DoctorLeaveSerializer(serializers.ModelSerializer):
    class Meta:
        model = DoctorLeave
        fields = ("id", "doctor", "start_date", "end_date", "reason", "created_at")
        read_only_fields = ("id", "doctor", "created_at")
