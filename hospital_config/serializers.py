"""
Serializers for Hospital Configuration (Phase 2 foundation).

Public and admin shapes are kept as separate classes so a stricter
admin-write serializer can never accidentally leak into the unauthenticated
public response — the same public/admin split already used elsewhere in
this codebase (e.g. doctors.serializers.DoctorSerializer vs
DoctorWriteSerializer).
"""

from rest_framework import serializers

from .models import ConfigurationValue, HospitalProfile

_HOSPITAL_FIELDS = (
    "name",
    "short_name",
    "logo",
    "email",
    "phone",
    "address",
    "website",
    "gst_number",
    "registration_number",
    "timezone",
    "currency",
    "primary_color",
    "secondary_color",
    "updated_at",
)


class HospitalProfileSerializer(serializers.ModelSerializer):
    """Public, read-only shape — GET /api/config/hospital/. Every field is
    safe to expose unauthenticated; nothing sensitive is stored on this
    model (GST/registration numbers are already public on invoices/signage
    in practice)."""

    class Meta:
        model = HospitalProfile
        fields = _HOSPITAL_FIELDS
        read_only_fields = _HOSPITAL_FIELDS


class HospitalProfileWriteSerializer(serializers.ModelSerializer):
    """Admin write shape — GET/PATCH /api/config/admin/hospital/. Same
    fields as the public serializer minus the server-set `updated_at`;
    `updated_by` is stamped by the view from `request.user`, never
    accepted from the client."""

    class Meta:
        model = HospitalProfile
        fields = _HOSPITAL_FIELDS
        read_only_fields = ("updated_at",)
        extra_kwargs = {
            "name": {"required": False},
            "logo": {"required": False},
        }


class ConfigurationValueSerializer(serializers.ModelSerializer):
    """Admin CRUD shape — GET/POST/PATCH/DELETE under
    /api/config/admin/features/. `updated_by` is stamped by the view.

    `value` is accepted and returned as its native JSON type (bool / str /
    int / object) rather than the model's internal text-encoded storage —
    API consumers never need to know about that encoding detail. Both
    directions delegate to ConfigurationValue.get_value()/set_value()."""

    value = serializers.JSONField()

    class Meta:
        model = ConfigurationValue
        fields = (
            "id",
            "key",
            "value",
            "value_type",
            "label",
            "description",
            "category",
            "display_order",
            "updated_at",
        )
        read_only_fields = ("id", "updated_at")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["value"] = instance.get_value()
        return data

    def create(self, validated_data):
        raw_value = validated_data.pop("value")
        instance = ConfigurationValue(**validated_data)
        instance.set_value(raw_value)
        instance.save()
        return instance

    def update(self, instance, validated_data):
        has_value = "value" in self.initial_data
        raw_value = validated_data.pop("value", None)
        for attr, val in validated_data.items():
            setattr(instance, attr, val)
        if has_value:
            instance.set_value(raw_value)
        instance.save()
        return instance
