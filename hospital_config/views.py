"""
Views for Hospital Configuration (Phase 2 foundation + Phase 2.1 refinement).

Two audiences, kept in separate view classes reachable only via separate
URL prefixes — never the same endpoint gated by method — so it is obvious
at a glance which routes are public and which require RBAC. This mirrors
the existing separation between doctors.urls/doctors.me_urls (admin vs
self-service) elsewhere in this codebase.

  Public  (AllowAny):        GET /api/config/hospital/
                              GET /api/config/features/
                              GET /api/config/bootstrap/
  Admin   (RBAC settings.*): GET/PATCH /api/config/admin/hospital/
                              GET/POST  /api/config/admin/features/
                              GET/PATCH/DELETE /api/config/admin/features/{key}/

Admin endpoints reuse the existing "settings.view" / "settings.edit"
permission codes already seeded in authorization.migrations
(0002_seed_rbac_data) and already granted to the Owner/Admin roles —
no new Permission rows are introduced, following the same reuse-over-new
precedent as doctors.views.DoctorListView (which reuses "system.admin"
rather than inventing a doctor-management-specific code).
"""

from django.db.models import Max
from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from authorization.permissions import PermissionRequired

from .models import ConfigurationValue, HospitalProfile
from .serializers import (
    ConfigurationValueSerializer,
    HospitalProfileSerializer,
    HospitalProfileWriteSerializer,
)


def _config_version(hospital: HospitalProfile) -> int:
    """A lightweight, monotonically-increasing marker mobile clients can
    cache and compare instead of diffing the whole bootstrap payload: the
    unix timestamp (seconds) of whichever changed most recently between
    the hospital profile and every configuration value. Computed on demand
    from existing `updated_at` columns — no extra table, counter, or
    write-time bookkeeping required."""
    latest = hospital.updated_at
    config_latest = ConfigurationValue.objects.aggregate(latest=Max("updated_at"))["latest"]
    if config_latest is not None and (latest is None or config_latest > latest):
        latest = config_latest
    return int(latest.timestamp()) if latest else 0


# ---------------------------------------------------------------- Public ---


class HospitalProfilePublicView(APIView):
    """GET /api/config/hospital/ — unauthenticated. Suitable for mobile app
    startup before login: branding, contact info, locale/currency."""

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        profile = HospitalProfile.load()
        serializer = HospitalProfileSerializer(profile, context={"request": request})
        return Response(serializer.data)


class FeatureFlagPublicView(APIView):
    """GET /api/config/features/ — unauthenticated. Returns every
    boolean-typed configuration entry as a flat {key: bool} map, exactly
    the shape this endpoint returned before ConfigurationValue generalized
    beyond booleans (Phase 2.1) — existing consumers of this endpoint see
    no difference. Non-boolean configuration values (string/integer/json)
    are intentionally not included here; they're available, alongside
    these same boolean keys, from the richer /api/config/bootstrap/
    endpoint below."""

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        flags = {
            row.key: row.get_value()
            for row in ConfigurationValue.objects.filter(
                value_type=ConfigurationValue.ValueType.BOOLEAN
            )
        }
        return Response(flags)


class BootstrapView(APIView):
    """GET /api/config/bootstrap/ — unauthenticated. A single combined
    payload purpose-built for mobile app startup: hospital branding/contact
    info plus every configuration value of every type (not just booleans),
    plus `config_version` so the client can cheaply detect that nothing
    changed since its last launch and skip re-processing the payload.
    Replaces two round trips (hospital + features) with one for this
    specific case; the original two endpoints are unchanged and still work
    independently for any other caller."""

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        hospital = HospitalProfile.load()
        configuration = {
            row.key: row.get_value() for row in ConfigurationValue.objects.all()
        }
        return Response(
            {
                "hospital": HospitalProfileSerializer(
                    hospital, context={"request": request}
                ).data,
                "configuration": configuration,
                "config_version": _config_version(hospital),
            }
        )


# ------------------------------------------------------------------ Admin --


class HospitalProfileAdminView(generics.RetrieveUpdateAPIView):
    """GET/PUT/PATCH /api/config/admin/hospital/ — RBAC-gated. GET requires
    "settings.view"; PUT/PATCH requires "settings.edit"."""

    def get_permissions(self):
        self.permission_code = (
            "settings.view" if self.request.method == "GET" else "settings.edit"
        )
        return [PermissionRequired()]

    def get_serializer_class(self):
        return (
            HospitalProfileSerializer
            if self.request.method == "GET"
            else HospitalProfileWriteSerializer
        )

    def get_object(self):
        return HospitalProfile.load()

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


class FeatureFlagAdminListView(generics.ListCreateAPIView):
    """GET/POST /api/config/admin/features/ — list every configuration
    value, or create a brand-new one. Creating one here is exactly how a
    future setting gets added: no migration, just a new row — of any
    value_type, not only boolean flags."""

    queryset = ConfigurationValue.objects.all()
    serializer_class = ConfigurationValueSerializer

    def get_permissions(self):
        self.permission_code = (
            "settings.view" if self.request.method == "GET" else "settings.edit"
        )
        return [PermissionRequired()]

    def perform_create(self, serializer):
        serializer.save(updated_by=self.request.user)


class FeatureFlagAdminDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/config/admin/features/{key}/ — looked up by the
    entry's stable key rather than numeric id, since both admins and any
    future server-side caller refer to configuration values by key, not
    id."""

    queryset = ConfigurationValue.objects.all()
    serializer_class = ConfigurationValueSerializer
    lookup_field = "key"
    lookup_url_kwarg = "key"

    def get_permissions(self):
        self.permission_code = (
            "settings.view" if self.request.method == "GET" else "settings.edit"
        )
        return [PermissionRequired()]

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)
