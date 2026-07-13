"""
Regression tests for the legacy GET /api/doctors/{id}/slots/ endpoint.

These lock in the backward-compatibility fix: the legacy fixed-grid endpoint
must mark a slot unavailable whenever it overlaps an active appointment of ANY
duration, not only when an appointment shares its exact start time. It uses the
same interval-overlap engine as smart-slots and booking validation, while
keeping the URL and response schema 100% identical.

A booked interval and the grid it is tested against:

    Working block : 09:00 - 12:00, 30-minute grid
    Grid slots    : 09:00 09:30 10:00 10:30 11:00 11:30
"""

import tempfile
from datetime import date, time

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from doctors.models import Doctor, DoctorSchedule, Specialty
from appointments.models import Appointment
from medical_records.models import MedicalVisit


# A fixed future Monday so weekday() == 0 lines up with the schedule below.
TEST_DAY = date(2026, 7, 13)  # Monday


class LegacySlotsOverlapTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.specialty = Specialty.objects.create(name="Cardiology")
        cls.doctor = Doctor.objects.create(
            name="Dr. Sarah Lee", specialty=cls.specialty, hospital="Central"
        )
        # Monday 09:00-12:00 on a 30-minute grid.
        DoctorSchedule.objects.create(
            doctor=cls.doctor,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(12, 0),
            slot_minutes=30,
        )
        cls.patient = User.objects.create_user(
            email="patient@example.com", password="x", name="Pat"
        )
        cls.url = reverse("doctor-slots", args=[cls.doctor.id])

    # -- helpers -----------------------------------------------------------

    def _book(self, start, minutes, status=Appointment.Status.CONFIRMED):
        return Appointment.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            date=TEST_DAY,
            time=start,
            estimated_duration=minutes,
            status=status,
        )

    def _slots(self):
        resp = self.client.get(self.url, {"date": TEST_DAY.isoformat()})
        self.assertEqual(resp.status_code, 200)
        return {s["time"]: s["available"] for s in resp.data["slots"]}

    # -- schema / contract -------------------------------------------------

    def test_response_schema_unchanged(self):
        """Every slot still has exactly time/label/available; envelope has
        date + slots."""
        resp = self.client.get(self.url, {"date": TEST_DAY.isoformat()})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.data.keys()), {"date", "slots"})
        self.assertEqual(resp.data["date"], TEST_DAY.isoformat())
        self.assertTrue(resp.data["slots"])
        for slot in resp.data["slots"]:
            self.assertEqual(set(slot.keys()), {"time", "label", "available"})
            self.assertIsInstance(slot["available"], bool)

    def test_no_appointments_all_available(self):
        avail = self._slots()
        self.assertEqual(len(avail), 6)  # 09:00..11:30
        self.assertTrue(all(avail.values()))

    # -- durations ---------------------------------------------------------

    def test_30_minute_appointment_blocks_its_own_slot(self):
        self._book(time(9, 0), 30)
        avail = self._slots()
        self.assertFalse(avail["09:00"])
        self.assertTrue(avail["09:30"])  # adjacent, free

    def test_45_minute_appointment_blocks_overlapped_grid_slot(self):
        # 09:00-09:45 must knock out BOTH 09:00 and the 09:30 grid slot
        # (09:30-10:00 overlaps 09:45), which the old exact-match logic missed.
        self._book(time(9, 0), 45)
        avail = self._slots()
        self.assertFalse(avail["09:00"])
        self.assertFalse(avail["09:30"])
        self.assertTrue(avail["10:00"])

    def test_60_minute_appointment_blocks_two_grid_slots(self):
        # 09:00-10:00 blocks 09:00 and 09:30; 10:00 is adjacent and free.
        self._book(time(9, 0), 60)
        avail = self._slots()
        self.assertFalse(avail["09:00"])
        self.assertFalse(avail["09:30"])
        self.assertTrue(avail["10:00"])

    # -- overlap shapes ----------------------------------------------------

    def test_partial_overlap_blocks_slot(self):
        # Appointment 09:15-09:45 partially overlaps both 09:00 and 09:30 grid
        # slots even though it shares a start time with neither.
        self._book(time(9, 15), 30)
        avail = self._slots()
        self.assertFalse(avail["09:00"])
        self.assertFalse(avail["09:30"])
        self.assertTrue(avail["10:00"])

    def test_exact_overlap_blocks_slot(self):
        self._book(time(10, 0), 30)
        avail = self._slots()
        self.assertFalse(avail["10:00"])
        self.assertTrue(avail["09:30"])
        self.assertTrue(avail["10:30"])

    def test_adjacent_appointments_still_available(self):
        # Back-to-back bookings must not block the touching grid slot:
        # 09:00-09:30 and 10:00-10:30 leave 09:30 open.
        self._book(time(9, 0), 30)
        self._book(time(10, 0), 30)
        avail = self._slots()
        self.assertFalse(avail["09:00"])
        self.assertTrue(avail["09:30"])
        self.assertFalse(avail["10:00"])
        self.assertTrue(avail["10:30"])

    # -- status filtering --------------------------------------------------

    def test_cancelled_appointment_does_not_block_slot(self):
        self._book(time(9, 0), 45, status=Appointment.Status.CANCELLED)
        avail = self._slots()
        self.assertTrue(avail["09:00"])
        self.assertTrue(avail["09:30"])

    def test_completed_appointment_does_not_block_slot(self):
        # COMPLETED is not an ACTIVE_STATUS, so it frees the grid too.
        self._book(time(9, 0), 45, status=Appointment.Status.COMPLETED)
        avail = self._slots()
        self.assertTrue(avail["09:00"])
        self.assertTrue(avail["09:30"])

    def test_pending_appointment_blocks_slot(self):
        self._book(time(9, 0), 45, status=Appointment.Status.PENDING)
        avail = self._slots()
        self.assertFalse(avail["09:00"])
        self.assertFalse(avail["09:30"])


MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA)
class DoctorMeVisitTests(APITestCase):
    """Regression tests for the doctor self-service EMR endpoints
    (/api/doctors/me/visits/...). Confirms: IsLinkedDoctor gating, strict
    per-doctor scoping (a doctor never sees or can write another doctor's
    visit), and that notes/prescription/reports reuse the existing
    MedicalVisit/Prescription/LabReport models without touching the
    patient- or admin-scoped medical_records endpoints."""

    @classmethod
    def setUpTestData(cls):
        specialty = Specialty.objects.create(name="Cardiology")

        cls.doctor_user = User.objects.create_user(
            email="doc-a@example.com", password="x", name="Dr. A"
        )
        cls.doctor = Doctor.objects.create(
            name="Dr. A", specialty=specialty, hospital="Central",
            user=cls.doctor_user,
        )

        cls.other_doctor_user = User.objects.create_user(
            email="doc-b@example.com", password="x", name="Dr. B"
        )
        cls.other_doctor = Doctor.objects.create(
            name="Dr. B", specialty=specialty, hospital="Central",
            user=cls.other_doctor_user,
        )

        cls.unlinked_user = User.objects.create_user(
            email="patient@example.com", password="x", name="Pat"
        )

        cls.patient = User.objects.create_user(
            email="pat2@example.com", password="x", name="Patient Two"
        )

    def _completed_visit(self, doctor, patient=None):
        appt = Appointment.objects.create(
            doctor=doctor,
            patient=patient or self.patient,
            date=timezone.localdate(),
            time=time(10, 0),
            estimated_duration=30,
            status=Appointment.Status.CONFIRMED,
        )
        appt.status = Appointment.Status.COMPLETED
        appt.save(update_fields=["status", "updated_at"])
        return MedicalVisit.objects.get(appointment=appt)

    # -- permission gating ---------------------------------------------- #

    def test_unlinked_user_is_forbidden(self):
        visit = self._completed_visit(self.doctor)
        self.client.force_authenticate(self.unlinked_user)
        resp = self.client.get(reverse("doctor-me-visit-list"))
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get(
            reverse("doctor-me-visit-detail", args=[visit.id])
        )
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_is_unauthorized(self):
        resp = self.client.get(reverse("doctor-me-visit-list"))
        self.assertEqual(resp.status_code, 401)

    # -- list / detail scoping -------------------------------------------- #

    def test_list_only_returns_own_visits(self):
        mine = self._completed_visit(self.doctor)
        self._completed_visit(self.other_doctor)

        self.client.force_authenticate(self.doctor_user)
        resp = self.client.get(reverse("doctor-me-visit-list"))
        self.assertEqual(resp.status_code, 200)
        ids = {row["id"] for row in resp.data["results"]}
        self.assertEqual(ids, {mine.id})

    def test_detail_of_own_visit(self):
        visit = self._completed_visit(self.doctor)
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.get(
            reverse("doctor-me-visit-detail", args=[visit.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["id"], visit.id)

    def test_cannot_view_another_doctors_visit(self):
        visit = self._completed_visit(self.other_doctor)
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.get(
            reverse("doctor-me-visit-detail", args=[visit.id])
        )
        self.assertEqual(resp.status_code, 404)

    # -- notes (Consultation) --------------------------------------------- #

    def test_patch_notes_updates_own_visit(self):
        visit = self._completed_visit(self.doctor)
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.patch(
            reverse("doctor-me-visit-notes", args=[visit.id]),
            {
                "chief_complaint": "Chest pain",
                "diagnosis": "Angina",
                "clinical_notes": "Stable, prescribed nitroglycerin.",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        visit.refresh_from_db()
        self.assertEqual(visit.diagnosis, "Angina")
        self.assertEqual(visit.chief_complaint, "Chest pain")

    def test_cannot_patch_notes_on_another_doctors_visit(self):
        visit = self._completed_visit(self.other_doctor)
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.patch(
            reverse("doctor-me-visit-notes", args=[visit.id]),
            {"diagnosis": "Hijacked"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)
        visit.refresh_from_db()
        self.assertNotEqual(visit.diagnosis, "Hijacked")

    # -- prescription (Write Prescription) -------------------------------- #

    def test_patch_prescription_creates_line(self):
        visit = self._completed_visit(self.doctor)
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.patch(
            reverse("doctor-me-visit-prescription", args=[visit.id]),
            {
                "medicine": "Atorvastatin",
                "dosage": "20mg",
                "frequency": "Once daily",
                "duration": "30 days",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(visit.prescriptions.count(), 1)
        self.assertEqual(visit.prescriptions.first().medicine, "Atorvastatin")

    def test_cannot_prescribe_on_another_doctors_visit(self):
        visit = self._completed_visit(self.other_doctor)
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.patch(
            reverse("doctor-me-visit-prescription", args=[visit.id]),
            {"medicine": "Hijacked"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(visit.prescriptions.count(), 0)

    # -- reports (Upload Reports) ------------------------------------------ #

    def test_upload_report_on_own_visit(self):
        visit = self._completed_visit(self.doctor)
        self.client.force_authenticate(self.doctor_user)
        file = SimpleUploadedFile(
            "bloodwork.pdf", b"%PDF-1.4 test", content_type="application/pdf"
        )
        resp = self.client.post(
            reverse("doctor-me-visit-reports", args=[visit.id]),
            {"title": "Bloodwork", "file": file},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(visit.reports.count(), 1)
        self.assertEqual(visit.reports.first().uploaded_by_id, self.doctor_user.id)

    def test_cannot_upload_report_on_another_doctors_visit(self):
        visit = self._completed_visit(self.other_doctor)
        self.client.force_authenticate(self.doctor_user)
        file = SimpleUploadedFile(
            "bloodwork.pdf", b"%PDF-1.4 test", content_type="application/pdf"
        )
        resp = self.client.post(
            reverse("doctor-me-visit-reports", args=[visit.id]),
            {"title": "Bloodwork", "file": file},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(visit.reports.count(), 0)

    # -- other endpoints unaffected --------------------------------------- #

    def test_patient_scoped_visit_endpoint_still_patient_only(self):
        """Guard against accidentally widening the pre-existing
        patient-scoped medical_records endpoints while adding this file."""
        visit = self._completed_visit(self.doctor)
        self.client.force_authenticate(self.doctor_user)
        resp = self.client.get(reverse("record-visits"))
        self.assertEqual(resp.status_code, 200)
        # The treating doctor is not the visit's patient, so the
        # patient-scoped list must not return it to them.
        ids = {row["id"] for row in resp.data["results"]}
        self.assertNotIn(visit.id, ids)
