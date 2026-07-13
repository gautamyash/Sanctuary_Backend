from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "name",
            "email",
            "date_joined",
            "is_staff",
            "phone",
            "gender",
            "date_of_birth",
            "profile_photo",
        )
        read_only_fields = ("id", "email", "date_joined", "is_staff")


class UserUpdateSerializer(serializers.ModelSerializer):
    """PATCH /api/auth/me/ shape. Email is intentionally not editable here —
    changing a login identifier needs its own re-verification flow, which is
    out of scope for this pass."""

    class Meta:
        model = User
        fields = ("name", "phone", "gender", "date_of_birth", "profile_photo")
        extra_kwargs = {
            "name": {"required": False},
            "phone": {"required": False},
            "gender": {"required": False},
            "date_of_birth": {"required": False},
            "profile_photo": {"required": False},
        }


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, validators=[validate_password]
    )

    class Meta:
        model = User
        fields = ("name", "email", "password")

    def create(self, validated_data):
        return User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            name=validated_data["name"],
        )


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(validators=[validate_password])
