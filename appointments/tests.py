from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from authorization.models import Role, UserRole
from doctors.models import Doctor, DoctorLeave, DoctorSchedule, Specialty
from .models import Appointment
from .services import BookingConflict, BookingService


class BookingServiceConcurrencyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="patient@example.com",
            password="password",
            name="Patient",
        )
        self.specialty = Specialty.objects.create(name="Cardiology")
        self.doctor = Doctor.objects.create(
            name="Dr. Example",
            specialty=self.specialty,
            hospital="Example Hospital",
        )
        self.doctor.schedules.create(
            weekday=DoctorSchedule.Weekday.MONDAY,
            start_time=time(9, 0),
            end_time=time(17, 0),
            slot_minutes=30,
        )

    def test_book_raises_conflict_when_overlap_exists_after_lock(self):
        Appointment.objects.create(
            doctor=self.doctor,
            patient=self.user,
            date=date(2026, 7, 6),
            time=time(9, 0),
            estimated_duration=60,
            status=Appointment.Status.CONFIRMED,
        )

        with self.assertRaises(BookingConflict):
            BookingService.book(
                patient=self.user,
                doctor=self.doctor,
                date=date(2026, 7, 6),
                time=time(9, 30),
                estimated_duration=30,
            )

        self.assertEqual(Appointment.objects.count(), 1)


