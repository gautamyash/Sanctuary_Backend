"""
EMR endpoints (Feature 5). All additive and independent of booking, queue,
attendance, waitlist, and duration-prediction logic.
"""

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from appointments.analytics import track
from appointments.models import Appointment
from appointments.notifications import NotificationService
from authorization.permissions import PermissionRequired
from authorization.services import PermissionService

from .models import (
    Allergy,
    LabReport,
    Medication,
    MedicalVisit,
    PatientRecord,
    Prescription,
)
from .serializers import (
    AllergySerializer,
    AllergyWriteSerializer,
    LabReportSerializer,
    LabReportUploadSerializer,
    MedicalVisitSerializer,
    MedicationSerializer,
    MedicationWriteSerializer,
    DoctorNotesSerializer,
    PatientRecordSerializer,
    PrescriptionSerializer,
    PrescriptionWriteSerializer,
)
from .services import MedicalTimelineService


def _record_for(user) -> PatientRecord:
    record, _ = PatientRecord.objects.get_or_create(patient=user)
    return record


def _visit_for(pk, user, permission_code=None):
    """A visit the user may read: their own, or any if they hold
    `permission_code` under RBAC (replaces the previous is_staff-wide
    check, same "any visit" behavior)."""
    qs = MedicalVisit.objects.select_related(
        "appointment", "doctor", "patient", "vitals"
    ).prefetch_related("prescriptions", "reports")
    if permission_code and PermissionService.has_permission(user, permission_code):
        return qs.filter(pk=pk).first()
    return qs.filter(pk=pk, patient=user).first()


class PatientRecordMeView(APIView):
    """GET /api/records/me/ — the authenticated patient's health record."""

    def get(self, request):
        record = _record_for(request.user)
        return Response(
            PatientRecordSerializer(record, context={"request": request}).data
        )


class VisitListView(APIView):
    """GET /api/records/visits/ — the patient's visit history.

    Search: ?q= / ?diagnosis= / ?medicine= / ?doctor= / ?date=YYYY-MM-DD
    """

    def get(self, request):
        qs = (
            MedicalVisit.objects.filter(patient=request.user)
            .select_related("appointment", "doctor")
            .prefetch_related("prescriptions", "reports", "vitals")
        )
        p = request.query_params
        if p.get("diagnosis"):
            qs = qs.filter(diagnosis__icontains=p["diagnosis"])
        if p.get("medicine"):
            qs = qs.filter(prescriptions__medicine__icontains=p["medicine"])
        if p.get("doctor"):
            qs = qs.filter(doctor__name__icontains=p["doctor"])
        if p.get("date"):
            qs = qs.filter(appointment__date=p["date"])
        if p.get("q"):
            term = p["q"]
            qs = qs.filter(
                Q(diagnosis__icontains=term)
                | Q(chief_complaint__icontains=term)
                | Q(doctor__name__icontains=term)
                | Q(prescriptions__medicine__icontains=term)
            )
        qs = qs.distinct()
        data = MedicalVisitSerializer(
            qs, many=True, context={"request": request}
        ).data
        return Response({"results": data})


class VisitDetailView(APIView):
    """GET /api/records/visits/{id}/"""

    def get(self, request, pk):
        visit = _visit_for(pk, request.user, "emr.view")
        if visit is None:
            return Response({"detail": "Not found."}, status=404)
        return Response(
            MedicalVisitSerializer(visit, context={"request": request}).data
        )


