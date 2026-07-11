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
