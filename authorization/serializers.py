"""
Serializers for the authorization API (Feature 7).

Read/write shapes for Role, Permission, and role assignment only. Does not
touch any serializer belonging to another app.
"""

from rest_framework import serializers

from .models import Permission, Role, UserRole


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