class VisitNotesView(APIView):
    """POST /api/records/visits/{id}/notes/ — staff clinical documentation."""

    permission_classes = [PermissionRequired]
    permission_code = "emr.edit"

    def post(self, request, pk):
        visit = get_object_or_404(MedicalVisit, pk=pk)
        serializer = DoctorNotesSerializer(visit, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        track("medical_notes_saved", visit=visit.id)
        if visit.follow_up_date:
            NotificationService.send_followup_reminder(visit)
        return Response(
            MedicalVisitSerializer(visit, context={"request": request}).data
        )


class VisitPrescriptionView(APIView):
    """POST /api/records/visits/{id}/prescriptions/ — staff adds a prescription."""

    permission_classes = [PermissionRequired]
    permission_code = "emr.prescription"

    def post(self, request, pk):
        visit = get_object_or_404(MedicalVisit, pk=pk)
        serializer = PrescriptionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        prescription = serializer.save(medical_visit=visit)
        track("prescription_added", visit=visit.id, medicine=prescription.medicine)
        NotificationService.send_prescription_ready(visit)
        return Response(
            PrescriptionSerializer(prescription).data,
            status=status.HTTP_201_CREATED,
        )


class VisitReportUploadView(APIView):
    """POST /api/records/visits/{id}/reports/ — multipart lab-report upload.

    Owner or staff may upload."""

    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk):
        visit = _visit_for(pk, request.user, "emr.upload")
        if visit is None:
            return Response({"detail": "Not found."}, status=404)

        serializer = LabReportUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report = serializer.save(
            medical_visit=visit, uploaded_by=request.user
        )
        track("lab_report_uploaded", visit=visit.id, report=report.id)
        NotificationService.send_lab_report_uploaded(report)
        return Response(
            LabReportSerializer(report, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class AllergyCreateView(APIView):
    """POST /api/records/allergies/ — add an allergy to the caller's record."""

    def post(self, request):
        record = _record_for(request.user)
        serializer = AllergyWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        allergy = serializer.save(patient_record=record)
        track("allergy_added", allergy=allergy.id)
        return Response(
            AllergySerializer(allergy).data, status=status.HTTP_201_CREATED
        )


class MedicationCreateView(APIView):
    """POST /api/records/medications/ — add a medication to the caller's record."""

    def post(self, request):
        record = _record_for(request.user)
        serializer = MedicationWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        medication = serializer.save(patient_record=record)
        track("medication_added", medication=medication.id)
        return Response(
            MedicationSerializer(medication).data, status=status.HTTP_201_CREATED
        )


class TimelineView(APIView):
    """GET /api/records/timeline/ — the caller's clinical timeline.

    ?visit=<id> restricts to a single visit's events."""

    def get(self, request):
        visit_id = request.query_params.get("visit")
        if visit_id:
            visit = _visit_for(visit_id, request.user, "emr.view")
            if visit is None:
                return Response({"detail": "Not found."}, status=404)
            events = MedicalTimelineService.build_for_visit(visit)
        else:
            events = MedicalTimelineService.build_for_patient(request.user)
        return Response({"timeline": events})


class RecordsAnalyticsView(APIView):
    """GET /api/analytics/records/ — staff-only EMR analytics."""

    permission_classes = [PermissionRequired]
    permission_code = "analytics.view"

    def get(self, request):
        visits = MedicalVisit.objects.all()
        total_visits = visits.count()

        common_diagnoses = list(
            visits.exclude(diagnosis="")
            .values("diagnosis")
            .annotate(count=Count("id"))
            .order_by("-count")[:5]
        )

        # Repeat conditions: (patient, diagnosis) pairs seen more than once.
        repeat_conditions = (
            visits.exclude(diagnosis="")
            .values("patient", "diagnosis")
            .annotate(count=Count("id"))
            .filter(count__gt=1)
            .count()
        )

        medicine_usage = list(
            Prescription.objects.values("medicine")
            .annotate(count=Count("id"))
            .order_by("-count")[:5]
        )

        with_followup = visits.exclude(follow_up_date__isnull=True).count()
        # Compliance: a follow-up counts as met if the patient has another visit
        # created on/after the follow-up date.
        met = 0
        for v in visits.exclude(follow_up_date__isnull=True):
            if MedicalVisit.objects.filter(
                patient=v.patient, created_at__date__gte=v.follow_up_date
            ).exclude(pk=v.pk).exists():
                met += 1
        follow_up_compliance = (
            round(met / with_followup * 100, 1) if with_followup else 0.0
        )

        # Documentation completion: visits with both diagnosis and notes.
        documented = visits.exclude(diagnosis="").exclude(clinical_notes="").count()
        documentation_completion = (
            round(documented / total_visits * 100, 1) if total_visits else 0.0
        )

        return Response(
            {
                "total_visits": total_visits,
                "common_diagnoses": common_diagnoses,
                "repeat_conditions": repeat_conditions,
                "medicine_usage": medicine_usage,
                "follow_up_compliance": follow_up_compliance,
                "documentation_completion": documentation_completion,
            }
        )
