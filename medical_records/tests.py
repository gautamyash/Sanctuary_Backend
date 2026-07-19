"""
Regression tests for Feature 5 — Electronic Medical Records (EMR).

Covers record/visit/prescription/allergy/medication/report CRUD, auto visit
creation on completion, timeline generation, analytics, and explicit
"existing systems unchanged" checks (booking, queue, attendance, duration
prediction, waitlist).
"""

import tempfile
from datetime import time, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from appointments.models import Appointment, VisitType, WaitlistEntry
from authorization.models import Role, UserRole
from doctors.models import Doctor, DoctorSchedule, Specialty
from medical_records.models import (
    Allergy,
    LabReport,
    Medication,
    MedicalVisit,
    PatientRecord,
    Prescription,
    VitalSigns,
)

MEDIA = tempfile.mkdtemp()


class Base(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.specialty = Specialty.objects.create(name="Cardiology")
        cls.doctor = Doctor.objects.create(
            name="Dr. Mitchell", specialty=cls.specialty, hospital="St. Mary's"
        )
        for wd in range(7):
            DoctorSchedule.objects.create(
                doctor=cls.doctor, weekday=wd,
                start_time=time(8, 0), end_time=time(20, 0), slot_minutes=30,
            )
        cls.patient = User.objects.create_user(
            email="pat@example.com", password="x", name="Pat"
        )
        cls.other = User.objects.create_user(
            email="other@example.com", password="x", name="Other"
        )
        # RBAC replaces the old is_staff-based "staff" persona: Admin holds
        # every permission except system.admin, matching the previous
        # is_staff=True "can do all staff-level actions" intent.
        cls.staff = User.objects.create_user(
            email="doc@example.com", password="x", name="Doc"
        )
        UserRole.objects.create(user=cls.staff, role=Role.objects.get(name="Admin"))
        cls.visit_type = VisitType.objects.create(
            name="Follow-up", default_duration=20
        )

    def _appt(self, patient=None, day=None, t=time(10, 0),
              status=Appointment.Status.CONFIRMED):
        return Appointment.objects.create(
            doctor=self.doctor,
            patient=patient or self.patient,
            date=day or timezone.localdate(),
            time=t,
            estimated_duration=30,
            status=status,
        )

    def _visit(self, patient=None, **kwargs):
        appt = self._appt(patient=patient)
        appt.status = Appointment.Status.COMPLETED
        appt.save(update_fields=["status", "updated_at"])
        visit = MedicalVisit.objects.get(appointment=appt)
        for k, v in kwargs.items():
            setattr(visit, k, v)
        if kwargs:
            visit.save()
        return visit


# --------------------------------------------------------------------------- #
# Records, visits, auto-creation
# --------------------------------------------------------------------------- #
class RecordTests(Base):
    def test_records_me_autocreates(self):
        self.assertFalse(PatientRecord.objects.filter(patient=self.patient).exists())
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("record-me"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(PatientRecord.objects.filter(patient=self.patient).exists())
        self.assertIn("allergies", resp.data)
        self.assertIn("medications", resp.data)

    def test_bmi_is_derived(self):
        record = PatientRecord.objects.create(
            patient=self.patient, height_cm=180, weight_kg=81
        )
        self.assertEqual(record.bmi, 25.0)


class AutoVisitTests(Base):
    def test_patient_cannot_complete_own_appointment(self):
        appt = self._appt()
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("appointment-complete", args=[appt.id]),
            {"actual_minutes": 20}, format="json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(MedicalVisit.objects.filter(appointment=appt).exists())

    def test_anonymous_user_cannot_complete_appointment(self):
        appt = self._appt()
        resp = self.client.post(
            reverse("appointment-complete", args=[appt.id]),
            {"actual_minutes": 20}, format="json",
        )
        # Genuinely anonymous (no force_authenticate): JWTAuthentication
        # finds no credentials, so request.successful_authenticator stays
        # None and DRF's APIView.permission_denied() raises NotAuthenticated
        # (401), not PermissionDenied (403).
        self.assertEqual(resp.status_code, 401)

    def test_staff_can_complete_appointment(self):
        appt = self._appt()
        self.client.force_authenticate(self.staff)
        resp = self.client.post(
            reverse("appointment-complete", args=[appt.id]),
            {"actual_minutes": 20}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(MedicalVisit.objects.filter(appointment=appt).exists())
        visit = MedicalVisit.objects.get(appointment=appt)
        self.assertEqual(visit.patient_id, self.patient.id)
        self.assertEqual(visit.doctor_id, self.doctor.id)

    def test_visit_creation_is_idempotent(self):
        appt = self._appt()
        appt.status = Appointment.Status.COMPLETED
        appt.save(update_fields=["status", "updated_at"])
        appt.save(update_fields=["status", "updated_at"])  # second save
        self.assertEqual(
            MedicalVisit.objects.filter(appointment=appt).count(), 1
        )


class VisitApiTests(Base):
    def test_list_and_detail(self):
        visit = self._visit(diagnosis="Hypertension")
        self.client.force_authenticate(self.patient)
        lst = self.client.get(reverse("record-visits"))
        self.assertEqual(lst.status_code, 200)
        self.assertEqual(len(lst.data["results"]), 1)
        det = self.client.get(reverse("record-visit-detail", args=[visit.id]))
        self.assertEqual(det.status_code, 200)
        self.assertEqual(det.data["diagnosis"], "Hypertension")
        self.assertIn("vitals", det.data)
        self.assertIn("prescriptions", det.data)

    def test_cannot_read_others_visit(self):
        visit = self._visit()
        self.client.force_authenticate(self.other)
        resp = self.client.get(reverse("record-visit-detail", args=[visit.id]))
        self.assertEqual(resp.status_code, 404)

    def test_search_by_diagnosis(self):
        self._visit(diagnosis="Migraine")
        self._visit(diagnosis="Asthma")
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("record-visits"), {"diagnosis": "migr"})
        self.assertEqual(len(resp.data["results"]), 1)

    # --- Phase: Admin Medical Visit Management — MedicalVisitSerializer
    # extended with read-only visit_type/status, sourced from the related
    # Appointment (no such fields exist on MedicalVisit itself). ----------

    def test_visit_detail_exposes_visit_type_and_status(self):
        visit = self._visit(diagnosis="Hypertension")
        visit.appointment.visit_type = self.visit_type
        visit.appointment.save(update_fields=["visit_type"])
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("record-visit-detail", args=[visit.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["visit_type"], self.visit_type.name)
        self.assertEqual(resp.data["status"], "completed")

    def test_visit_detail_visit_type_is_none_when_unset(self):
        visit = self._visit(diagnosis="Hypertension")
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("record-visit-detail", args=[visit.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data["visit_type"])


class NotesAndPrescriptionTests(Base):
    def test_staff_saves_notes(self):
        visit = self._visit()
        self.client.force_authenticate(self.staff)
        resp = self.client.post(
            reverse("record-visit-notes", args=[visit.id]),
            {"diagnosis": "Flu", "clinical_notes": "Rest and fluids"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        visit.refresh_from_db()
        self.assertEqual(visit.diagnosis, "Flu")

    def test_non_staff_cannot_save_notes(self):
        visit = self._visit()
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("record-visit-notes", args=[visit.id]),
            {"diagnosis": "Flu"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_staff_adds_prescription(self):
        visit = self._visit()
        self.client.force_authenticate(self.staff)
        resp = self.client.post(
            reverse("record-visit-prescriptions", args=[visit.id]),
            {"medicine": "Amoxicillin", "dosage": "500mg", "frequency": "3x daily"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(
            Prescription.objects.filter(medical_visit=visit).count(), 1
        )


class AllergyMedicationTests(Base):
    def test_add_allergy(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("record-allergies"),
            {"name": "Penicillin", "severity": "HIGH"}, format="json",
        )
        self.assertEqual(resp.status_code, 201)
        record = PatientRecord.objects.get(patient=self.patient)
        self.assertEqual(Allergy.objects.filter(patient_record=record).count(), 1)

    def test_add_medication(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("record-medications"),
            {"name": "Metformin", "dosage": "850mg", "active": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Medication.objects.count(), 1)


@override_settings(MEDIA_ROOT=MEDIA)
class ReportUploadTests(Base):
    def test_upload_report(self):
        visit = self._visit()
        self.client.force_authenticate(self.patient)
        upload = SimpleUploadedFile(
            "result.pdf", b"%PDF-1.4 fake", content_type="application/pdf"
        )
        resp = self.client.post(
            reverse("record-visit-reports", args=[visit.id]),
            {"title": "Blood panel", "file": upload},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertIn("file_url", resp.data)
        self.assertEqual(LabReport.objects.filter(medical_visit=visit).count(), 1)

    def test_valid_pdf_upload_is_accepted(self):
        visit = self._visit()
        self.client.force_authenticate(self.patient)
        upload = SimpleUploadedFile(
            "lab-result.pdf", b"%PDF-1.4 test", content_type="application/pdf"
        )
        resp = self.client.post(
            reverse("record-visit-reports", args=[visit.id]),
            {"title": "Lab report", "file": upload},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["title"], "Lab report")
        self.assertIn("file_url", resp.data)

    def test_valid_image_upload_is_accepted(self):
        visit = self._visit()
        self.client.force_authenticate(self.patient)
        upload = SimpleUploadedFile(
            "scan.jpg", b"\xFF\xD8\xFF fake", content_type="image/jpeg"
        )
        resp = self.client.post(
            reverse("record-visit-reports", args=[visit.id]),
            {"title": "Image report", "file": upload},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(LabReport.objects.filter(medical_visit=visit).count(), 1)

    def test_invalid_extension_is_rejected(self):
        visit = self._visit()
        self.client.force_authenticate(self.patient)
        upload = SimpleUploadedFile(
            "result.exe", b"%PDF-1.4 fake", content_type="application/pdf"
        )
        resp = self.client.post(
            reverse("record-visit-reports", args=[visit.id]),
            {"title": "Bad extension", "file": upload},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("file", resp.data)

    def test_invalid_mime_type_is_rejected(self):
        visit = self._visit()
        self.client.force_authenticate(self.patient)
        upload = SimpleUploadedFile(
            "result.pdf", b"%PDF-1.4 fake", content_type="text/plain"
        )
        resp = self.client.post(
            reverse("record-visit-reports", args=[visit.id]),
            {"title": "Bad mime", "file": upload},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("file", resp.data)

    def test_oversized_file_is_rejected(self):
        visit = self._visit()
        self.client.force_authenticate(self.patient)
        upload = SimpleUploadedFile(
            "result.pdf",
            b"%PDF-1.4 fake" + b"a" * (10 * 1024 * 1024 + 1),
            content_type="application/pdf",
        )
        resp = self.client.post(
            reverse("record-visit-reports", args=[visit.id]),
            {"title": "Oversized", "file": upload},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("file", resp.data)

    def test_empty_file_is_rejected(self):
        visit = self._visit()
        self.client.force_authenticate(self.patient)
        upload = SimpleUploadedFile(
            "empty.pdf", b"", content_type="application/pdf"
        )
        resp = self.client.post(
            reverse("record-visit-reports", args=[visit.id]),
            {"title": "Empty", "file": upload},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("file", resp.data)

    def test_existing_upload_flow_is_unchanged(self):
        visit = self._visit()
        self.client.force_authenticate(self.patient)
        upload = SimpleUploadedFile(
            "report.pdf", b"%PDF-1.4 fake", content_type="application/pdf"
        )
        resp = self.client.post(
            reverse("record-visit-reports", args=[visit.id]),
            {"title": "Flow preserved", "file": upload},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["title"], "Flow preserved")
        self.assertIn("file_url", resp.data)
        self.assertTrue(LabReport.objects.filter(medical_visit=visit).exists())


class TimelineTests(Base):
    def test_timeline_has_stages(self):
        visit = self._visit(diagnosis="Bronchitis", follow_up_date=timezone.localdate())
        Prescription.objects.create(medical_visit=visit, medicine="Azithromycin")
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("record-timeline"))
        self.assertEqual(resp.status_code, 200)
        stages = {e["stage"] for e in resp.data["timeline"]}
        self.assertIn("appointment", stages)
        self.assertIn("consultation", stages)
        self.assertIn("diagnosis", stages)
        self.assertIn("prescription", stages)
        self.assertIn("follow_up", stages)


class RecordsAnalyticsTests(Base):
    def test_staff_only(self):
        self.client.force_authenticate(self.patient)
        self.assertEqual(
            self.client.get(reverse("records-analytics")).status_code, 403
        )

    def test_payload(self):
        self._visit(diagnosis="Hypertension", clinical_notes="notes")
        self.client.force_authenticate(self.staff)
        resp = self.client.get(reverse("records-analytics"))
        self.assertEqual(resp.status_code, 200)
        for key in (
            "total_visits",
            "common_diagnoses",
            "repeat_conditions",
            "medicine_usage",
            "follow_up_compliance",
            "documentation_completion",
        ):
            self.assertIn(key, resp.data)


# --------------------------------------------------------------------------- #
# PatientRecordDetailView: GET (pre-existing, now also permission-switched)
# and PATCH (new, Phase: Backend Support for Remaining Patient Fields)
# --------------------------------------------------------------------------- #
class PatientRecordDetailViewTests(Base):
    def test_get_requires_authentication(self):
        resp = self.client.get(reverse("record-patient-detail", args=[self.patient.id]))
        self.assertEqual(resp.status_code, 401)

    def test_get_requires_emr_view(self):
        self.client.force_authenticate(self.other)
        resp = self.client.get(reverse("record-patient-detail", args=[self.patient.id]))
        self.assertEqual(resp.status_code, 403)

    def test_get_returns_record_shape_unchanged(self):
        """Locks in the pre-existing GET response shape now that this view
        also handles PATCH — the per-method permission switch must not
        affect GET's behavior or payload at all."""
        self.client.force_authenticate(self.staff)
        resp = self.client.get(reverse("record-patient-detail", args=[self.patient.id]))
        self.assertEqual(resp.status_code, 200)
        for key in (
            "id", "blood_group", "height_cm", "weight_kg", "bmi",
            "smoking_status", "alcohol", "pregnant", "emergency_contact",
            "allergies", "medications", "created_at", "updated_at",
        ):
            self.assertIn(key, resp.data)
        self.assertEqual(resp.data["blood_group"], "")

    def test_get_autocreates_record_like_before(self):
        self.assertFalse(PatientRecord.objects.filter(patient=self.patient).exists())
        self.client.force_authenticate(self.staff)
        resp = self.client.get(reverse("record-patient-detail", args=[self.patient.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(PatientRecord.objects.filter(patient=self.patient).exists())

    def test_patch_requires_authentication(self):
        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"blood_group": "O+"}, format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_patch_requires_emr_edit_not_just_emr_view(self):
        # Nurse holds emr.view but not emr.edit.
        UserRole.objects.create(user=self.other, role=Role.objects.get(name="Nurse"))
        self.client.force_authenticate(self.other)
        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"blood_group": "O+"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_patch_updates_blood_group(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"blood_group": "AB-"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["blood_group"], "AB-")
        record = PatientRecord.objects.get(patient=self.patient)
        self.assertEqual(record.blood_group, "AB-")

    def test_patch_creates_record_if_missing(self):
        self.assertFalse(PatientRecord.objects.filter(patient=self.patient).exists())
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"blood_group": "B+"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            PatientRecord.objects.get(patient=self.patient).blood_group, "B+"
        )

    # --- Phase: Admin Patient Edit workflow — PatientRecordUpdateSerializer
    # extended beyond blood_group to every already-existing PatientRecord
    # field the Edit Patient dialog needs. ------------------------------

    def test_patch_updates_emergency_contact(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"emergency_contact": "Sam, +1 555 0199"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["emergency_contact"], "Sam, +1 555 0199")
        record = PatientRecord.objects.get(patient=self.patient)
        self.assertEqual(record.emergency_contact, "Sam, +1 555 0199")

    def test_patch_updates_height_and_weight_and_recomputes_bmi(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"height_cm": 180, "weight_kg": 81}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["height_cm"], 180)
        self.assertEqual(resp.data["weight_kg"], 81)
        # bmi is derived (PatientRecord.save()), not part of the update
        # serializer's fields, and must still recompute via the model's own
        # save() hook exactly like every other write path.
        self.assertEqual(resp.data["bmi"], 25.0)
        record = PatientRecord.objects.get(patient=self.patient)
        self.assertEqual(record.bmi, 25.0)

    def test_patch_updates_smoking_status(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"smoking_status": "former"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["smoking_status"], "former")

    def test_patch_rejects_invalid_smoking_status_choice(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"smoking_status": "sometimes"}, format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("smoking_status", resp.data)

    def test_patch_updates_alcohol(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"alcohol": "occasional"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["alcohol"], "occasional")

    def test_patch_rejects_invalid_alcohol_choice(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"alcohol": "heavy"}, format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("alcohol", resp.data)

    def test_patch_updates_pregnant_true_and_false_and_null(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"pregnant": True}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["pregnant"])

        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"pregnant": False}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["pregnant"])

        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"pregnant": None}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data["pregnant"])

    def test_patch_updates_multiple_fields_in_one_request(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {
                "blood_group": "O+",
                "emergency_contact": "Sam, +1 555 0199",
                "height_cm": 165,
                "weight_kg": 60,
                "smoking_status": "never",
                "alcohol": "none",
                "pregnant": False,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        record = PatientRecord.objects.get(patient=self.patient)
        self.assertEqual(record.blood_group, "O+")
        self.assertEqual(record.emergency_contact, "Sam, +1 555 0199")
        self.assertEqual(record.height_cm, 165)
        self.assertEqual(record.weight_kg, 60)
        self.assertEqual(record.smoking_status, "never")
        self.assertEqual(record.alcohol, "none")
        self.assertFalse(record.pregnant)

    def test_patch_does_not_expose_bmi_as_writable(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"height_cm": 180, "weight_kg": 81, "bmi": 999}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        # bmi is silently ignored (read-only on the update serializer), not
        # an error and not settable to an arbitrary value — it stays derived.
        self.assertEqual(resp.data["bmi"], 25.0)

    def test_patch_is_partial_and_leaves_other_fields_untouched(self):
        record = PatientRecord.objects.create(
            patient=self.patient,
            emergency_contact="Alex, +1 555 0100",
            height_cm=170,
        )
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"blood_group": "A+"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        record.refresh_from_db()
        self.assertEqual(record.blood_group, "A+")
        self.assertEqual(record.emergency_contact, "Alex, +1 555 0100")
        self.assertEqual(record.height_cm, 170)

    def test_patch_rejects_invalid_blood_group_choice(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"blood_group": "Z+"}, format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("blood_group", resp.data)

    def test_patch_does_not_affect_other_patients_records(self):
        PatientRecord.objects.create(patient=self.other, blood_group="O-")
        self.client.force_authenticate(self.staff)
        self.client.patch(
            reverse("record-patient-detail", args=[self.patient.id]),
            {"blood_group": "A+"}, format="json",
        )
        self.assertEqual(
            PatientRecord.objects.get(patient=self.other).blood_group, "O-"
        )


# --------------------------------------------------------------------------- #
# Admin-scoped Allergy/Medication CRUD (Phase: Admin Patient Clinical
# Management). AllergyMedicationTests above covers the pre-existing
# self-service create endpoints (unaffected). These cover the new
# patient_id-scoped admin endpoints: create-for-patient, edit, delete.
# --------------------------------------------------------------------------- #
class PatientAllergyAdminTests(Base):
    def test_create_requires_authentication(self):
        resp = self.client.post(
            reverse("record-patient-allergies", args=[self.patient.id]),
            {"name": "Penicillin", "severity": "HIGH"}, format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_create_requires_emr_edit(self):
        self.client.force_authenticate(self.other)
        resp = self.client.post(
            reverse("record-patient-allergies", args=[self.patient.id]),
            {"name": "Penicillin", "severity": "HIGH"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_creates_allergy_for_patient(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.post(
            reverse("record-patient-allergies", args=[self.patient.id]),
            {"name": "Penicillin", "severity": "HIGH"}, format="json",
        )
        self.assertEqual(resp.status_code, 201)
        record = PatientRecord.objects.get(patient=self.patient)
        self.assertEqual(Allergy.objects.filter(patient_record=record).count(), 1)
        self.assertEqual(resp.data["name"], "Penicillin")

    def test_create_autocreates_record_if_missing(self):
        self.assertFalse(PatientRecord.objects.filter(patient=self.patient).exists())
        self.client.force_authenticate(self.staff)
        resp = self.client.post(
            reverse("record-patient-allergies", args=[self.patient.id]),
            {"name": "Latex", "severity": "LOW"}, format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(PatientRecord.objects.filter(patient=self.patient).exists())

    def test_edit_requires_authentication(self):
        record = PatientRecord.objects.create(patient=self.patient)
        allergy = Allergy.objects.create(
            patient_record=record, name="Penicillin", severity="HIGH"
        )
        resp = self.client.patch(
            reverse("record-allergy-detail", args=[allergy.id]),
            {"severity": "MEDIUM"}, format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_edit_requires_emr_edit(self):
        record = PatientRecord.objects.create(patient=self.patient)
        allergy = Allergy.objects.create(
            patient_record=record, name="Penicillin", severity="HIGH"
        )
        self.client.force_authenticate(self.other)
        resp = self.client.patch(
            reverse("record-allergy-detail", args=[allergy.id]),
            {"severity": "MEDIUM"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_edits_allergy(self):
        record = PatientRecord.objects.create(patient=self.patient)
        allergy = Allergy.objects.create(
            patient_record=record, name="Penicillin", severity="HIGH", notes="Old"
        )
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-allergy-detail", args=[allergy.id]),
            {"severity": "MEDIUM", "notes": "Updated"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["severity"], "MEDIUM")
        self.assertEqual(resp.data["notes"], "Updated")
        allergy.refresh_from_db()
        self.assertEqual(allergy.severity, "MEDIUM")
        # Partial: name untouched.
        self.assertEqual(allergy.name, "Penicillin")

    def test_admin_deletes_allergy(self):
        record = PatientRecord.objects.create(patient=self.patient)
        allergy = Allergy.objects.create(
            patient_record=record, name="Penicillin", severity="HIGH"
        )
        self.client.force_authenticate(self.staff)
        resp = self.client.delete(reverse("record-allergy-detail", args=[allergy.id]))
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Allergy.objects.filter(pk=allergy.id).exists())

    def test_delete_requires_emr_edit(self):
        record = PatientRecord.objects.create(patient=self.patient)
        allergy = Allergy.objects.create(
            patient_record=record, name="Penicillin", severity="HIGH"
        )
        self.client.force_authenticate(self.other)
        resp = self.client.delete(reverse("record-allergy-detail", args=[allergy.id]))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Allergy.objects.filter(pk=allergy.id).exists())

    def test_self_service_allergy_endpoint_still_works(self):
        """AllergyCreateView (self-service, /records/allergies/) must remain
        fully untouched by the new admin-scoped endpoints."""
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("record-allergies"),
            {"name": "Dust", "severity": "LOW"}, format="json",
        )
        self.assertEqual(resp.status_code, 201)


class PatientMedicationAdminTests(Base):
    def test_create_requires_authentication(self):
        resp = self.client.post(
            reverse("record-patient-medications", args=[self.patient.id]),
            {"name": "Metformin", "dosage": "850mg"}, format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_create_requires_emr_edit(self):
        self.client.force_authenticate(self.other)
        resp = self.client.post(
            reverse("record-patient-medications", args=[self.patient.id]),
            {"name": "Metformin", "dosage": "850mg"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_creates_medication_for_patient(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.post(
            reverse("record-patient-medications", args=[self.patient.id]),
            {"name": "Metformin", "dosage": "850mg", "active": True}, format="json",
        )
        self.assertEqual(resp.status_code, 201)
        record = PatientRecord.objects.get(patient=self.patient)
        self.assertEqual(
            Medication.objects.filter(patient_record=record).count(), 1
        )
        self.assertEqual(resp.data["name"], "Metformin")

    def test_create_autocreates_record_if_missing(self):
        self.assertFalse(PatientRecord.objects.filter(patient=self.patient).exists())
        self.client.force_authenticate(self.staff)
        resp = self.client.post(
            reverse("record-patient-medications", args=[self.patient.id]),
            {"name": "Ibuprofen"}, format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(PatientRecord.objects.filter(patient=self.patient).exists())

    def test_edit_requires_authentication(self):
        record = PatientRecord.objects.create(patient=self.patient)
        medication = Medication.objects.create(
            patient_record=record, name="Metformin", dosage="850mg", active=True
        )
        resp = self.client.patch(
            reverse("record-medication-detail", args=[medication.id]),
            {"active": False}, format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_edit_requires_emr_edit(self):
        record = PatientRecord.objects.create(patient=self.patient)
        medication = Medication.objects.create(
            patient_record=record, name="Metformin", dosage="850mg", active=True
        )
        self.client.force_authenticate(self.other)
        resp = self.client.patch(
            reverse("record-medication-detail", args=[medication.id]),
            {"active": False}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_edits_medication(self):
        record = PatientRecord.objects.create(patient=self.patient)
        medication = Medication.objects.create(
            patient_record=record, name="Metformin", dosage="850mg", active=True
        )
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-medication-detail", args=[medication.id]),
            {"dosage": "1000mg", "active": False}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["dosage"], "1000mg")
        self.assertFalse(resp.data["active"])
        medication.refresh_from_db()
        self.assertEqual(medication.dosage, "1000mg")
        self.assertFalse(medication.active)
        # Partial: name untouched.
        self.assertEqual(medication.name, "Metformin")

    def test_admin_deletes_medication(self):
        record = PatientRecord.objects.create(patient=self.patient)
        medication = Medication.objects.create(
            patient_record=record, name="Metformin", dosage="850mg"
        )
        self.client.force_authenticate(self.staff)
        resp = self.client.delete(
            reverse("record-medication-detail", args=[medication.id])
        )
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Medication.objects.filter(pk=medication.id).exists())

    def test_delete_requires_emr_edit(self):
        record = PatientRecord.objects.create(patient=self.patient)
        medication = Medication.objects.create(
            patient_record=record, name="Metformin", dosage="850mg"
        )
        self.client.force_authenticate(self.other)
        resp = self.client.delete(
            reverse("record-medication-detail", args=[medication.id])
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Medication.objects.filter(pk=medication.id).exists())

    def test_self_service_medication_endpoint_still_works(self):
        """MedicationCreateView (self-service, /records/medications/) must
        remain fully untouched by the new admin-scoped endpoints."""
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("record-medications"),
            {"name": "Vitamin D"}, format="json",
        )
        self.assertEqual(resp.status_code, 201)


# --------------------------------------------------------------------------- #
# PrescriptionDetailView: admin edit/delete of a single prescription line
# (Phase: Admin Prescription Management). VisitPrescriptionView's existing
# create flow (tested in NotesAndPrescriptionTests.test_staff_adds_prescription
# above) is untouched; these cover the new PATCH/DELETE endpoint only, mirroring
# PatientAllergyAdminTests/PatientMedicationAdminTests exactly.
# --------------------------------------------------------------------------- #
class PrescriptionAdminTests(Base):
    def test_edit_requires_authentication(self):
        visit = self._visit()
        prescription = Prescription.objects.create(
            medical_visit=visit, medicine="Amoxicillin", dosage="500mg"
        )
        resp = self.client.patch(
            reverse("record-prescription-detail", args=[prescription.id]),
            {"dosage": "250mg"}, format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_edit_requires_emr_prescription(self):
        visit = self._visit()
        prescription = Prescription.objects.create(
            medical_visit=visit, medicine="Amoxicillin", dosage="500mg"
        )
        self.client.force_authenticate(self.other)
        resp = self.client.patch(
            reverse("record-prescription-detail", args=[prescription.id]),
            {"dosage": "250mg"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_edits_prescription(self):
        visit = self._visit()
        prescription = Prescription.objects.create(
            medical_visit=visit,
            medicine="Amoxicillin",
            dosage="500mg",
            frequency="3x daily",
            duration="7 days",
            instructions="Old instructions",
        )
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-prescription-detail", args=[prescription.id]),
            {"dosage": "250mg", "instructions": "Take with food"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["dosage"], "250mg")
        self.assertEqual(resp.data["instructions"], "Take with food")
        prescription.refresh_from_db()
        self.assertEqual(prescription.dosage, "250mg")
        self.assertEqual(prescription.instructions, "Take with food")
        # Partial: medicine and frequency untouched.
        self.assertEqual(prescription.medicine, "Amoxicillin")
        self.assertEqual(prescription.frequency, "3x daily")

    def test_admin_deletes_prescription(self):
        visit = self._visit()
        prescription = Prescription.objects.create(
            medical_visit=visit, medicine="Amoxicillin", dosage="500mg"
        )
        self.client.force_authenticate(self.staff)
        resp = self.client.delete(
            reverse("record-prescription-detail", args=[prescription.id])
        )
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Prescription.objects.filter(pk=prescription.id).exists())

    def test_delete_requires_emr_prescription(self):
        visit = self._visit()
        prescription = Prescription.objects.create(
            medical_visit=visit, medicine="Amoxicillin", dosage="500mg"
        )
        self.client.force_authenticate(self.other)
        resp = self.client.delete(
            reverse("record-prescription-detail", args=[prescription.id])
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Prescription.objects.filter(pk=prescription.id).exists())

    def test_deleting_one_prescription_leaves_others_on_the_visit_intact(self):
        visit = self._visit()
        keep = Prescription.objects.create(
            medical_visit=visit, medicine="Ibuprofen", dosage="200mg"
        )
        remove = Prescription.objects.create(
            medical_visit=visit, medicine="Amoxicillin", dosage="500mg"
        )
        self.client.force_authenticate(self.staff)
        resp = self.client.delete(
            reverse("record-prescription-detail", args=[remove.id])
        )
        self.assertEqual(resp.status_code, 204)
        self.assertTrue(Prescription.objects.filter(pk=keep.id).exists())
        self.assertTrue(MedicalVisit.objects.filter(pk=visit.id).exists())

    def test_existing_create_endpoint_still_works(self):
        """VisitPrescriptionView (POST /records/visits/{id}/prescriptions/)
        must remain fully untouched by the new admin edit/delete endpoint."""
        visit = self._visit()
        self.client.force_authenticate(self.staff)
        resp = self.client.post(
            reverse("record-visit-prescriptions", args=[visit.id]),
            {"medicine": "Paracetamol", "dosage": "500mg"}, format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(
            Prescription.objects.filter(medical_visit=visit, medicine="Paracetamol").count(),
            1,
        )


# --------------------------------------------------------------------------- #
# LabReportDetailView: admin edit/delete of a single lab report (Phase: Admin
# Lab Report Management). VisitReportUploadView's existing multipart upload
# flow (tested in ReportUploadTests above) is untouched; these cover the new
# PATCH/DELETE endpoint only, mirroring PrescriptionAdminTests/
# PatientAllergyAdminTests. LabReportSerializer only exposes `title` as
# writable (id/file_url/uploaded_at are all read-only: pk,
# SerializerMethodField, and auto_now_add respectively), so PATCH is
# necessarily title-only — no separate write serializer was introduced, and
# no schema (test_name/status/ordered_date/result_date/summary) was invented
# since none of it exists on the LabReport model.
# --------------------------------------------------------------------------- #
@override_settings(MEDIA_ROOT=MEDIA)
class LabReportAdminTests(Base):
    def _report(self, visit, title="Blood panel"):
        upload = SimpleUploadedFile(
            "result.pdf", b"%PDF-1.4 fake", content_type="application/pdf"
        )
        return LabReport.objects.create(
            medical_visit=visit, title=title, file=upload, uploaded_by=self.staff
        )

    def test_edit_requires_authentication(self):
        visit = self._visit()
        report = self._report(visit)
        resp = self.client.patch(
            reverse("record-report-detail", args=[report.id]),
            {"title": "Updated"}, format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_edit_requires_emr_edit(self):
        visit = self._visit()
        report = self._report(visit)
        self.client.force_authenticate(self.other)
        resp = self.client.patch(
            reverse("record-report-detail", args=[report.id]),
            {"title": "Updated"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_edits_report_title(self):
        visit = self._visit()
        report = self._report(visit, title="Blood panel")
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-report-detail", args=[report.id]),
            {"title": "Blood panel (repeat)"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["title"], "Blood panel (repeat)")
        report.refresh_from_db()
        self.assertEqual(report.title, "Blood panel (repeat)")

    def test_edit_ignores_read_only_fields(self):
        """id/file_url/uploaded_at are read-only on LabReportSerializer, so
        attempting to override them via PATCH is silently ignored rather
        than erroring — the same "read-only fields stay derived" behavior
        already locked in for PatientRecordDetailView's bmi."""
        visit = self._visit()
        report = self._report(visit)
        original_uploaded_at = report.uploaded_at
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-report-detail", args=[report.id]),
            {"title": "Renamed", "uploaded_at": "2020-01-01T00:00:00Z"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        report.refresh_from_db()
        self.assertEqual(report.title, "Renamed")
        self.assertEqual(report.uploaded_at, original_uploaded_at)

    def test_admin_deletes_report(self):
        visit = self._visit()
        report = self._report(visit)
        self.client.force_authenticate(self.staff)
        resp = self.client.delete(
            reverse("record-report-detail", args=[report.id])
        )
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(LabReport.objects.filter(pk=report.id).exists())

    def test_delete_requires_emr_delete(self):
        visit = self._visit()
        report = self._report(visit)
        self.client.force_authenticate(self.other)
        resp = self.client.delete(
            reverse("record-report-detail", args=[report.id])
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(LabReport.objects.filter(pk=report.id).exists())

    def test_deleting_one_report_leaves_others_on_the_visit_intact(self):
        visit = self._visit()
        keep = self._report(visit, title="Blood panel")
        remove = self._report(visit, title="X-Ray")
        self.client.force_authenticate(self.staff)
        resp = self.client.delete(
            reverse("record-report-detail", args=[remove.id])
        )
        self.assertEqual(resp.status_code, 204)
        self.assertTrue(LabReport.objects.filter(pk=keep.id).exists())
        self.assertTrue(MedicalVisit.objects.filter(pk=visit.id).exists())

    def test_existing_upload_flow_still_works(self):
        """VisitReportUploadView (POST /records/visits/{id}/reports/) must
        remain fully untouched by the new admin edit/delete endpoint."""
        visit = self._visit()
        self.client.force_authenticate(self.patient)
        upload = SimpleUploadedFile(
            "new-result.pdf", b"%PDF-1.4 fake", content_type="application/pdf"
        )
        resp = self.client.post(
            reverse("record-visit-reports", args=[visit.id]),
            {"title": "New report", "file": upload},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(
            LabReport.objects.filter(medical_visit=visit, title="New report").count(),
            1,
        )


class VitalSignsAdminTests(Base):
    def test_edit_requires_authentication(self):
        visit = self._visit()
        resp = self.client.patch(
            reverse("record-visit-vitals", args=[visit.id]),
            {"pulse": 72}, format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_edit_requires_emr_edit(self):
        visit = self._visit()
        self.client.force_authenticate(self.other)
        resp = self.client.patch(
            reverse("record-visit-vitals", args=[visit.id]),
            {"pulse": 72}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_first_edit_creates_vitals_row(self):
        """A visit may exist with zero VitalSigns rows (no auto-creation
        signal, and the OneToOneField is not required) — the first PATCH
        must get-or-create rather than 404 or error."""
        visit = self._visit()
        self.assertFalse(VitalSigns.objects.filter(medical_visit=visit).exists())
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-visit-vitals", args=[visit.id]),
            {"pulse": 72, "temperature": 37.0, "blood_pressure": "120/80"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["pulse"], 72)
        self.assertEqual(resp.data["blood_pressure"], "120/80")
        self.assertEqual(VitalSigns.objects.filter(medical_visit=visit).count(), 1)

    def test_second_edit_updates_same_row_not_a_duplicate(self):
        """OneToOneField means a second row is impossible anyway, but this
        confirms the endpoint's get_or_create reuses the same row rather
        than erroring on a would-be IntegrityError."""
        visit = self._visit()
        self.client.force_authenticate(self.staff)
        self.client.patch(
            reverse("record-visit-vitals", args=[visit.id]),
            {"pulse": 72}, format="json",
        )
        resp = self.client.patch(
            reverse("record-visit-vitals", args=[visit.id]),
            {"pulse": 80}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["pulse"], 80)
        self.assertEqual(VitalSigns.objects.filter(medical_visit=visit).count(), 1)

    def test_partial_update_leaves_other_fields_intact(self):
        visit = self._visit()
        VitalSigns.objects.create(
            medical_visit=visit, pulse=72, temperature=37.0, weight=70, height=175,
        )
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-visit-vitals", args=[visit.id]),
            {"pulse": 90}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["pulse"], 90)
        self.assertEqual(resp.data["temperature"], 37.0)
        self.assertEqual(resp.data["weight"], 70)
        self.assertEqual(resp.data["height"], 175)

    def test_no_bmi_field_in_response(self):
        """BMI does not exist on VitalSigns — it is only a derived property
        on the unrelated, patient-level PatientRecord model — so it must
        not be invented in this endpoint's response."""
        visit = self._visit()
        self.client.force_authenticate(self.staff)
        resp = self.client.patch(
            reverse("record-visit-vitals", args=[visit.id]),
            {"pulse": 72}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("bmi", resp.data)

    def test_visit_detail_reflects_updated_vitals(self):
        """VisitDetailView already nests vitals read-only via
        MedicalVisitSerializer — confirms this new PATCH endpoint and that
        pre-existing read path stay in sync without any change to
        VisitDetailView/MedicalVisitSerializer."""
        visit = self._visit()
        self.client.force_authenticate(self.staff)
        self.client.patch(
            reverse("record-visit-vitals", args=[visit.id]),
            {"pulse": 88}, format="json",
        )
        resp = self.client.get(reverse("record-visit-detail", args=[visit.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["vitals"]["pulse"], 88)


# --------------------------------------------------------------------------- #
# Existing systems unchanged
# --------------------------------------------------------------------------- #
class ExistingSystemsUnchangedTests(Base):
    def test_booking_still_works(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("appointments"),
            {
                "doctor": self.doctor.id,
                "date": (timezone.localdate() + timedelta(days=1)).isoformat(),
                "time": "11:00",
                "reason": "Checkup",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)

    def test_check_in_still_works(self):
        appt = self._appt()
        self.client.force_authenticate(self.patient)
        resp = self.client.post(reverse("appointment-check-in", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("queue_position", resp.data)

    def test_attendance_confirm_still_works(self):
        appt = self._appt(day=timezone.localdate() + timedelta(days=1))
        self.client.force_authenticate(self.patient)
        resp = self.client.post(reverse("appointment-confirm", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("risk_level", resp.data)

    def test_duration_prediction_still_works(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("predict-duration"),
            {"doctor": self.doctor.id, "visit_type": self.visit_type.id},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("estimated_duration", resp.data)

    def test_waitlist_still_works(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("waitlist"),
            {
                "doctor": self.doctor.id,
                "date": (timezone.localdate() + timedelta(days=2)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
