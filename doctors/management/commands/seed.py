"""Seed the database with the specialties and doctors used by the mobile app.

Usage: python manage.py seed
Idempotent — safe to run repeatedly.
"""

from datetime import time

from django.core.management.base import BaseCommand

from appointments.models import VisitType
from doctors.models import Doctor, DoctorSchedule, Specialty

VISIT_TYPES = [
    ("General Consultation", 30, "Standard first-line consultation."),
    ("Follow-up", 15, "Review of an ongoing treatment or recovery."),
    ("Routine Checkup", 20, "Periodic preventive health check."),
    ("Emergency Consultation", 60, "Urgent, unplanned consultation."),
    ("Prescription Renewal", 10, "Renewal of an existing prescription."),
    ("Lab Report Review", 10, "Walkthrough of recent lab results."),
    ("Vaccination", 15, "Vaccine administration and observation."),
    ("Chronic Disease Review", 20, "Diabetes, hypertension, and similar reviews."),
]

SPECIALTIES = [
    ("General", "medkit-outline"),
    ("Cardiology", "heart-outline"),
    ("Dentistry", "happy-outline"),
    ("Pediatrics", "body-outline"),
    ("Dermatology", "sunny-outline"),
    ("Neurology", "pulse-outline"),
]

DOCTORS = [
    {
        "name": "Dr. Sarah Mitchell",
        "specialty": "Cardiology",
        "hospital": "St. Mary's General Hospital",
        "address": "450 Medical Drive, Austin, TX",
        "distance_km": "1.2",
        "rating": "4.9",
        "reviews": 128,
        "years_experience": 12,
        "fee": "120.00",
        "about": "Board-certified cardiologist focused on preventive heart care and patient-first treatment plans. Known for calm, unhurried consultations.",
        "photo": "https://randomuser.me/api/portraits/women/44.jpg",
        "color": "#003d9b",
    },
    {
        "name": "Dr. James Wilson",
        "specialty": "General",
        "hospital": "Sanctuary Health, Main Campus",
        "address": "12 Wellness Way, Austin, TX",
        "distance_km": "0.8",
        "rating": "4.7",
        "reviews": 96,
        "years_experience": 8,
        "fee": "60.00",
        "about": "General physician for routine checkups, chronic care follow-ups, and everyday health questions. First point of contact for most patients.",
        "photo": "https://randomuser.me/api/portraits/men/32.jpg",
        "color": "#0e7490",
    },
    {
        "name": "Dr. Emily Chen",
        "specialty": "Pediatrics",
        "hospital": "Sanctuary Family Clinic",
        "address": "88 Riverside Ave, Austin, TX",
        "distance_km": "2.5",
        "rating": "5.0",
        "reviews": 214,
        "years_experience": 15,
        "fee": "80.00",
        "about": "Pediatrician known for gentle care and clear guidance for parents at every stage, from newborn checkups to teen health.",
        "photo": "https://randomuser.me/api/portraits/women/65.jpg",
        "color": "#b45309",
    },
    {
        "name": "Dr. Julian Vance",
        "specialty": "Dentistry",
        "hospital": "Sanctuary Dental Studio",
        "address": "301 Smile Street, Austin, TX",
        "distance_km": "3.1",
        "rating": "4.8",
        "reviews": 152,
        "years_experience": 11,
        "fee": "70.00",
        "about": "Dentist delivering painless modern dentistry, from cleanings and whitening to full restorations, in a spa-calm studio.",
        "photo": "https://randomuser.me/api/portraits/men/43.jpg",
        "color": "#0f766e",
    },
    {
        "name": "Dr. Elena Rodriguez",
        "specialty": "Dermatology",
        "hospital": "City Health Center",
        "address": "77 Plaza Blvd, Austin, TX",
        "distance_km": "4.0",
        "rating": "4.8",
        "reviews": 87,
        "years_experience": 9,
        "fee": "95.00",
        "about": "Specialist in medical and cosmetic dermatology with an evidence-based, skin-health-first approach.",
        "photo": "https://randomuser.me/api/portraits/women/68.jpg",
        "color": "#6d28d9",
    },
    {
        "name": "Dr. Marcus Thorne",
        "specialty": "Neurology",
        "hospital": "Sanctuary Neuro Center",
        "address": "5 Quiet Lane, Austin, TX",
        "distance_km": "5.4",
        "rating": "4.9",
        "reviews": 63,
        "years_experience": 14,
        "fee": "140.00",
        "about": "Neurologist specializing in migraines, sleep disorders, and long-term neurological care with thorough, unrushed evaluations.",
        "photo": "https://randomuser.me/api/portraits/men/11.jpg",
        "color": "#166534",
    },
]

# Mon-Sat, 09:00-12:00 and 14:00-17:30, 30-minute slots
SCHEDULE_BLOCKS = [
    (time(9, 0), time(12, 0)),
    (time(14, 0), time(17, 30)),
]


class Command(BaseCommand):
    help = "Seed specialties, doctors, and weekly schedules."

    def handle(self, *args, **options):
        for name, icon in SPECIALTIES:
            Specialty.objects.update_or_create(name=name, defaults={"icon": icon})
        self.stdout.write(f"Specialties: {Specialty.objects.count()}")

        for name, minutes, description in VISIT_TYPES:
            VisitType.objects.update_or_create(
                name=name,
                defaults={
                    "default_duration": minutes,
                    "description": description,
                    "active": True,
                },
            )
        self.stdout.write(f"Visit types: {VisitType.objects.count()}")

        for entry in DOCTORS:
            specialty = Specialty.objects.get(name=entry.pop("specialty"))
            doctor, _ = Doctor.objects.update_or_create(
                name=entry["name"],
                defaults={**entry, "specialty": specialty},
            )
            for weekday in range(6):  # Monday..Saturday
                for start, end in SCHEDULE_BLOCKS:
                    DoctorSchedule.objects.update_or_create(
                        doctor=doctor,
                        weekday=weekday,
                        start_time=start,
                        defaults={"end_time": end, "slot_minutes": 30},
                    )
        self.stdout.write(
            self.style.SUCCESS(f"Doctors: {Doctor.objects.count()} seeded.")
        )
