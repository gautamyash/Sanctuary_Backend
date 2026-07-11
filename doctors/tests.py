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

from datetime import date, time

from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import User
from doctors.models import Doctor, DoctorSchedule, Specialty
from appointments.models import Appointment


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
