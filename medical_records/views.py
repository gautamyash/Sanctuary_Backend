"""
EMR endpoints (Feature 5). All additive and independent of booking, queue,
attendance, waitlist, and duration-prediction logic.
"""

from django.contrib.auth import get_user_model
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
    VitalSigns,
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
    PatientRecordUpdateSerializer,
    PrescriptionSerializer,
    PrescriptionWriteSerializer,
    VitalSignsSerializer,
)
from .services import MedicalTimelineService

User = get_user_model()


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


class PrescriptionDetailView(APIView):
    """PATCH/DELETE /api/records/prescriptions/{id}/ — admin/staff edit or
    removal of a single prescription line (Phase: Admin Prescription
    Management). Gated on "emr.prescription" — the same permission
    VisitPrescriptionView's create already requires, reused rather than
    switching to "emr.edit" so prescription writes stay under one
    resource-specific permission end to end.

    Mirrors AllergyDetailView/MedicationDetailView's exact pattern: PATCH
    reuses PrescriptionWriteSerializer (the same serializer
    VisitPrescriptionView already uses for input) with `partial=True`, and
    DELETE is a plain removal. A Prescription is a leaf row under a
    MedicalVisit — unlike the visit itself, deleting one doesn't cascade
    into destroying any other clinical record, so it fits the same
    business rules as Allergy/Medication delete."""

    permission_classes = [PermissionRequired]
    permission_code = "emr.prescription"

    def patch(self, request, pk):
        prescription = get_object_or_404(Prescription, pk=pk)
        serializer = PrescriptionWriteSerializer(
            prescription, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(PrescriptionSerializer(prescription).data)

    def delete(self, request, pk):
        prescription = get_object_or_404(Prescription, pk=pk)
        prescription.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


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


class LabReportDetailView(APIView):
    """PATCH/DELETE /api/records/reports/{id}/ — admin/staff edit or removal
    of a single lab report (Phase: Admin Lab Report Management).

    LabReport only has title/file/uploaded_at/uploaded_by as real fields —
    there is no test_name, status, ordered_date, result_date, or summary
    concept anywhere in the schema, so none of those are invented here.
    PATCH reuses LabReportSerializer (the same serializer
    VisitReportUploadView's response already uses) with `partial=True`;
    since `id`/`file_url`/`uploaded_at` are all read-only on that serializer
    (pk, a SerializerMethodField, and an auto_now_add field respectively),
    this naturally restricts editing to `title` only — no separate write
    serializer needed, and no risk of silently accepting fields the model
    doesn't support.

    Edit is gated on "emr.edit" (the same general EMR-edit permission used
    by Allergy/Medication/Visit-notes edits) rather than "emr.upload" —
    "emr.upload" is granted to the Lab Technician role specifically for
    *creating* reports, and editing report metadata afterward is a broader
    EMR-edit action, not a re-upload.

    Delete is gated on "emr.delete" ("Delete EMR Records") — a permission
    code that already exists in the RBAC seed data but was not yet used by
    any view. A LabReport is a leaf row under a MedicalVisit (its FK is not
    unique, so a visit can have many reports, and removing one doesn't
    cascade into destroying any other clinical record), so it fits the same
    business rules as Prescription/Allergy/Medication delete. Using the
    dedicated "emr.delete" code instead of "emr.edit" or "emr.upload" keeps
    deleting an uploaded medical document — arguably a more sensitive action
    than editing its title — under its own, already-provisioned permission
    rather than folding it into a broader one."""

    def get_permissions(self):
        self.permission_code = (
            "emr.delete" if self.request.method == "DELETE" else "emr.edit"
        )
        return [PermissionRequired()]

    def patch(self, request, pk):
        report = get_object_or_404(LabReport, pk=pk)
        serializer = LabReportSerializer(
            report, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        report = get_object_or_404(LabReport, pk=pk)
        report.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class VisitVitalsView(APIView):
    """PATCH /api/records/visits/{id}/vitals/ — admin/staff edit (and,
    implicitly, first-time creation) of a visit's vital signs (Phase: Admin
    Vital Signs Management).

    VitalSigns has a `OneToOneField` to MedicalVisit (unlike Prescription/
    LabReport's plain FK) — at most one row per visit, and nothing
    auto-creates it (no signal, unlike MedicalVisit's own auto-creation on
    appointment completion). So a visit may exist with zero VitalSigns rows.
    This endpoint reuses the same get-or-create shape `_record_for()`
    already established for PatientRecord (another OneToOneField-adjacent,
    not-auto-created resource): `VitalSigns.objects.get_or_create(
    medical_visit=visit)`, then updates via the existing VitalSignsSerializer
    with `partial=True`. This is deliberately the *only* endpoint — it covers
    both "Create" and "Edit" in one action, which is exactly what the phase
    requirements ask for ("if VitalSigns are auto-created or one-to-one,
    reuse that flow instead of creating duplicates"): a separate create
    endpoint would only risk attempting a second row, which the
    OneToOneField would reject anyway.

    Gated on "emr.edit" — the same general EMR-edit permission already used
    by Allergy/Medication/Visit-notes/LabReport-title edits. There is no
    vitals-specific RBAC code, and this isn't a distinct enough action to
    warrant one.

    No BMI field is exposed or invented: BMI does not exist on VitalSigns at
    all — it is only a derived property on the unrelated PatientRecord model
    (computed from that model's own height_cm/weight_kg there, a
    patient-level record, not a per-visit one).

    No DELETE: every field on VitalSigns is nullable/blank-friendly, so
    clearing a visit's vitals is already fully achievable through this same
    PATCH (send nulls/blank values). A dedicated delete would only differ in
    removing the row itself rather than zeroing its values — a distinction
    the requirements don't call for. Unlike Prescription/LabReport (leaf
    rows with siblings on the same visit), VitalSigns is a singleton per
    visit, so "deleting one of several" doesn't apply here the way it does
    for those resources. Reported rather than implemented."""

    permission_classes = [PermissionRequired]
    permission_code = "emr.edit"

    def patch(self, request, pk):
        visit = get_object_or_404(MedicalVisit, pk=pk)
        vitals, _ = VitalSigns.objects.get_or_create(medical_visit=visit)
        serializer = VitalSignsSerializer(vitals, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


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


# --------------------------------------------------------------------------- #
# Admin/staff Allergy + Medication CRUD (Phase: Admin Patient Clinical
# Management). AllergyCreateView/MedicationCreateView above are self-service
# only — they resolve the record from `request.user`, so they can never be
# used by an admin managing a *different* patient's record, and neither
# resource had any edit or delete endpoint at all. These views are the
# admin-scoped counterpart, mirroring PatientRecordDetailView's own pattern
# (per-method "emr.edit" gate, `_record_for()` for the create side, the same
# AllergySerializer/AllergyWriteSerializer and MedicationSerializer/
# MedicationWriteSerializer pairs already used by the self-service views
# above). No new model fields, no new serializers, no duplicated CRUD — the
# self-service endpoints are untouched and keep serving the mobile app.
# --------------------------------------------------------------------------- #
class PatientAllergyCreateView(APIView):
    """POST /api/records/patients/{patient_id}/allergies/ — admin/staff adds
    an allergy to a specific patient's record. Gated on "emr.edit"."""

    permission_classes = [PermissionRequired]
    permission_code = "emr.edit"

    def post(self, request, patient_id):
        patient = get_object_or_404(User, pk=patient_id)
        record = _record_for(patient)
        serializer = AllergyWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        allergy = serializer.save(patient_record=record)
        track("allergy_added", allergy=allergy.id)
        return Response(
            AllergySerializer(allergy).data, status=status.HTTP_201_CREATED
        )


class AllergyDetailView(APIView):
    """PATCH/DELETE /api/records/allergies/{id}/ — admin/staff edit or
    removal of a single allergy entry. Gated on "emr.edit". PATCH reuses
    AllergyWriteSerializer (the same serializer AllergyCreateView/
    PatientAllergyCreateView already use for input), just with
    `partial=True` for edits."""

    permission_classes = [PermissionRequired]
    permission_code = "emr.edit"

    def patch(self, request, pk):
        allergy = get_object_or_404(Allergy, pk=pk)
        serializer = AllergyWriteSerializer(allergy, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(AllergySerializer(allergy).data)

    def delete(self, request, pk):
        allergy = get_object_or_404(Allergy, pk=pk)
        allergy.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PatientMedicationCreateView(APIView):
    """POST /api/records/patients/{patient_id}/medications/ — admin/staff
    adds a medication to a specific patient's record. Gated on "emr.edit"."""

    permission_classes = [PermissionRequired]
    permission_code = "emr.edit"

    def post(self, request, patient_id):
        patient = get_object_or_404(User, pk=patient_id)
        record = _record_for(patient)
        serializer = MedicationWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        medication = serializer.save(patient_record=record)
        track("medication_added", medication=medication.id)
        return Response(
            MedicationSerializer(medication).data, status=status.HTTP_201_CREATED
        )


class MedicationDetailView(APIView):
    """PATCH/DELETE /api/records/medications/{id}/ — admin/staff edit or
    removal of a single medication entry. Gated on "emr.edit"."""

    permission_classes = [PermissionRequired]
    permission_code = "emr.edit"

    def patch(self, request, pk):
        medication = get_object_or_404(Medication, pk=pk)
        serializer = MedicationWriteSerializer(
            medication, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(MedicationSerializer(medication).data)

    def delete(self, request, pk):
        medication = get_object_or_404(Medication, pk=pk)
        medication.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


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


class PatientRecordDetailView(APIView):
    """GET   /api/records/patients/{patient_id}/ — a specific patient's health
                                                     record, for staff.
    PATCH /api/records/patients/{patient_id}/ — admin/staff edit of that
                                                  record's editable fields.

    GET requires "emr.view" (unchanged); PATCH requires "emr.edit" — the
    same per-method permission_code switch already used by UserListView/
    UserDetailView in the authorization app, reused rather than reinvented.

    Both reuse PatientRecordSerializer and the existing _record_for() helper
    (get-or-create, never 404s even if the record hasn't been touched yet),
    so PATCH behaves exactly like GET for locating the record, just with a
    write afterward. PATCH is deliberately scoped to whatever
    PatientRecordUpdateSerializer exposes today (blood_group only) — see
    that serializer for how to extend it later.
    """

    permission_classes = [PermissionRequired]

    def get_permissions(self):
        self.permission_code = (
            "emr.view" if self.request.method == "GET" else "emr.edit"
        )
        return [PermissionRequired()]

    def get(self, request, patient_id):
        patient = get_object_or_404(User, pk=patient_id)
        record = _record_for(patient)
        return Response(
            PatientRecordSerializer(record, context={"request": request}).data
        )

    def patch(self, request, patient_id):
        patient = get_object_or_404(User, pk=patient_id)
        record = _record_for(patient)
        serializer = PatientRecordUpdateSerializer(
            record, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            PatientRecordSerializer(record, context={"request": request}).data
        )


class PatientVisitListView(APIView):
    """GET /api/records/patients/{patient_id}/visits/ — a specific patient's
    visit history, for staff. Requires "emr.view".

    Mirrors VisitListView's search params, scoped to the target patient.
    """

    permission_classes = [PermissionRequired]
    permission_code = "emr.view"

    def get(self, request, patient_id):
        patient = get_object_or_404(User, pk=patient_id)
        qs = (
            MedicalVisit.objects.filter(patient=patient)
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
        return Response(
            {
                "results": MedicalVisitSerializer(
                    qs, many=True, context={"request": request}
                ).data
            }
        )


class PatientTimelineView(APIView):
    """GET /api/records/patients/{patient_id}/timeline/ — a specific patient's
    clinical timeline, for staff. Requires "emr.view".

    Reuses MedicalTimelineService.build_for_patient().
    """

    permission_classes = [PermissionRequired]
    permission_code = "emr.view"

    def get(self, request, patient_id):
        patient = get_object_or_404(User, pk=patient_id)
        events = MedicalTimelineService.build_for_patient(patient)
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
