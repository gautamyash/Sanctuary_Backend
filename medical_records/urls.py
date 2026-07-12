from django.urls import path

from .views import (
    AllergyCreateView,
    MedicationCreateView,
    PatientRecordDetailView,
    PatientRecordMeView,
    PatientTimelineView,
    PatientVisitListView,
    RecordsAnalyticsView,
    TimelineView,
    VisitDetailView,
    VisitListView,
    VisitNotesView,
    VisitPrescriptionView,
    VisitReportUploadView,
)

urlpatterns = [
    path("records/me/", PatientRecordMeView.as_view(), name="record-me"),
    path(
        "records/patients/<int:patient_id>/",
        PatientRecordDetailView.as_view(),
        name="record-patient-detail",
    ),
    path(
        "records/patients/<int:patient_id>/visits/",
        PatientVisitListView.as_view(),
        name="record-patient-visits",
    ),
    path(
        "records/patients/<int:patient_id>/timeline/",
        PatientTimelineView.as_view(),
        name="record-patient-timeline",
    ),
    path("records/visits/", VisitListView.as_view(), name="record-visits"),
    path(
        "records/visits/<int:pk>/",
        VisitDetailView.as_view(),
        name="record-visit-detail",
    ),
    path(
        "records/visits/<int:pk>/notes/",
        VisitNotesView.as_view(),
        name="record-visit-notes",
    ),
    path(
        "records/visits/<int:pk>/prescriptions/",
        VisitPrescriptionView.as_view(),
        name="record-visit-prescriptions",
    ),
    path(
        "records/visits/<int:pk>/reports/",
        VisitReportUploadView.as_view(),
        name="record-visit-reports",
    ),
    path("records/allergies/", AllergyCreateView.as_view(), name="record-allergies"),
    path(
        "records/medications/",
        MedicationCreateView.as_view(),
        name="record-medications",
    ),
    path("records/timeline/", TimelineView.as_view(), name="record-timeline"),
    path(
        "analytics/records/",
        RecordsAnalyticsView.as_view(),
        name="records-analytics",
    ),
]
