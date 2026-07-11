"""Expire overdue waitlist offers and cascade slots to the next patients.

Run every minute, e.g. with cron:
    * * * * * /path/to/python manage.py expire_offers
(or a Celery beat / APScheduler job calling
 appointments.services.expire_stale_offers).
"""

from django.core.management.base import BaseCommand

from appointments.services import expire_stale_offers


class Command(BaseCommand):
    help = "Expire overdue waitlist offers and offer slots to the next in line."

    def handle(self, *args, **options):
        count = expire_stale_offers()
        self.stdout.write(self.style.SUCCESS(f"Expired {count} offer(s)."))
