"""
Doctor self-service mobile API — completely separate from the admin Doctor
Management endpoints in `views.py`/`urls.py`.

Every view here resolves the acting doctor as `request.user.doctor_profile`
and never accepts a doctor id from the client, so a doctor can only ever
read or write their own data. Admin endpoints (`admin/doctors/...`,
`doctors/<id>/schedules/...`, `doctors/<id>/leaves/...`, all requiring
`system.admin`) are untouched and are never reused or exposed here.
"""

from datetime import datetime

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .me_permissions import IsLinkedDoctor
from .models import Certification, Doctor, DoctorLeave, DoctorSchedule, Education, Language
from .serializers import (
    CertificationSerializer,
    DoctorMeLeaveSerializer,
    DoctorMeScheduleSerializer,
    DoctorMeSerializer,
    EducationSerializer,
    LanguageSerializer,
)


def _my_doctor(request) -> Doctor:
    """The Doctor row owned by the authenticated user. IsLinkedDoctor has
    already guaranteed this exists by the time a view body runs."""
    return request.user.doctor_profile


class DoctorMeView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/doctors/me/ — the authenticated doctor's own profile."""

    serializer_class = DoctorMeSerializer
    permission_classes = [permissions.IsAuthenticated, IsLinkedDoctor]

    def get_object(self):
        return _my_doctor(self.request)


class _MeSubResourceListCreateView(generics.ListCreateAPIView):
    """Shared base for the small owned collections (certifications,
    languages, education) hanging off the doctor's own profile."""

    permission_classes = [permissions.IsAuthenticated, IsLinkedDoctor]
    model = None  # set by subclass

    def get_queryset(self):
        return self.model.objects.filter(doctor=_my_doctor(self.request))

    def perform_create(self, serializer):
        serializer.save(doctor=_my_doctor(self.request))


class _MeSubResourceDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Shared base for retrieving/editing/deleting a single owned entry,
    scoped so a doctor can never touch another doctor's row."""

    permission_classes = [permissions.IsAuthenticated, IsLinkedDoctor]
    model = None  # set by subclass

    def get_queryset(self):
        return self.model.objects.filter(doctor=_my_doctor(self.request))


class DoctorMeCertificationListView(_MeSubResourceListCreateView):
    """GET/POST /api/doctors/me/certifications/"""

    serializer_class = CertificationSerializer
    model = Certification


class DoctorMeCertificationDetailView(_MeSubResourceDetailView):
    """GET/PATCH/DELETE /api/doctors/me/certifications/{id}/"""

    serializer_class = CertificationSerializer
    model = Certification


class DoctorMeLanguageListView(_MeSubResourceListCreateView):
    """GET/POST /api/doctors/me/languages/"""

    serializer_class = LanguageSerializer
    model = Language


class DoctorMeLanguageDetailView(_MeSubResourceDetailView):
    """GET/PATCH/DELETE /api/doctors/me/languages/{id}/"""

    serializer_class = LanguageSerializer
    model = Language


class DoctorMeEducationListView(_MeSubResourceListCreateView):
    """GET/POST /api/doctors/me/education/"""

    serializer_class = EducationSerializer
    model = Education


class DoctorMeEducationDetailView(_MeSubResourceDetailView):
    """GET/PATCH/DELETE /api/doctors/me/education/{id}/"""

    serializer_class = EducationSerializer
    model = Education


class DoctorMeScheduleListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/doctors/me/schedule/ — the doctor's own weekly working
    hours. Same DoctorSchedule model the admin panel manages via
    `doctors/<id>/schedules/`, but scoped to the caller and reachable only
    through this separate self-service path."""

    serializer_class = DoctorMeScheduleSerializer
    permission_classes = [permissions.IsAuthenticated, IsLinkedDoctor]

    def get_queryset(self):
        return DoctorSchedule.objects.filter(doctor=_my_doctor(self.request))

    def perform_create(self, serializer):
        serializer.save(doctor=_my_doctor(self.request))


class DoctorMeScheduleDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/doctors/me/schedule/{id}/"""

    serializer_class = DoctorMeScheduleSerializer
    permission_classes = [permissions.IsAuthenticated, IsLinkedDoctor]

    def get_queryset(self):
        return DoctorSchedule.objects.filter(doctor=_my_doctor(self.request))


class DoctorMeLeaveListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/doctors/me/leaves/ — the doctor's own leave requests.
    Creating always starts a request at status=pending; only staff (via the
    existing admin leave endpoints) can approve/reject it."""

    serializer_class = DoctorMeLeaveSerializer
    permission_classes = [permissions.IsAuthenticated, IsLinkedDoctor]

    def get_queryset(self):
        return DoctorLeave.objects.filter(doctor=_my_doctor(self.request))

    def perform_create(self, serializer):
        serializer.save(doctor=_my_doctor(self.request), status=DoctorLeave.Status.PENDING)


class DoctorMeLeaveDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/doctors/me/leaves/{id}/ — a doctor may edit or
    withdraw their own request only while it is still pending."""

    serializer_class = DoctorMeLeaveSerializer
    permission_classes = [permissions.IsAuthenticated, IsLinkedDoctor]

    def get_queryset(self):
        return DoctorLeave.objects.filter(doctor=_my_doctor(self.request))

    def perform_update(self, serializer):
        if serializer.instance.status != DoctorLeave.Status.PENDING:
            from rest_framework.exceptions import ValidationError

            raise ValidationError("Only a pending leave request can be edited.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.status != DoctorLeave.Status.PENDING:
            from rest_framework.exceptions import ValidationError

            raise ValidationError("Only a pending leave request can be withdrawn.")
        instance.delete()


class DoctorMeQueueView(APIView):
    """GET /api/doctors/me/queue/?date=YYYY-MM-DD — the authenticated
    doctor's own live queue/timeline for the day. Same read the admin
    `doctors/{id}/queue/` endpoint exposes (QueueService.build_timeline),
    but scoped to the caller and reachable only through this separate
    self-service path — the admin endpoint is untouched."""

    permission_classes = [permissions.IsAuthenticated, IsLinkedDoctor]

    def get(self, request):
        from queues.services import QueueService

        doctor = _my_doctor(request)
        date_str = request.query_params.get("date")
        try:
            day = (
                datetime.strptime(date_str, "%Y-%m-%d").date()
                if date_str
                else timezone.localdate()
            )
        except ValueError:
            return Response({"detail": "Invalid date, use YYYY-MM-DD."}, status=400)
        return Response(QueueService.build_timeline(doctor, day))


# --------------------------------------------------------------------------- #
# EMR self-service (Consultation / Prescription / Upload Reports) — additive.
#
# Completely separate read/write surface from medical_records/views.py:
#   - VisitListView / VisitDetailView are scoped to patient=request.user.
#   - VisitNotesView / VisitPrescriptionView / VisitReportUploadView are
#     gated by admin RBAC (PermissionRequired: "emr.edit" / "emr.prescription"
#     / "emr.upload"), which a self-service doctor account does not hold.
# Neither family is reused, extended, or exposed here. Every view below is
# gated by IsLinkedDoctor only and every queryset is filtered to
# MedicalVisit.doctor == the caller's own Doctor row, so a doctor can never
# read or write another doctor's visit (404, not 403, on mismatch — same
# convention as the schedule/leave "me" sub-resources above).
# --------------------------------------------------------------------------- #


def _my_visits_qs(request):
    from medical_records.models import MedicalVisit

    return (
        MedicalVisit.objects.filter(doctor=_my_doctor(request))
        .select_related("appointment", "doctor", "patient", "vitals")
        .prefetch_related("prescriptions", "reports")
    )


def _my_visit_or_404(request, pk):
    return get_object_or_404(_my_visits_qs(request), pk=pk)


class DoctorMeVisitListView(generics.ListAPIView):
    """GET /api/doctors/me/visits/ — visits for the caller's own patients
    (MedicalVisit.doctor == the authenticated doctor). Supports the same
    ?date=/?q= filters as the patient- and admin-scoped visit lists for
    consistency, applied to this doctor's own queryset only."""

    permission_classes = [permissions.IsAuthenticated, IsLinkedDoctor]

    def get_serializer_class(self):
        from medical_records.serializers import DoctorVisitSerializer

        return DoctorVisitSerializer

    def get_queryset(self):
        from django.db.models import Q

        qs = _my_visits_qs(self.request)
        p = self.request.query_params
        if p.get("date"):
            qs = qs.filter(appointment__date=p["date"])
        if p.get("diagnosis"):
            qs = qs.filter(diagnosis__icontains=p["diagnosis"])
        if p.get("q"):
            term = p["q"]
            qs = qs.filter(
                Q(diagnosis__icontains=term)
                | Q(chief_complaint__icontains=term)
                | Q(patient__name__icontains=term)
            )
        return qs.distinct()


class DoctorMeVisitDetailView(generics.RetrieveAPIView):
    """GET /api/doctors/me/visits/{id}/"""

    permission_classes = [permissions.IsAuthenticated, IsLinkedDoctor]

    def get_serializer_class(self):
        from medical_records.serializers import DoctorVisitSerializer

        return DoctorVisitSerializer

    def get_queryset(self):
        return _my_visits_qs(self.request)


class DoctorMeVisitNotesView(APIView):
    """PATCH /api/doctors/me/visits/{id}/notes/ — the treating doctor's own
    consultation documentation (chief complaint, diagnosis, clinical notes,
    follow-up date) for one of their own visits. Reuses DoctorNotesSerializer
    and MedicalVisit from medical_records; does not touch
    medical_records.views.VisitNotesView (admin RBAC "emr.edit")."""

    permission_classes = [permissions.IsAuthenticated, IsLinkedDoctor]

    def patch(self, request, pk):
        from medical_records.serializers import (
            DoctorNotesSerializer,
            DoctorVisitSerializer,
        )

        visit = _my_visit_or_404(request, pk)
        serializer = DoctorNotesSerializer(visit, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        from appointments.analytics import track

        track("medical_notes_saved", visit=visit.id)
        if visit.follow_up_date:
            from appointments.notifications import NotificationService

            NotificationService.send_followup_reminder(visit)

        return Response(
            DoctorVisitSerializer(visit, context={"request": request}).data
        )


class DoctorMeVisitPrescriptionView(APIView):
    """PATCH /api/doctors/me/visits/{id}/prescription/ — add a prescription
    line to one of the caller's own visits. Reuses the existing Prescription
    model and PrescriptionWriteSerializer from medical_records; does not
    touch medical_records.views.VisitPrescriptionView (admin RBAC
    "emr.prescription"). A visit may carry more than one prescription line
    (unchanged model shape), so repeated PATCH calls add further lines."""

    permission_classes = [permissions.IsAuthenticated, IsLinkedDoctor]

    def patch(self, request, pk):
        from medical_records.serializers import (
            PrescriptionSerializer,
            PrescriptionWriteSerializer,
        )

        visit = _my_visit_or_404(request, pk)
        serializer = PrescriptionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        prescription = serializer.save(medical_visit=visit)

        from appointments.analytics import track
        from appointments.notifications import NotificationService

        track("prescription_added", visit=visit.id, medicine=prescription.medicine)
        NotificationService.send_prescription_ready(visit)

        return Response(
            PrescriptionSerializer(prescription).data,
            status=status.HTTP_201_CREATED,
        )


class DoctorMeVisitReportUploadView(APIView):
    """POST /api/doctors/me/visits/{id}/reports/ — multipart lab-report
    upload to one of the caller's own visits. Reuses the existing LabReport
    model and LabReportUploadSerializer from medical_records; does not touch
    medical_records.views.VisitReportUploadView (owner-or-RBAC "emr.upload")."""

    permission_classes = [permissions.IsAuthenticated, IsLinkedDoctor]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk):
        from medical_records.serializers import (
            LabReportSerializer,
            LabReportUploadSerializer,
        )

        visit = _my_visit_or_404(request, pk)
        serializer = LabReportUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report = serializer.save(medical_visit=visit, uploaded_by=request.user)

        from appointments.analytics import track
        from appointments.notifications import NotificationService

        track("lab_report_uploaded", visit=visit.id, report=report.id)
        NotificationService.send_lab_report_uploaded(report)

        return Response(
            LabReportSerializer(report, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )
