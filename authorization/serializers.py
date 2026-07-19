"""
Serializers for the authorization API (Feature 7).

Read/write shapes for Role, Permission, and role assignment only. Does not
touch any serializer belonging to another app.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from .models import Permission, Role, UserRole
from .profile_provisioning import StaffProfileProvisioningService

User = get_user_model()


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = (
            "id",
            "name",
            "description",
            "system_role",
            "priority",
            "created_at",
        )
        read_only_fields = ("id", "created_at")


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ("id", "code", "name", "description", "category")
        read_only_fields = fields


class AssignRoleSerializer(serializers.Serializer):
    """Input for POST /api/auth/users/{id}/role/."""

    role = serializers.PrimaryKeyRelatedField(queryset=Role.objects.all())


class UserRoleSerializer(serializers.ModelSerializer):
    """Response shape after a role assignment."""

    role = RoleSerializer(read_only=True)
    assigned_by = serializers.SerializerMethodField()

    class Meta:
        model = UserRole
        fields = ("id", "role", "assigned_by", "assigned_at")
        read_only_fields = fields

    def get_assigned_by(self, obj):
        user = obj.assigned_by
        if user is None:
            return None
        return getattr(user, "name", None) or getattr(user, "email", None) or str(user)


class AdminCreateUserSerializer(serializers.ModelSerializer):
    """Input/output for POST /api/auth/users/ (admin-managed user creation,
    gated on "user.create").

    Reuses, rather than reimplements, every piece of existing machinery:
      - `User.objects.create_user()` (the same manager method
        `RegisterSerializer.create()` already uses) for password hashing,
        so a created account authenticates identically to a self-registered
        one.
      - Django's `validate_password` (the same validator already used by
        `RegisterSerializer` and `PasswordResetConfirmSerializer`) for the
        weak-password check.
      - The model's own `unique=True` on `email`, which DRF's
        ModelSerializer turns into a `UniqueValidator` automatically — no
        hand-written duplicate-email check needed.
      - `UserRole.objects.update_or_create(...)`, the exact call
        `AssignRoleView` already makes, for the role assignment — this is
        not a second, parallel role-assignment code path.
    """

    password = serializers.CharField(write_only=True, validators=[validate_password])
    role = serializers.PrimaryKeyRelatedField(queryset=Role.objects.all())
    is_active = serializers.BooleanField(required=False, default=True)

    class Meta:
        model = User
        fields = (
            "id",
            "name",
            "email",
            "phone",
            "gender",
            "date_of_birth",
            "password",
            "role",
            "is_active",
        )
        read_only_fields = ("id",)
        extra_kwargs = {
            "phone": {"required": False},
            "gender": {"required": False},
            "date_of_birth": {"required": False},
        }

    def create(self, validated_data):
        role = validated_data.pop("role")
        password = validated_data.pop("password")
        is_active = validated_data.pop("is_active", True)

        user = User.objects.create_user(
            email=validated_data["email"],
            password=password,
            name=validated_data["name"],
            phone=validated_data.get("phone", ""),
            gender=validated_data.get("gender", ""),
            date_of_birth=validated_data.get("date_of_birth"),
            is_active=is_active,
        )

        assigned_by = self.context["request"].user
        UserRole.objects.update_or_create(
            user=user,
            role=role,
            defaults={"assigned_by": assigned_by},
        )
        self._assigned_role = role

        # Automatic Staff Profile Linking: a brand-new user has no prior
        # role, so old_role_name=None — this only ever provisions, never
        # deactivates anything.
        self._provisioning_results = StaffProfileProvisioningService.sync_for_role_change(
            user, None, role.name
        )
        return user


class AdminUpdateUserSerializer(serializers.ModelSerializer):
    """Input/output for PATCH /api/auth/users/{id}/ (admin-managed user edit,
    gated on "user.edit").

    Deliberately separate from `accounts.serializers.UserUpdateSerializer`
    (the self-service PATCH /api/auth/me/ shape) rather than reusing it
    as-is: that serializer must never expose `is_active` (a user must not be
    able to deactivate themselves) or `role`, both of which are exactly what
    an *admin* edit needs to change. Every field this serializer does share
    with it (name/phone/gender) behaves identically — same model fields, same
    optional-on-PATCH semantics.

    `role`, like in `AdminCreateUserSerializer`, is optional here: omitting it
    leaves the user's role assignment(s) untouched. When provided, it
    *replaces* the user's role — every other `UserRole` row for this user is
    removed and the selected one is (re)written via the same
    `UserRole.objects.update_or_create()` call `AssignRoleView` and
    `AdminCreateUserSerializer` already use. This mirrors how the rest of the
    Admin Panel presents role as a single value per user (UserDetailView's
    "Assigned role" card, the Users table, the Assign Role and New User
    dialogs are all single-select) even though the underlying schema
    technically allows more than one `UserRole` row per user. Multi-role
    assignment remains fully available for anyone who wants it, unchanged,
    via the existing POST /api/auth/users/{id}/role/ endpoint — this edit
    path does not remove or alter that capability.
    """

    role = serializers.PrimaryKeyRelatedField(
        queryset=Role.objects.all(), required=False
    )

    class Meta:
        model = User
        fields = ("name", "phone", "gender", "date_of_birth", "is_active", "role")
        extra_kwargs = {
            "name": {"required": False},
            "phone": {"required": False},
            "gender": {"required": False},
            "date_of_birth": {"required": False},
            "is_active": {"required": False},
        }

    def update(self, instance, validated_data):
        role = validated_data.pop("role", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        self._provisioning_results = []

        if role is not None:
            # Captured before the reassignment below so
            # StaffProfileProvisioningService can tell what's actually
            # changing (e.g. Doctor -> Receptionist should deactivate the
            # Doctor profile; reassigning the same role again should not).
            old_role = (
                Role.objects.filter(user_roles__user=instance)
                .order_by("-priority")
                .first()
            )
            assigned_by = self.context["request"].user
            with transaction.atomic():
                UserRole.objects.filter(user=instance).exclude(role=role).delete()
                UserRole.objects.update_or_create(
                    user=instance,
                    role=role,
                    defaults={"assigned_by": assigned_by},
                )
            self._assigned_role = role

            # Automatic Staff Profile Linking: reconcile linked staff
            # profiles for the role that's being left and the role that's
            # being taken on. No-ops (empty list) when the role didn't
            # actually change.
            self._provisioning_results = StaffProfileProvisioningService.sync_for_role_change(
                instance,
                old_role.name if old_role else None,
                role.name,
            )
        else:
            self._assigned_role = (
                Role.objects.filter(user_roles__user=instance)
                .order_by("-priority")
                .first()
            )
        return instance


class AdminResetPasswordSerializer(serializers.Serializer):
    """Input for POST /api/auth/users/{id}/reset-password/ (gated on
    "user.edit"). Only `new_password` is validated server-side — confirm-
    password matching is a client-side concern, the same division already
    used by PasswordResetConfirmSerializer (which likewise takes no confirm
    field). Reuses the identical `validate_password` validator."""

    new_password = serializers.CharField(validators=[validate_password])
