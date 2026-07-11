from rest_framework import serializers

from .models import Doctor, Specialty


class SpecialtySerializer(serializers.ModelSerializer):
    class Meta:
        model = Specialty
        fields = ("id", "name", "icon")


class DoctorSerializer(serializers.ModelSerializer):
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
        )
