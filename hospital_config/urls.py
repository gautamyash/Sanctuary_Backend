"""
URL patterns for Hospital Configuration (Phase 2 foundation).

Included from config/urls.py under the "api/" prefix, same as every other
app, giving:

  Public: /api/config/hospital/, /api/config/features/,
          /api/config/bootstrap/
  Admin:  /api/config/admin/hospital/, /api/config/admin/features/,
          /api/config/admin/features/{key}/
"""

from django.urls import path

from .views import (
    BootstrapView,
    FeatureFlagAdminDetailView,
    FeatureFlagAdminListView,
    FeatureFlagPublicView,
    HospitalProfileAdminView,
    HospitalProfilePublicView,
)

urlpatterns = [
    # Public — no authentication.
    path(
        "config/hospital/",
        HospitalProfilePublicView.as_view(),
        name="config-hospital-public",
    ),
    path(
        "config/features/",
        FeatureFlagPublicView.as_view(),
        name="config-features-public",
    ),
    path(
        "config/bootstrap/",
        BootstrapView.as_view(),
        name="config-bootstrap",
    ),
    # Admin — RBAC-gated ("settings.view" / "settings.edit").
    path(
        "config/admin/hospital/",
        HospitalProfileAdminView.as_view(),
        name="config-hospital-admin",
    ),
    path(
        "config/admin/features/",
        FeatureFlagAdminListView.as_view(),
        name="config-features-admin-list",
    ),
    path(
        "config/admin/features/<str:key>/",
        FeatureFlagAdminDetailView.as_view(),
        name="config-features-admin-detail",
    ),
]
