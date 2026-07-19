"""
Regression tests for Feature 3 — Real-Time Queue Optimization.

Covers: queue recalculation & ordering, doctor delay estimation, check-in
rules, consultation start/completion triggers, and queue-status API
response stability. None of these touch the booking, duration-prediction,
or waitlist code paths.
"""

from datetime import time, timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from appointments.models import Appointment
from authorization.models import Role, UserRole
from doctors.models import Doctor, DoctorSchedule, Specialty
from queues.models import DoctorQueueState
from queues.services import QueueService


QUEUE_STATUS_KEYS = {
    "queue_position",
    "estimated_wait_minutes",
    "estimated_start",
    "estimated_finish",
    "doctor_running_late",
    "delay_minutes",
}


class QueueTestBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.specialty = Specialty.objects.create(name="Cardiology")
        cls.doctor = Doctor.objects.create(
            name="Dr. Sarah Mitchell", specialty=cls.specialty, hospital="St. Mary's"
        )
        # A wide working block so "today" always has the day-of-week covered.
        for weekday in range(7):
            DoctorSchedule.objects.create(
                doctor=cls.doctor,
                weekday=weekday,
                start_time=time(9, 0),
                end_time=time(17, 0),
                slot_minutes=30,
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
        cls.today = timezone.localdate()

    def _appt(self, t, minutes=30, status=Appointment.Status.CONFIRMED,
              patient=None, started=None, completed=None, actual=None):
        return Appointment.objects.create(
            doctor=self.doctor,
            patient=patient or self.patient,
            date=self.today,
            time=t,
            estimated_duration=minutes,
            status=status,
            consultation_started_at=started,
            consultation_completed_at=completed,
            actual_duration=actual,
        )


class QueueOrderingTests(QueueTestBase):
    def test_positions_are_sequential_for_waiting(self):
        a = self._appt(time(9, 0))
        b = self._appt(time(9, 30))
        c = self._appt(time(10, 0))
        QueueService.recalculate_queue(self.doctor, self.today)
        a.refresh_from_db(); b.refresh_from_db(); c.refresh_from_db()
        self.assertEqual([a.queue_position, b.queue_position, c.queue_position],
                         [1, 2, 3])

    def test_completed_drops_out_and_positions_shift(self):
        now = timezone.now()
        done = self._appt(
            time(9, 0),
            status=Appointment.Status.COMPLETED,
            started=now - timedelta(minutes=40),
            completed=now - timedelta(minutes=15),
            actual=25,
        )
        b = self._appt(time(9, 30))
        c = self._appt(time(10, 0))
        QueueService.recalculate_queue(self.doctor, self.today)
        done.refresh_from_db(); b.refresh_from_db(); c.refresh_from_db()
        self.assertIsNone(done.queue_position)
        self.assertEqual([b.queue_position, c.queue_position], [1, 2])

    def test_cancelled_never_enters_queue(self):
        a = self._appt(time(9, 0))
        cancelled = self._appt(time(9, 30), status=Appointment.Status.CANCELLED)
        QueueService.recalculate_queue(self.doctor, self.today)
        a.refresh_from_db(); cancelled.refresh_from_db()
        self.assertEqual(a.queue_position, 1)
        self.assertIsNone(cancelled.queue_position)

    def test_in_progress_is_current_and_position_one(self):
        now = timezone.now()
        current = self._appt(time(9, 0), started=now - timedelta(minutes=5))
        nxt = self._appt(time(9, 30))
        state = QueueService.recalculate_queue(self.doctor, self.today)
        current.refresh_from_db(); nxt.refresh_from_db()
        self.assertEqual(state.current_appointment_id, current.id)
        self.assertEqual(current.queue_position, 1)
        self.assertEqual(nxt.queue_position, 2)


class DelayEstimationTests(QueueTestBase):
    def test_running_late_delay_is_positive(self):
        # In progress: scheduled 17m ago, started 5m ago, 30m duration ->
        # projected finish now+25 vs scheduled finish now+13 => ~12m late.
        now = timezone.now()
        scheduled = (timezone.localtime(now) - timedelta(minutes=17)).time()
        self._appt(scheduled, minutes=30, started=now - timedelta(minutes=5))
        comp = QueueService._compute(self.doctor, self.today)
        self.assertGreaterEqual(comp.delay_minutes, 11)
        self.assertLessEqual(comp.delay_minutes, 13)

    def test_on_time_or_early_not_flagged_late(self):
        # A single waiting appointment scheduled comfortably in the future.
        future = (timezone.localtime(timezone.now()) + timedelta(hours=2)).time()
        appt = self._appt(future)
        status = QueueService.get_status(appt)
        self.assertLessEqual(status["delay_minutes"], 0)
        self.assertFalse(status["doctor_running_late"])

    def test_wait_and_arrival_are_ordered(self):
        future = (timezone.localtime(timezone.now()) + timedelta(hours=1)).time()
        appt = self._appt(future)
        QueueService.recalculate_queue(self.doctor, self.today)
        wait = QueueService.estimate_wait_time(appt)
        arrival = QueueService.estimate_arrival_time(appt)
        self.assertGreater(wait, 0)
        self.assertIsNotNone(arrival)
        # Recommended arrival is before the estimated start.
        self.assertLess(arrival, timezone.now() + timedelta(minutes=wait))


class CheckInRuleTests(QueueTestBase):
    def setUp(self):
        self.appt = self._appt(
            (timezone.localtime(timezone.now()) + timedelta(hours=1)).time()
        )
        self.url = reverse("appointment-check-in", args=[self.appt.id])

    def test_owner_can_check_in_once(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.data.keys()), QUEUE_STATUS_KEYS)
        self.appt.refresh_from_db()
        self.assertIsNotNone(self.appt.patient_checked_in_at)

    def test_cannot_check_in_twice(self):
        self.client.force_authenticate(self.patient)
        self.client.post(self.url)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 400)

    def test_non_owner_cannot_check_in(self):
        self.client.force_authenticate(self.other)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 404)

    def test_cannot_check_in_on_a_different_day(self):
        future = Appointment.objects.create(
            doctor=self.doctor, patient=self.patient,
            date=self.today + timedelta(days=1), time=time(9, 0),
            estimated_duration=30,
        )
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("appointment-check-in", args=[future.id])
        )
        self.assertEqual(resp.status_code, 400)


