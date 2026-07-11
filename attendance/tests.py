"""
Regression tests for Feature 4 — AI No-Show Prediction & Attendance
Intelligence.

Two halves:
  1. The attendance layer itself (prediction, scoring, bands, confirm/check-in
     effects, learning engine, analytics, reminders, waitlist/queue bridges).
  2. Explicit "existing systems unchanged" checks proving booking, waitlist,
     queue, and duration prediction behave exactly as before.
"""

from datetime import time, timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from appointments.models import (
    Appointment,
    AppointmentDurationPrediction,
    VisitType,
    WaitlistEntry,
)
from authorization.models import Role, UserRole
from attendance import interventions
from attendance.models import AppointmentRiskPrediction, ReminderLog
from attendance.services import NoShowPredictionService, level_for
from doctors.models import Doctor, DoctorSchedule, Specialty


def _future_weekday(days_min, weekday):
    d = timezone.localdate() + timedelta(days=days_min)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


class Base(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.specialty = Specialty.objects.create(name="Cardiology")
        cls.doctor = Doctor.objects.create(
            name="Dr. Mitchell", specialty=cls.specialty, hospital="St. Mary's"
        )
        cls.other_doctor = Doctor.objects.create(
            name="Dr. Lee", specialty=cls.specialty, hospital="St. Mary's"
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

    def _appt(self, day=None, t=time(10, 0), patient=None, doctor=None,
              status=Appointment.Status.CONFIRMED, minutes=30):
        return Appointment.objects.create(
            doctor=doctor or self.doctor,
            patient=patient or self.patient,
            date=day or _future_weekday(2, 1),  # a near Tuesday
            time=t,
            estimated_duration=minutes,
            status=status,
        )

    def _completed_visit(self, patient=None, doctor=None, days_ago=10):
        return Appointment.objects.create(
            doctor=doctor or self.doctor,
            patient=patient or self.patient,
            date=timezone.localdate() - timedelta(days=days_ago),
            time=time(10, 0),
            estimated_duration=30,
            actual_duration=30,
            status=Appointment.Status.COMPLETED,
        )

    def _no_show(self, patient=None, doctor=None, days_ago=15):
        return Appointment.objects.create(
            doctor=doctor or self.other_doctor,
            patient=patient or self.patient,
            date=timezone.localdate() - timedelta(days=days_ago),
            time=time(10, 0),
            estimated_duration=30,
            status=Appointment.Status.CANCELLED,
            attendance_status=Appointment.Attendance.NO_SHOW,
        )


# --------------------------------------------------------------------------- #
# 1. Prediction engine
# --------------------------------------------------------------------------- #
class PredictionGenerationTests(Base):
    def test_prediction_is_auto_generated_on_create(self):
        appt = self._appt()
        self.assertTrue(
            AppointmentRiskPrediction.objects.filter(appointment=appt).exists()
        )

    def test_risk_level_bands(self):
        self.assertEqual(level_for(0), "LOW")
        self.assertEqual(level_for(25), "LOW")
        self.assertEqual(level_for(26), "MEDIUM")
        self.assertEqual(level_for(50), "MEDIUM")
        self.assertEqual(level_for(51), "HIGH")
        self.assertEqual(level_for(75), "HIGH")
        self.assertEqual(level_for(76), "CRITICAL")
        self.assertEqual(level_for(100), "CRITICAL")

    def test_rule_scoring_high_risk_new_patient(self):
        # New patient, a prior no-show, booked >21 days ahead, new doctor,
        # neutral weekday/time (Tuesday 10:00).
        self._no_show()
        day = _future_weekday(22, 1)  # Tuesday, >21 days out
        appt = self._appt(day=day, t=time(10, 0))
        result = NoShowPredictionService.predict(appt)
        # 35 (no-show) + 15 (never visited) + 10 (booked ahead) = 60
        self.assertEqual(round(result.risk_score), 60)
        self.assertEqual(result.risk_level, "HIGH")
        self.assertIn("Previous no-show on record", result.reasons)

    def test_rule_scoring_monday_morning(self):
        self._completed_visit()  # visited doctor -> no "never visited"
        day = _future_weekday(2, 0)  # Monday, within 21 days
        appt = self._appt(day=day, t=time(9, 0))
        result = NoShowPredictionService.predict(appt)
        self.assertEqual(round(result.risk_score), 5)
        self.assertIn("Monday morning slot", result.reasons)

    def test_rule_scoring_friday_evening(self):
        self._completed_visit()
        day = _future_weekday(2, 4)  # Friday, within 21 days
        appt = self._appt(day=day, t=time(18, 0))
        result = NoShowPredictionService.predict(appt)
        self.assertEqual(round(result.risk_score), 8)
        self.assertIn("Friday evening slot", result.reasons)

    def test_low_risk_loyal_patient(self):
        for _ in range(4):
            self._completed_visit()
        appt = self._appt(t=time(10, 0))
        result = NoShowPredictionService.predict(appt)
        self.assertEqual(result.risk_level, "LOW")


class ConfirmAndCheckInTests(Base):
    def test_confirmation_reduces_risk(self):
        self._no_show()
        day = _future_weekday(22, 1)
        appt = self._appt(day=day, t=time(10, 0))
        before = AppointmentRiskPrediction.objects.get(appointment=appt).risk_score

        self.client.force_authenticate(self.patient)
        resp = self.client.post(reverse("appointment-confirm", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)
        # 60 - 30 = 30
        self.assertEqual(resp.data["risk_score"], 30)
        self.assertLess(resp.data["risk_score"], before)
        appt.refresh_from_db()
        self.assertEqual(
            appt.attendance_status, Appointment.Attendance.CONFIRMED
        )
        self.assertIsNotNone(appt.confirmed_at)

    def test_check_in_sets_risk_to_zero(self):
        self._no_show()
        appt = self._appt(day=_future_weekday(22, 1))
        # Simulate the existing queue check-in stamping the field.
        appt.patient_checked_in_at = timezone.now()
        appt.attendance_status = Appointment.Attendance.CHECKED_IN
        appt.save(update_fields=[
            "patient_checked_in_at", "attendance_status", "updated_at"
        ])
        pred = AppointmentRiskPrediction.objects.get(appointment=appt)
        self.assertEqual(round(pred.risk_score), 0)
        self.assertEqual(pred.risk_level, "LOW")

    def test_confirm_requires_owner(self):
        appt = self._appt()
        self.client.force_authenticate(self.other)
        resp = self.client.post(reverse("appointment-confirm", args=[appt.id]))
        self.assertEqual(resp.status_code, 404)


class LearningEngineTests(Base):
    def test_completion_records_accuracy(self):
        for _ in range(4):
            self._completed_visit()
        appt = self._appt()  # low risk
        self.client.force_authenticate(self.staff)
        resp = self.client.post(
            reverse("appointment-complete", args=[appt.id]),
            {"actual_minutes": 20}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        pred = AppointmentRiskPrediction.objects.get(appointment=appt)
        self.assertEqual(pred.actual_outcome, "attended")
        self.assertTrue(pred.was_correct)  # low risk + attended = correct

    def test_ml_source_after_enough_history(self):
        for _ in range(26):
            self._completed_visit()
        appt = self._appt()
        pred = AppointmentRiskPrediction.objects.get(appointment=appt)
        self.assertEqual(pred.prediction_source, "ml")

    def test_no_show_marked_correct_for_high_risk(self):
        self._no_show()
        appt = self._appt(day=_future_weekday(22, 1))  # HIGH
        interventions.mark_no_show(appt)
        appt.refresh_from_db()
        self.assertEqual(
            appt.attendance_status, Appointment.Attendance.NO_SHOW
        )
        pred = AppointmentRiskPrediction.objects.get(appointment=appt)
        self.assertEqual(pred.actual_outcome, "no_show")
        self.assertTrue(pred.was_correct)


class ReminderLoggingTests(Base):
    def test_confirmation_request_logged_for_medium_plus(self):
        self._no_show()
        appt = self._appt(day=_future_weekday(22, 1))  # HIGH -> confirmation
        self.assertTrue(
            ReminderLog.objects.filter(
                appointment=appt, type=ReminderLog.Type.CONFIRMATION
            ).exists()
        )

    def test_no_show_logs_followup(self):
        appt = self._appt(day=_future_weekday(22, 1))
        self._no_show()
        NoShowPredictionService.predict_and_store(appt)
        interventions.mark_no_show(appt)
        self.assertTrue(
            ReminderLog.objects.filter(
                appointment=appt, type=ReminderLog.Type.FOLLOWUP
            ).exists()
        )


class RiskEndpointTests(Base):
    def test_owner_can_read_risk(self):
        appt = self._appt()
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("appointment-risk", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            set(resp.data.keys()),
            {"risk_score", "risk_level", "confidence", "reasons"},
        )
        self.assertIsInstance(resp.data["risk_score"], int)
        self.assertLessEqual(resp.data["confidence"], 100)
        self.assertIsInstance(resp.data["reasons"], list)

    def test_non_owner_cannot_read_risk(self):
        appt = self._appt()
        self.client.force_authenticate(self.other)
        resp = self.client.get(reverse("appointment-risk", args=[appt.id]))
        self.assertEqual(resp.status_code, 404)


class AnalyticsTests(Base):
    def test_analytics_staff_only(self):
        appt = self._appt()
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("attendance-analytics"))
        self.assertEqual(resp.status_code, 403)

    def test_analytics_payload(self):
        for _ in range(3):
            self._completed_visit()
        self._no_show()
        # A guaranteed HIGH-risk active appointment: a fresh patient with a
        # prior no-show, a new doctor, booked >21 days ahead.
        self._no_show(patient=self.other)
        self._appt(patient=self.other, day=_future_weekday(22, 1))
        self.client.force_authenticate(self.staff)
        resp = self.client.get(reverse("attendance-analytics"))
        self.assertEqual(resp.status_code, 200)
        # Core metrics must always be present (backward-compatible contract).
        self.assertTrue(
            {
                "average_no_show_rate",
                "high_risk_patients",
                "appointments_confirmed",
                "appointments_no_show",
                "prediction_accuracy",
            }.issubset(resp.data.keys())
        )
        # Additive dashboard metrics.
        for key in (
            "predicted_no_shows",
            "reminders_sent",
            "reminders_responded",
            "reminder_response_rate",
        ):
            self.assertIn(key, resp.data)
        self.assertGreaterEqual(resp.data["appointments_no_show"], 1)
        self.assertGreaterEqual(resp.data["high_risk_patients"], 1)


class WaitlistBridgeTests(Base):
    def test_high_risk_no_show_backfills_waitlist(self):
        day = _future_weekday(22, 1)
        appt = self._appt(day=day, t=time(10, 0))
        self._no_show()
        NoShowPredictionService.predict_and_store(appt)  # ensure HIGH
        waiter = WaitlistEntry.objects.create(
            patient=self.other, doctor=self.doctor, date=day,
            status=WaitlistEntry.Status.WAITING,
        )
        interventions.mark_no_show(appt)
        waiter.refresh_from_db()
        self.assertEqual(waiter.status, WaitlistEntry.Status.OFFERED)

    def test_low_risk_no_show_does_not_backfill(self):
        for _ in range(4):
            self._completed_visit()
        day = _future_weekday(2, 1)
        appt = self._appt(day=day, t=time(10, 0))  # LOW risk
        waiter = WaitlistEntry.objects.create(
            patient=self.other, doctor=self.doctor, date=day,
            status=WaitlistEntry.Status.WAITING,
        )
        interventions.mark_no_show(appt)
        waiter.refresh_from_db()
        self.assertEqual(waiter.status, WaitlistEntry.Status.WAITING)


# --------------------------------------------------------------------------- #
# 2. Existing systems unchanged
# --------------------------------------------------------------------------- #
class ExistingBookingUnchangedTests(Base):
    def test_booking_still_works_and_conflicts_409(self):
        self.client.force_authenticate(self.patient)
        day = _future_weekday(3, 2)
        payload = {"doctor": self.doctor.id, "date": day.isoformat(),
                   "time": "10:00", "reason": "Checkup"}
        resp = self.client.post(reverse("appointments"), payload, format="json")
        self.assertEqual(resp.status_code, 201)
        # Same slot for another patient -> 409 (constraint unchanged).
        self.client.force_authenticate(self.other)
        resp2 = self.client.post(reverse("appointments"), payload, format="json")
        self.assertEqual(resp2.status_code, 409)


class ExistingWaitlistUnchangedTests(Base):
    def test_cancel_offers_exactly_one_slot(self):
        day = _future_weekday(3, 2)
        appt = self._appt(day=day, t=time(11, 0))
        WaitlistEntry.objects.create(
            patient=self.other, doctor=self.doctor, date=day,
            status=WaitlistEntry.Status.WAITING,
        )
        self.client.force_authenticate(self.patient)
        resp = self.client.post(reverse("appointment-cancel", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)
        # The attendance layer must NOT double-fill: exactly one offer exists.
        self.assertEqual(
            WaitlistEntry.objects.filter(
                status=WaitlistEntry.Status.OFFERED
            ).count(),
            1,
        )


class ExistingQueueUnchangedTests(Base):
    def test_check_in_endpoint_still_returns_queue_status(self):
        appt = self._appt(day=timezone.localdate(), t=time(10, 0))
        self.client.force_authenticate(self.patient)
        resp = self.client.post(reverse("appointment-check-in", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("queue_position", resp.data)
        self.assertIn("estimated_wait_minutes", resp.data)


class ExistingDurationPredictionUnchangedTests(Base):
    def test_duration_prediction_endpoint(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("predict-duration"),
            {"doctor": self.doctor.id, "visit_type": self.visit_type.id},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("estimated_duration", resp.data)
        self.assertIn("confidence", resp.data)
        self.assertIn("source", resp.data)
