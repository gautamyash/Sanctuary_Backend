"""
Authorization API (Feature 7).

Additive only — these views expose the Role/Permission/UserRole models and
the caller's own permission set. Nothing here wraps or modifies any
existing Features 1-6 endpoint.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Permission, Role, UserRole
from .serializers import (
    AssignRoleSerializer,
    PermissionSerializer,
    RoleSerializer,
    UserRoleSerializer,
)
from .services import PermissionService

User = get_user_model()

# Role definitions and role assignment are RBAC administration actions,
# gated behind the "system.admin" permission code.
_MANAGE_ROLES_CODE = "system.admin"


def _require(request, permission_code):
    """Run PermissionService.require_permission() and translate its Django
    PermissionDenied into DRF's, so the standard DRF exception handler
    returns a 403 response instead of a 500."""
    try:
        PermissionService.require_permission(request.user, permission_code)
    except DjangoPermissionDenied as exc:
        raise DRFPermissionDenied(str(exc))


class RoleListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/auth/roles/ — list all roles.
    POST /api/auth/roles/ — create a role (requires "system.admin").
    """

    queryset = Role.objects.all()
    serializer_class = RoleSerializer

    def create(self, request, *args, **kwargs):
        _require(request, _MANAGE_ROLES_CODE)
        return super().create(request, *args, **kwargs)


class RoleUpdateView(generics.UpdateAPIView):
    """PATCH /api/auth/roles/{id}/ — update a role (requires "system.admin")."""

    queryset = Role.objects.all()
    serializer_class = RoleSerializer

    def update(self, request, *args, **kwargs):
        _require(request, _MANAGE_ROLES_CODE)
        return super().update(request, *args, **kwargs)


class PermissionListView(generics.ListAPIView):
    """GET /api/auth/permissions/ — the full permission catalog."""

    queryset = Permission.objects.all()
    serializer_class = PermissionSerializer
    pagination_class = None


class AssignRoleView(APIView):
    """POST /api/auth/users/{id}/role/ — assign a role to a user.

    Requires "system.admin"; the write is wrapped in transaction.atomic()
    so the assignment is all-or-nothing.
    """

    def post(self, request, pk):
        _require(request, _MANAGE_ROLES_CODE)

        target_user = get_object_or_404(User, pk=pk)
        serializer = AssignRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = serializer.validated_data["role"]

        with transaction.atomic():
            user_role, _created = UserRole.objects.update_or_create(
                user=target_user,
                role=role,
                defaults={"assigned_by": request.user},
            )

        return Response(
            UserRoleSerializer(user_role).data, status=status.HTTP_201_CREATED
        )


class MyPermissionsView(APIView):
    """GET /api/auth/me/permissions/ — the caller's roles and their
    deduplicated, alphabetically sorted permission codes."""

    def get(self, request):
        user = request.user

        role_names = (
            Role.objects.filter(user_roles__user=user)
            .values_list("name", flat=True)
            .distinct()
        )

        # Public method: handles the superuser/anonymous cases internally,
        # so this view no longer needs its own branching or a private
        # PermissionService helper.
        codes = PermissionService.permissions_for(user).values_list(
            "code", flat=True
        )

        return Response(
            {
                "roles": sorted(set(role_names)),
                "permissions": sorted(set(codes)),
            }
        )
