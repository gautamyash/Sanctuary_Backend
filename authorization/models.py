"""
Authorization / RBAC foundation (Feature 7).

Additive only: these models introduce the role/permission schema but are not
yet wired into any existing view, serializer, or URL. Nothing here changes
the behavior of any existing app.
"""

from django.conf import settings
from django.db import models


class Role(models.Model):
    """A named role that can be assigned to users and carries a set of
    permissions via RolePermission."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    system_role = models.BooleanField(
        default=False,
        help_text="System-defined roles (e.g. Admin) cannot be deleted by users.",
    )
    priority = models.PositiveIntegerField(
        default=0,
        help_text="Higher priority roles take precedence when a user holds more than one.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-priority", "name"]
        indexes = [
            models.Index(fields=["name"], name="authz_role_name_idx"),
            models.Index(fields=["system_role"], name="authz_role_system_idx"),
        ]

    def __str__(self):
        return self.name


class Permission(models.Model):
    """A single grantable capability, identified by a stable code
    (e.g. "manage_users")."""

    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["category", "name"]
        indexes = [
            models.Index(fields=["code"], name="authz_permission_code_idx"),
            models.Index(fields=["category"], name="authz_permission_cat_idx"),
        ]

    def __str__(self):
        return self.code


class RolePermission(models.Model):
    """Join row granting a Permission to a Role."""

    role = models.ForeignKey(
        Role, on_delete=models.CASCADE, related_name="role_permissions"
    )
    permission = models.ForeignKey(
        Permission, on_delete=models.CASCADE, related_name="role_permissions"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["role", "permission"], name="unique_role_permission"
            )
        ]
        indexes = [
            models.Index(fields=["role", "permission"], name="authz_roleperm_idx"),
        ]

    def __str__(self):
        return f"{self.role} -> {self.permission}"


class UserRole(models.Model):
    """Assignment of a Role to a user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="user_roles",
    )
    role = models.ForeignKey(
        Role, on_delete=models.CASCADE, related_name="user_roles"
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="role_assignments_made",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "role"], name="unique_user_role"
            )
        ]
        indexes = [
            models.Index(fields=["user"], name="authz_userrole_user_idx"),
            models.Index(fields=["role"], name="authz_userrole_role_idx"),
        ]

    def __str__(self):
        return f"{self.user} -> {self.role}"
