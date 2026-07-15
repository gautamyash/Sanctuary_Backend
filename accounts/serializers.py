from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """Shared read shape for User, reused across accounts/ (self-service
    /api/auth/me/) and authorization/ (admin user list/detail/create/edit
    output) — never used to validate PATCH/POST input (each of those flows
    has its own dedicated input serializer), so adding a field here only
    ever affects what's returned, not what's writable.

    `is_active` added (Phase: Complete User Management) so admin-facing
    surfaces (Users table, Edit User dialog) can display/pre-fill an
    account's active status. It was previously omitted entirely, which is
    why AdminUpdateUserSerializer/AdminCreateUserSerializer always take
    `is_active` as separate, explicit input rather than reading it back
    from this serializer.
    """

    class Meta:
        model = User
        fields = (
            "id",
            "name",
            "email",
            "date_joined",
            "is_staff",
            "is_active",
            "phone",
            "gender",
            "date_of_birth",
            "profile_photo",
        )
        read_only_fields = ("id", "email", "date_joined", "is_staff", "is_active")


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
