"""
Authorization API (Feature 7).

Additive only — these views expose the Role/Permission/UserRole models and
the caller's own permission set. Nothing here wraps or modifies any
existing Features 1-6 endpoint.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.serializers import UserSerializer

from .models import Permission, Role, UserRole
from .permissions import PermissionRequired
from .serializers import (
    AdminCreateUserSerializer,
    AdminResetPasswordSerializer,
    AdminUpdateUserSerializer,
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


class UserListView(APIView):
    """GET  /api/auth/users/ — list users for RBAC administration.
    POST /api/auth/users/ — admin-managed user creation.

    GET requires "user.view" (unchanged); POST requires "user.create" — the
    same per-method permission_code switch already used by
    HospitalProfileAdminView/FeatureFlagAdminListView in hospital_config, so
    this reuses an established pattern rather than introducing a new one.

    GET keeps its exact existing behavior: search via ?q=, response shape
    {"results": [...]}, accounts.UserSerializer. Additive: ?role=<role name>
    now also filters to users holding that RBAC role (e.g. ?role=Patient),
    via the existing UserRole/Role join — no new field or model. Omitting
    the param preserves the exact prior response for every existing caller.

    Search hardening (Phase: Patient Search & Merge Hardening) — same ?q=
    param, same Q-filter chain, no new query param or endpoint:
      - `q` now also matches `phone` (previously name/email only).
      - `q` is stripped of leading/trailing whitespace before filtering, so
        e.g. "  jane " matches the same rows "jane" does instead of
        (accidentally) requiring a literal space in the field.
      - Email matching was already case-insensitive — `icontains` compiles
        to `ILIKE`/`UPPER()`-wrapped `LIKE` regardless of backend — this is
        unchanged, just confirmed.
      - `.distinct()` is now applied unconditionally (previously only when
        `?role=` was also given) so a caller combining `q` with any current
        or future join-based filter can never see the same user twice.

    Additive (Phase: Patient Archive hardening): `?is_active=true`/`false`
    filters the listing to active-only or inactive-only accounts, the same
    query-param style as `?role=`. Omitting it preserves the exact prior
    response (both active and inactive users listed together) for every
    existing caller — this endpoint never hid inactive accounts, so nothing
    changes by default; this only adds the ability to filter when asked.
    """

    def get_permissions(self):
        self.permission_code = (
            "user.view" if self.request.method == "GET" else "user.create"
        )
        return [PermissionRequired()]

    def get(self, request):
        qs = User.objects.all().order_by("name")
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q)
            )
        role_name = request.query_params.get("role")
        if role_name:
            qs = qs.filter(user_roles__role__name__iexact=role_name)
        is_active_param = request.query_params.get("is_active")
        if is_active_param is not None:
            qs = qs.filter(is_active=is_active_param.strip().lower() in ("1", "true", "yes"))
        return Response({"results": UserSerializer(qs.distinct(), many=True).data})

    def post(self, request):
        serializer = AdminCreateUserSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            user = serializer.save()
        role = getattr(serializer, "_assigned_role", None)
        profile_messages = [
            r.message
            for r in getattr(serializer, "_provisioning_results", [])
            if r.message
        ]
        return Response(
            {
                "user": UserSerializer(user).data,
                "role": RoleSerializer(role).data if role else None,
                "profile_messages": profile_messages,
            },
            status=status.HTTP_201_CREATED,
        )


class UserDetailView(APIView):
    """GET   /api/auth/users/{id}/ — a user with their assigned role and
                                      effective permission set.
    PATCH /api/auth/users/{id}/ — admin-managed edit (name, phone, gender,
                                    is_active, role).

    GET requires "user.view" (unchanged); PATCH requires "user.edit" — the
    same per-method permission_code switch already used by UserListView/
    HospitalProfileAdminView, reused rather than reinvented.
    """

    def get_permissions(self):
        self.permission_code = (
            "user.view" if self.request.method == "GET" else "user.edit"
        )
        return [PermissionRequired()]

    def get(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        role = (
            Role.objects.filter(user_roles__user=target)
            .order_by("-priority")
            .first()
        )
        granted = PermissionService.permissions_for(target)
        return Response(
            {
                "user": UserSerializer(target).data,
                "role": RoleSerializer(role).data if role else None,
                "permissions": PermissionSerializer(granted, many=True).data,
            }
        )

    def patch(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        serializer = AdminUpdateUserSerializer(
            target, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            user = serializer.save()
        role = getattr(serializer, "_assigned_role", None)
        profile_messages = [
            r.message
            for r in getattr(serializer, "_provisioning_results", [])
            if r.message
        ]
        return Response(
            {
                "user": UserSerializer(user).data,
                "role": RoleSerializer(role).data if role else None,
                "profile_messages": profile_messages,
            }
        )


class ResetPasswordView(APIView):
    """POST /api/auth/users/{id}/reset-password/ — admin-managed password
    reset. Requires "user.edit".

    Reuses the exact same hashing path as every other password write in this
    codebase: `User.set_password()` (see PasswordResetConfirmView), which
    delegates to Django's configured hasher — nothing new is introduced.
    Because this simply changes the stored password hash, it does not
    interact with is_active/deactivation at all; a deactivated user's
    password can still be reset, but they still cannot authenticate until
    reactivated (enforced by the existing JWT/auth flow, unchanged here).
    """

    permission_classes = [PermissionRequired]
    permission_code = "user.edit"

    def post(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        serializer = AdminResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target.set_password(serializer.validated_data["new_password"])
        target.save(update_fields=["password"])
        return Response({"detail": "Password has been reset."})


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
