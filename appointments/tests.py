from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase

from doctors.models import Doctor, DoctorSchedule, Specialty
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
