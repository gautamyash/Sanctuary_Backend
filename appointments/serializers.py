from datetime import date as date_cls

from rest_framework import serializers

from doctors.serializers import DoctorSerializer
from .models import Appointment, VisitType, WaitlistEntry
from .services import find_overlap, within_working_hours


def _time_label(value):
    return value.strftime("%I:%M %p").lstrip("0") if value else None


class VisitTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = VisitType
        fields = ("id", "name", "default_duration", "description")


class AppointmentSerializer(serializers.ModelSerializer):
    doctor_detail = DoctorSerializer(source="doctor", read_only=True)
    time_label = serializers.SerializerMethodField()
    visit_type_name = serializers.CharField(
        source="visit_type.name", read_only=True, default=None
    )
    # metadata about how the estimate was produced (stored on the
    # AppointmentDurationPrediction snapshot, not the appointment itself)
    prediction_source = serializers.ChoiceField(
        choices=["rule_based", "ml", "manual"],
        write_only=True,
        required=False,
        default="rule_based",
    )
    prediction_confidence = serializers.FloatField(
        write_only=True, required=False, default=0.8, min_value=0, max_value=1
    )
    # Attendance Intelligence (Feature 4) — additive, read-only. Present so the
    # mobile app can render attendance status and inline risk without a
    # separate call. Null/"unknown" when the attendance layer has no data yet.
    risk_level = serializers.SerializerMethodField()
    risk_score = serializers.SerializerMethodField()

    class Meta:
        model = Appointment
        fields = (
            "id",
            "doctor",
            "doctor_detail",
            "date",
            "time",
            "time_label",
            "status",
            "reason",
            "visit_type",
            "visit_type_name",
            "estimated_duration",
            "actual_duration",
            "patient_checked_in_at",
            "queue_position",
            "attendance_status",
            "confirmed_at",
            "risk_level",
            "risk_score",
            "prediction_source",
            "prediction_confidence",
            "created_at",
        )
        read_only_fields = (
            "id",
            "status",
            "actual_duration",
            "patient_checked_in_at",
            "queue_position",
            "attendance_status",
            "confirmed_at",
            "risk_level",
            "risk_score",
            "created_at",
        )
        extra_kwargs = {
            "visit_type": {"required": False, "allow_null": True},
            "estimated_duration": {
                "required": False,
                "min_value": 5,
                "max_value": 240,
            },
        }

    def get_time_label(self, obj):
        return _time_label(obj.time)

    def get_risk_level(self, obj):
        # Reverse OneToOne; Django makes the missing-relation error an
        # AttributeError so getattr(..., None) is safe.
        prediction = getattr(obj, "risk_prediction", None)
        return prediction.risk_level if prediction else None

    def get_risk_score(self, obj):
        prediction = getattr(obj, "risk_prediction", None)
        return round(prediction.risk_score) if prediction else None

    def validate_date(self, value):
        if value < date_cls.today():
            raise serializers.ValidationError("Cannot book a date in the past.")
        return value

    def validate(self, attrs):
        doctor = attrs["doctor"]
        day = attrs["date"]
        slot = attrs["time"]
        visit_type = attrs.get("visit_type")

        duration = attrs.get("estimated_duration") or (
            visit_type.default_duration if visit_type else 30
        )
        attrs["estimated_duration"] = duration

        if not within_working_hours(doctor, day, slot, duration):
            raise serializers.ValidationError(
                {
                    "time": "This time is outside the doctor's working hours "
                    "for the requested duration."
                }
            )

        if find_overlap(doctor, day, slot, duration):
            raise serializers.ValidationError(
                {"time": "This time overlaps another appointment."},
                code="overlap",
            )
        return attrs

    def create(self, validated_data):
        # write-only prediction metadata is consumed by the view
        validated_data.pop("prediction_source", None)
        validated_data.pop("prediction_confidence", None)
        return super().create(validated_data)


class WaitlistEntrySerializer(serializers.ModelSerializer):
    doctor_detail = DoctorSerializer(source="doctor", read_only=True)
    preferred_time_label = serializers.SerializerMethodField()
    offered_time_label = serializers.SerializerMethodField()

    class Meta:
        model = WaitlistEntry
        fields = (
            "id",
            "doctor",
            "doctor_detail",
            "date",
            "preferred_time",
            "preferred_time_label",
            "offered_time",
            "offered_time_label",
            "offered_duration",
            "status",
            "joined_at",
            "offered_at",
            "expires_at",
            "accepted_at",
        )
        read_only_fields = (
            "id",
            "offered_time",
            "offered_duration",
            "status",
            "joined_at",
            "offered_at",
            "expires_at",
            "accepted_at",
        )

    def get_preferred_time_label(self, obj):
        return _time_label(obj.preferred_time)

    def get_offered_time_label(self, obj):
        return _time_label(obj.offered_time)

    def validate_date(self, value):
        if value < date_cls.today():
            raise serializers.ValidationError("Date cannot be in the past.")
        return value

    def validate(self, attrs):
        user = self.context["request"].user
        doctor = attrs["doctor"]
        day = attrs["date"]

        if WaitlistEntry.objects.filter(
            patient=user,
            doctor=doctor,
            date=day,
            status__in=WaitlistEntry.ACTIVE_STATUSES,
        ).exists():
            raise serializers.ValidationError(
                "You are already on the waitlist for this doctor on this day."
            )

        if Appointment.objects.filter(
            patient=user,
            doctor=doctor,
            date=day,
            status__in=Appointment.ACTIVE_STATUSES,
        ).exists():
            raise serializers.ValidationError(
                "You already have an appointment with this doctor on this day."
            )
        return attrs
