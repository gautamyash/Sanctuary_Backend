"""
Detect no-shows: active appointments whose slot has passed without a check-in.

Marks each as a no-show through the attendance layer (which reconciles
prediction accuracy, notifies, logs, and backfills the freed slot from the
Smart Waitlist when the appointment was high risk). Intended to run on a cron
similar to `expire_offers`. Never touches booking/scheduling logic.
"""

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from appointments.models import Appointment
from attendance.interventions import mark_no_show

GRACE_MINUTES = 30


class Command(BaseCommand):
    help = "Mark past, un-attended, active appointments as no-shows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--grace",
            type=int,
            default=GRACE_MINUTES,
            help="Minutes after the slot start before flagging a no-show.",
        )

    def handle(self, *args, **options):
        grace = timedelta(minutes=options["grace"])
        now = timezone.now()
        count = 0

        candidates = Appointment.objects.filter(
            status__in=Appointment.ACTIVE_STATUSES,
            patient_checked_in_at__isnull=True,
        ).exclude(attendance_status=Appointment.Attendance.NO_SHOW)

        for appt in candidates:
            slot = datetime.combine(appt.date, appt.time)
            if timezone.is_naive(slot):
                slot = timezone.make_aware(slot, timezone.get_current_timezone())
            if slot + grace < now:
                mark_no_show(appt)
                count += 1

        self.stdout.write(self.style.SUCCESS(f"Marked {count} no-show(s)."))
