"""
URL patterns for the doctor self-service mobile API. Included separately in
config/urls.py from `doctors.urls` (the public directory + admin Doctor
Management endpoints) so the two are never mixed in the same file, and so it
is obvious at a glance that nothing here is admin-reachable.
"""

from django.urls import path

from .me_views import (
    DoctorMeCertificationDetailView,
    DoctorMeCertificationListView,
    DoctorMeEducationDetailView,
    DoctorMeEducationListView,
    DoctorMeLanguageDetailView,
    DoctorMeLanguageListView,
    DoctorMeLeaveDetailView,
    DoctorMeLeaveListCreateView,
    DoctorMeQueueView,
    DoctorMeScheduleDetailView,
    DoctorMeScheduleListCreateView,
    DoctorMeView,
    DoctorMeVisitDetailView,
    DoctorMeVisitListView,
    DoctorMeVisitNotesView,
    DoctorMeVisitPrescriptionView,
    DoctorMeVisitReportUploadView,
)

urlpatterns = [
    path("doctors/me/", DoctorMeView.as_view(), name="doctor-me"),
    path(
        "doctors/me/certifications/",
        DoctorMeCertificationListView.as_view(),
        name="doctor-me-certification-list",
    ),
    path(
        "doctors/me/certifications/<int:pk>/",
        DoctorMeCertificationDetailView.as_view(),
        name="doctor-me-certification-detail",
    ),
    path(
        "doctors/me/languages/",
        DoctorMeLanguageListView.as_view(),
        name="doctor-me-language-list",
    ),
    path(
        "doctors/me/languages/<int:pk>/",
        DoctorMeLanguageDetailView.as_view(),
        name="doctor-me-language-detail",
    ),
    path(
        "doctors/me/education/",
        DoctorMeEducationListView.as_view(),
        name="doctor-me-education-list",
    ),
    path(
        "doctors/me/education/<int:pk>/",
        DoctorMeEducationDetailView.as_view(),
        name="doctor-me-education-detail",
    ),
    path(
        "doctors/me/schedule/",
        DoctorMeScheduleListCreateView.as_view(),
        name="doctor-me-schedule-list",
    ),
    path(
        "doctors/me/schedule/<int:pk>/",
        DoctorMeScheduleDetailView.as_view(),
        name="doctor-me-schedule-detail",
    ),
    path(
        "doctors/me/leaves/",
        DoctorMeLeaveListCreateView.as_view(),
        name="doctor-me-leave-list",
    ),
    path(
        "doctors/me/leaves/<int:pk>/",
        DoctorMeLeaveDetailView.as_view(),
        name="doctor-me-leave-detail",
    ),
    path("doctors/me/queue/", DoctorMeQueueView.as_view(), name="doctor-me-queue"),
    path(
        "doctors/me/visits/",
        DoctorMeVisitListView.as_view(),
        name="doctor-me-visit-list",
    ),
    path(
        "doctors/me/visits/<int:pk>/",
        DoctorMeVisitDetailView.as_view(),
        name="doctor-me-visit-detail",
    ),
    path(
        "doctors/me/visits/<int:pk>/notes/",
        DoctorMeVisitNotesView.as_view(),
        name="doctor-me-visit-notes",
    ),
    path(
        "doctors/me/visits/<int:pk>/prescription/",
        DoctorMeVisitPrescriptionView.as_view(),
        name="doctor-me-visit-prescription",
    ),
    path(
        "doctors/me/visits/<int:pk>/reports/",
        DoctorMeVisitReportUploadView.as_view(),
        name="doctor-me-visit-reports",
    ),
]