# --------------------------------------------------------------------------- #
# AdminAppointmentListView: ?patient= filter (Phase: Admin Medical Visit
# Management). The view previously supported status/doctor/date*/q filters
# only; this adds the one additional filter the Admin Panel's "Add Visit"
# flow needs to list a specific patient's own appointments so staff can pick
# one to complete. No new endpoint, no schema change — same view, same
# permission ("appointment.view"), one more optional query param.
# --------------------------------------------------------------------------- #
class AdminAppointmentListViewPatientFilterTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.specialty = Specialty.objects.create(name="Cardiology")
        cls.doctor = Doctor.objects.create(
            name="Dr. Mitchell", specialty=cls.specialty, hospital="St. Mary's"
        )
        cls.patient = get_user_model().objects.create_user(
            email="pat@example.com", password="x", name="Pat"
        )
        cls.other = get_user_model().objects.create_user(
            email="other@example.com", password="x", name="Other"
        )
        cls.staff = get_user_model().objects.create_user(
            email="staff@example.com", password="x", name="Staff"
        )
        UserRole.objects.create(user=cls.staff, role=Role.objects.get(name="Admin"))
        cls.appt1 = Appointment.objects.create(
            doctor=cls.doctor,
            patient=cls.patient,
            date=date(2026, 8, 1),
            time=time(9, 0),
            estimated_duration=30,
        )
        cls.appt2 = Appointment.objects.create(
            doctor=cls.doctor,
            patient=cls.other,
            date=date(2026, 8, 1),
            time=time(10, 0),
            estimated_duration=30,
        )

    def test_requires_authentication(self):
        resp = self.client.get(reverse("admin-appointments"))
        self.assertEqual(resp.status_code, 401)

    def test_requires_appointment_view(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("admin-appointments"))
        self.assertEqual(resp.status_code, 403)

    def test_no_patient_filter_returns_all(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.get(reverse("admin-appointments"))
        self.assertEqual(resp.status_code, 200)
        ids = {row["id"] for row in resp.data["results"]}
        self.assertEqual(ids, {self.appt1.id, self.appt2.id})

    def test_patient_filter_scopes_to_one_patient(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.get(
            reverse("admin-appointments"), {"patient": self.patient.id}
        )
        self.assertEqual(resp.status_code, 200)
        ids = {row["id"] for row in resp.data["results"]}
        self.assertEqual(ids, {self.appt1.id})

    def test_patient_filter_combines_with_status(self):
        self.appt1.status = Appointment.Status.COMPLETED
        self.appt1.save(update_fields=["status", "updated_at"])
        self.client.force_authenticate(self.staff)
        resp = self.client.get(
            reverse("admin-appointments"),
            {"patient": self.patient.id, "status": "confirmed"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["results"], [])


# --------------------------------------------------------------------------- #
# AdminAppointmentListView: POST — staff books on behalf of a patient (Phase:
# Admin Follow-up & Care Plan). Before this, nothing let staff book for anyone
# but themselves; AppointmentListCreateView.create() hardcodes
# patient=request.user. This reuses AppointmentSerializer + BookingService.book()
# verbatim — same validation, same locking/conflict logic — the only new
# behavior is accepting an explicit `patient` id. Gated on "appointment.create",
# a permission code already seeded in RBAC but unused until now.
# --------------------------------------------------------------------------- #
class AdminAppointmentBookingTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.specialty = Specialty.objects.create(name="Dermatology")
        cls.doctor = Doctor.objects.create(
            name="Dr. Patel", specialty=cls.specialty, hospital="St. Mary's"
        )
        cls.patient = get_user_model().objects.create_user(
            email="followup@example.com", password="x", name="Follow"
        )
        cls.staff = get_user_model().objects.create_user(
            email="staff2@example.com", password="x", name="Staff2"
        )
        UserRole.objects.create(user=cls.staff, role=Role.objects.get(name="Admin"))
        cls.other = get_user_model().objects.create_user(
            email="other2@example.com", password="x", name="Other2"
        )
        cls.book_date = date.today() + timedelta(days=14)
        cls.doctor.schedules.create(
            weekday=cls.book_date.weekday(),
            start_time=time(9, 0),
            end_time=time(17, 0),
            slot_minutes=30,
        )

    def _payload(self, **overrides):
        payload = {
            "patient": self.patient.id,
            "doctor": self.doctor.id,
            "date": self.book_date.isoformat(),
            "time": "10:00",
        }
        payload.update(overrides)
        return payload

    def test_requires_authentication(self):
        resp = self.client.post(
            reverse("admin-appointments"), self._payload(), format="json"
        )
        self.assertEqual(resp.status_code, 401)

    def test_requires_appointment_create(self):
        self.client.force_authenticate(self.other)
        resp = self.client.post(
            reverse("admin-appointments"), self._payload(), format="json"
        )
        self.assertEqual(resp.status_code, 403)

    def test_staff_books_for_patient_not_self(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.post(
            reverse("admin-appointments"), self._payload(), format="json"
        )
        self.assertEqual(resp.status_code, 201)
        appt = Appointment.objects.get(pk=resp.data["id"])
        self.assertEqual(appt.patient_id, self.patient.id)
        self.assertNotEqual(appt.patient_id, self.staff.id)

    def test_missing_patient_is_rejected(self):
        self.client.force_authenticate(self.staff)
        payload = self._payload()
        payload.pop("patient")
        resp = self.client.post(
            reverse("admin-appointments"), payload, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_conflicting_slot_returns_409(self):
        Appointment.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            date=self.book_date,
            time=time(10, 0),
            estimated_duration=30,
            status=Appointment.Status.CONFIRMED,
        )
        self.client.force_authenticate(self.staff)
        resp = self.client.post(
            reverse("admin-appointments"), self._payload(), format="json"
        )
        self.assertEqual(resp.status_code, 409)

    def test_booked_appointment_appears_in_patient_scoped_admin_list(self):
        self.client.force_authenticate(self.staff)
        self.client.post(reverse("admin-appointments"), self._payload(), format="json")
        resp = self.client.get(
            reverse("admin-appointments"), {"patient": self.patient.id}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["results"]), 1)

    def test_existing_self_service_booking_still_works(self):
        """AppointmentListCreateView (POST /api/appointments/) must remain
        fully untouched by the new admin booking endpoint."""
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("appointments"),
            {
                "doctor": self.doctor.id,
                "date": self.book_date.isoformat(),
                "time": "11:00",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        appt = Appointment.objects.get(pk=resp.data["id"])
        self.assertEqual(appt.patient_id, self.patient.id)


# --------------------------------------------------------------------------- #
# DoctorLeave -> booking/slot-generation wiring (Phase: Advanced Doctor
# Schedule & Leave Management). Before this phase, DoctorLeave had zero
# effect on scheduling. Confirms: only an *approved* leave blocks booking,
# it blocks on every booking-creation path (serializer-validated AND the
# direct BookingService.book() path used by WaitlistAcceptView), and slot
# endpoints report on_leave instead of silently returning stale slots.
# --------------------------------------------------------------------------- #
class DoctorLeaveBookingTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.specialty = Specialty.objects.create(name="Neurology")
        cls.doctor = Doctor.objects.create(
            name="Dr. Osei", specialty=cls.specialty, hospital="St. Mary's"
        )
        cls.patient = get_user_model().objects.create_user(
            email="leave-patient@example.com", password="x", name="Leave Patient"
        )
        cls.staff = get_user_model().objects.create_user(
            email="staff3@example.com", password="x", name="Staff3"
        )
        UserRole.objects.create(user=cls.staff, role=Role.objects.get(name="Admin"))

        cls.leave_date = date.today() + timedelta(days=21)
        cls.doctor.schedules.create(
            weekday=cls.leave_date.weekday(),
            start_time=time(9, 0),
            end_time=time(17, 0),
            slot_minutes=30,
        )
        cls.leave = DoctorLeave.objects.create(
            doctor=cls.doctor,
            start_date=cls.leave_date,
            end_date=cls.leave_date,
            reason="Conference",
            status=DoctorLeave.Status.APPROVED,
        )

    # -- serializer-validated paths (self-service + admin booking) -------- #

    def test_self_service_booking_blocked_on_approved_leave(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("appointments"),
            {
                "doctor": self.doctor.id,
                "date": self.leave_date.isoformat(),
                "time": "10:00",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("date", resp.data)

    def test_admin_booking_blocked_on_approved_leave(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.post(
            reverse("admin-appointments"),
            {
                "patient": self.patient.id,
                "doctor": self.doctor.id,
                "date": self.leave_date.isoformat(),
                "time": "10:00",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("date", resp.data)

    def test_pending_leave_does_not_block_booking(self):
        self.leave.status = DoctorLeave.Status.PENDING
        self.leave.save(update_fields=["status"])
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("appointments"),
            {
                "doctor": self.doctor.id,
                "date": self.leave_date.isoformat(),
                "time": "10:00",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)

    def test_rejected_leave_does_not_block_booking(self):
        self.leave.status = DoctorLeave.Status.REJECTED
        self.leave.save(update_fields=["status"])
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("appointments"),
            {
                "doctor": self.doctor.id,
                "date": self.leave_date.isoformat(),
                "time": "10:30",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)

    def test_leave_outside_date_range_does_not_block(self):
        other_day = self.leave_date + timedelta(days=1)
        self.doctor.schedules.create(
            weekday=other_day.weekday(),
            start_time=time(9, 0),
            end_time=time(17, 0),
            slot_minutes=30,
        )
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("appointments"),
            {
                "doctor": self.doctor.id,
                "date": other_day.isoformat(),
                "time": "10:00",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)

    # -- direct BookingService.book() path (WaitlistAcceptView bypass) ---- #

    def test_direct_booking_service_raises_conflict_on_approved_leave(self):
        with self.assertRaises(BookingConflict):
            BookingService.book(
                patient=self.patient,
                doctor=self.doctor,
                date=self.leave_date,
                time=time(11, 0),
                estimated_duration=30,
            )
        self.assertEqual(Appointment.objects.count(), 0)

    def test_direct_booking_service_succeeds_when_leave_pending(self):
        self.leave.status = DoctorLeave.Status.PENDING
        self.leave.save(update_fields=["status"])
        appt = BookingService.book(
            patient=self.patient,
            doctor=self.doctor,
            date=self.leave_date,
            time=time(11, 0),
            estimated_duration=30,
        )
        self.assertIsNotNone(appt.id)

    # -- slot-generation endpoints ----------------------------------------- #

    def test_legacy_slots_endpoint_reports_on_leave(self):
        resp = self.client.get(
            reverse("doctor-slots", args=[self.doctor.id]),
            {"date": self.leave_date.isoformat()},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["slots"], [])
        self.assertTrue(resp.data["on_leave"])

    def test_smart_slots_endpoint_reports_on_leave(self):
        resp = self.client.get(
            reverse("doctor-smart-slots", args=[self.doctor.id]),
            {"date": self.leave_date.isoformat(), "duration": 30},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["slots"], [])
        self.assertEqual(resp.data["booked"], [])
        self.assertTrue(resp.data["on_leave"])

    def test_legacy_slots_endpoint_unaffected_when_not_on_leave(self):
        other_day = self.leave_date + timedelta(days=1)
        self.doctor.schedules.create(
            weekday=other_day.weekday(),
            start_time=time(9, 0),
            end_time=time(17, 0),
            slot_minutes=30,
        )
        resp = self.client.get(
            reverse("doctor-slots", args=[self.doctor.id]),
            {"date": other_day.isoformat()},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["slots"])
        self.assertNotIn("on_leave", resp.data)
