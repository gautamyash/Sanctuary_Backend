"""
PermissionService — read-only authorization checks for the RBAC layer
(Feature 7).

This module only *reads* the UserRole -> Role -> RolePermission graph
already defined in authorization/models.py. It does not wrap, modify, or
otherwise affect any existing view, serializer, or business logic — nothing
calls this service yet.
"""

from django.core.exceptions import PermissionDenied

from .models import Permission


class PermissionService:
    """Permission checks for a given user.

    Rules:
      - Superusers automatically pass every check.
      - Unauthenticated (including anonymous) users always fail.
      - Every other user's permissions come from Permission rows reachable
        through their UserRole -> Role -> RolePermission assignments.

    No caching: every check queries the database.
    """

    # ------------------------------------------------------------------ #
    # Internal helpers (shared by every public method below)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_authenticated(user) -> bool:
        """True for a real, logged-in user; False for None/AnonymousUser."""
        return bool(user) and bool(getattr(user, "is_authenticated", False))

    @staticmethod
    def _permission_queryset(user):
        """Base queryset of Permission rows reachable through this user's
        roles, via the existing UserRole -> Role -> RolePermission chain.
        Defined once here so every public method reuses the same join
        instead of re-deriving it."""
        return Permission.objects.filter(
            role_permissions__role__user_roles__user=user
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    @classmethod
    def has_permission(cls, user, permission_code: str) -> bool:
        if not cls._is_authenticated(user):
            return False
        if getattr(user, "is_superuser", False):
            return True
        return cls._permission_queryset(user).filter(code=permission_code).exists()

    @classmethod
    def has_any_permission(cls, user, permission_codes) -> bool:
        if not cls._is_authenticated(user):
            return False
        if getattr(user, "is_superuser", False):
            return True
        if not permission_codes:
            return False
        return cls._permission_queryset(user).filter(
            code__in=permission_codes
        ).exists()

    @classmethod
    def has_all_permissions(cls, user, permission_codes) -> bool:
        if not cls._is_authenticated(user):
            return False
        if getattr(user, "is_superuser", False):
            return True
        required = set(permission_codes or [])
        if not required:
            return True
        granted = set(
            cls._permission_queryset(user)
            .filter(code__in=required)
            .values_list("code", flat=True)
        )
        return required.issubset(granted)

    @classmethod
    def require_permission(cls, user, permission_code: str) -> None:
        if not cls.has_permission(user, permission_code):
            raise PermissionDenied(
                f"Missing required permission: {permission_code}"
            )

    @classmethod
    def permissions_for(cls, user):
        """All Permission rows visible to this user: every Permission for a
        superuser, none for an unauthenticated user, otherwise everything
        reachable through their UserRole -> Role -> RolePermission chain.

        Public counterpart to _permission_queryset(), for callers outside
        this class that need the full set rather than a single check.
        """
        if not cls._is_authenticated(user):
            return Permission.objects.none()
        if getattr(user, "is_superuser", False):
            return Permission.objects.all()
        return cls._permission_queryset(user)