class ConsultationStartTests(QueueTestBase):
    def setUp(self):
        self.appt = self._appt(time(9, 0))
        self.url = reverse("appointment-start", args=[self.appt.id])

    def test_staff_can_start_and_queue_recalculates(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)
        self.appt.refresh_from_db()
        self.assertIsNotNone(self.appt.consultation_started_at)
        state = DoctorQueueState.objects.get(doctor=self.doctor, date=self.today)
        self.assertEqual(state.current_appointment_id, self.appt.id)

    def test_non_staff_cannot_start(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_cannot_start_twice(self):
        self.client.force_authenticate(self.staff)
        self.client.post(self.url)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 400)


class CompletionTriggersQueueTests(QueueTestBase):
    def test_completion_recalculates_queue(self):
        appt = self._appt(time(9, 0), started=timezone.now() - timedelta(minutes=5))
        # Complete through the EXISTING completion endpoint (unchanged API).
        self.client.force_authenticate(self.staff)
        resp = self.client.post(
            reverse("appointment-complete", args=[appt.id]),
            {"actual_minutes": 20}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.COMPLETED)
        # Queue snapshot exists and no longer points at the finished consult.
        state = DoctorQueueState.objects.get(doctor=self.doctor, date=self.today)
        self.assertIsNone(state.current_appointment_id)
        self.assertIsNone(appt.queue_position)


class AppointmentNoShowTests(QueueTestBase):
    """Phase: Live Consultation & Queue Workflow — manual no-show marking.

    Reuses the existing attendance.interventions.mark_no_show() verbatim
    (already covered by attendance/tests.py for its own reconciliation/
    notification/waitlist-backfill behavior); these tests cover only the new
    endpoint's own guards, permission gate, and that it recalculates the
    queue like its check-in/start siblings."""

    def setUp(self):
        self.appt = self._appt(time(9, 0))
        self.url = reverse("appointment-no-show", args=[self.appt.id])

    def test_staff_can_mark_no_show_and_queue_recalculates(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.data.keys()), QUEUE_STATUS_KEYS)
        self.appt.refresh_from_db()
        self.assertEqual(
            self.appt.attendance_status, Appointment.Attendance.NO_SHOW
        )
        # Status is untouched — attendance is an independent, informational
        # layer that never cancels the appointment or alters booking.
        self.assertEqual(self.appt.status, Appointment.Status.CONFIRMED)
        state = DoctorQueueState.objects.get(doctor=self.doctor, date=self.today)
        self.assertIsNotNone(state)

    def test_non_staff_cannot_mark_no_show(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_cannot_mark_no_show(self):
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 401)

    def test_cannot_mark_no_show_twice(self):
        self.client.force_authenticate(self.staff)
        self.client.post(self.url)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 400)

    def test_cannot_mark_no_show_if_already_checked_in(self):
        self.appt.patient_checked_in_at = timezone.now()
        self.appt.save(update_fields=["patient_checked_in_at"])
        self.client.force_authenticate(self.staff)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 400)
        self.appt.refresh_from_db()
        self.assertNotEqual(
            self.appt.attendance_status, Appointment.Attendance.NO_SHOW
        )

    def test_cannot_mark_no_show_on_completed_appointment(self):
        self.appt.status = Appointment.Status.COMPLETED
        self.appt.save(update_fields=["status"])
        self.client.force_authenticate(self.staff)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 400)

    def test_doctor_role_holds_attendance_manage_and_can_mark(self):
        """attendance.manage is already seeded to Doctor (and Nurse); this
        confirms the code the view gates on is actually reachable by a
        doctor's own account, not just Admin."""
        doctor_user = User.objects.create_user(
            email="dr-osei@example.com", password="x", name="Dr. Osei"
        )
        UserRole.objects.create(
            user=doctor_user, role=Role.objects.get(name="Doctor")
        )
        self.client.force_authenticate(doctor_user)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)


class QueueTimelineFieldsTests(QueueTestBase):
    """Phase: Live Consultation & Queue Workflow — additive fields on
    build_timeline()'s per-entry dict."""

    def test_timeline_entry_includes_checked_in_at_and_attendance_status(self):
        appt = self._appt(time(9, 0))
        appt.patient_checked_in_at = timezone.now()
        appt.save(update_fields=["patient_checked_in_at"])
        data = QueueService.build_timeline(self.doctor, self.today)
        row = data["timeline"][0]
        self.assertIn("checked_in_at", row)
        self.assertIn("attendance_status", row)
        self.assertIsNotNone(row["checked_in_at"])
        self.assertEqual(row["attendance_status"], Appointment.Attendance.UNKNOWN)

    def test_timeline_entry_checked_in_at_null_when_not_checked_in(self):
        self._appt(time(9, 0))
        data = QueueService.build_timeline(self.doctor, self.today)
        row = data["timeline"][0]
        self.assertIsNone(row["checked_in_at"])

    def test_timeline_entry_reflects_no_show_status(self):
        from attendance.interventions import mark_no_show

        appt = self._appt(time(9, 0))
        mark_no_show(appt)
        data = QueueService.build_timeline(self.doctor, self.today)
        row = data["timeline"][0]
        self.assertEqual(row["attendance_status"], Appointment.Attendance.NO_SHOW)
        # Existing "state"/"checked_in" contract is untouched by this.
        self.assertEqual(row["state"], "waiting")
        self.assertFalse(row["checked_in"])


class QueueStatusApiTests(QueueTestBase):
    def test_status_payload_is_stable(self):
        appt = self._appt(
            (timezone.localtime(timezone.now()) + timedelta(hours=1)).time()
        )
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("appointment-queue-status", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.data.keys()), QUEUE_STATUS_KEYS)
        self.assertEqual(resp.data["queue_position"], 1)

    def test_patient_cannot_access_full_doctor_queue(self):
        self._appt(time(9, 0))
        self._appt(time(9, 30))
        self.client.force_authenticate(self.patient)
        resp = self.client.get(
            reverse("doctor-queue", args=[self.doctor.id]),
            {"date": self.today.isoformat()},
        )
        self.assertEqual(resp.status_code, 403)

    def test_staff_can_access_full_doctor_queue(self):
        self._appt(time(9, 0))
        self._appt(time(9, 30))
        self.client.force_authenticate(self.staff)
        resp = self.client.get(
            reverse("doctor-queue", args=[self.doctor.id]),
            {"date": self.today.isoformat()},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["timeline"]), 2)
        self.assertEqual(resp.data["timeline"][0]["queue_position"], 1)
