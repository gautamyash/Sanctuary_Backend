"""
Queue layer endpoints. All additive — they never alter booking, scheduling,
duration prediction, or waitlist behavior. They read appointment lifecycle
data and (for check-in/start) stamp new optional timestamps, then ask
QueueService to recalculate.
"""

from datetime import datetime, timedelta

from django.db.models import Avg, Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from appointments.analytics import track
from appointments.models import Appointment
from appointments.notifications import NotificationService
from authorization.permissions import PermissionRequired
from authorization.services import PermissionService
from doctors.models import Doctor, DoctorSchedule

from .models import DoctorQueueState
from .services import (
    DEFAULT_DURATION_MINUTES,
    RUNNING_LATE_THRESHOLD_MINUTES,
    QueueService,
    _aware,
)


def _owned_appointment_or_none(pk, user):
    try:
        return Appointment.objects.select_related("doctor").get(
            pk=pk, patient=user
        )
    except Appointment.DoesNotExist:
        return None


class CheckInView(APIView):
    """POST /api/appointments/{id}/check-in/ — patient marks arrival.

    Rules: owner only, appointment must be today, only once."""

    def post(self, request, pk):
        appointment = _owned_appointment_or_none(pk, request.user)
        if appointment is None:
            return Response({"detail": "Not found."}, status=404)

        today = timezone.localdate()
        if appointment.date != today:
            return Response(
                {"detail": "You can only check in on the day of your appointment."},
                status=400,
            )
        if appointment.status not in Appointment.ACTIVE_STATUSES:
            return Response(
                {"detail": "This appointment is not active."}, status=400
            )
        if appointment.patient_checked_in_at is not None:
            return Response(
                {"detail": "You have already checked in."}, status=400
            )

        appointment.patient_checked_in_at = timezone.now()
        appointment.save(update_fields=["patient_checked_in_at", "updated_at"])
        track("patient_checked_in", appointment=appointment.id)

        QueueService.recalculate_queue(appointment.doctor, appointment.date)
        return Response(QueueService.get_status(appointment))


class ConsultationStartView(APIView):
    """POST /api/appointments/{id}/start/ — doctor/admin starts a consult.

    Stamps consultation_started_at (once) and recalculates the queue."""

    permission_classes = [PermissionRequired]
    permission_code = "queue.manage"

    def post(self, request, pk):
        appointment = get_object_or_404(
            Appointment.objects.select_related("doctor"), pk=pk
        )
        if appointment.status not in Appointment.ACTIVE_STATUSES:
            return Response(
                {"detail": "Only active appointments can be started."}, status=400
            )
        if appointment.consultation_started_at is not None:
            return Response(
                {"detail": "This consultation has already started."}, status=400
            )

        appointment.consultation_started_at = timezone.now()
        appointment.save(update_fields=["consultation_started_at", "updated_at"])
        track("consultation_started", appointment=appointment.id)

        QueueService.recalculate_queue(appointment.doctor, appointment.date)
        return Response(QueueService.get_status(appointment))


class QueueStatusView(APIView):
    """GET /api/appointments/{id}/queue-status/ — live status for the owner
    (or staff)."""

    def get(self, request, pk):
        if PermissionService.has_permission(request.user, "queue.view"):
            appointment = get_object_or_404(
                Appointment.objects.select_related("doctor"), pk=pk
            )
        else:
            appointment = _owned_appointment_or_none(pk, request.user)
            if appointment is None:
                return Response({"detail": "Not found."}, status=404)

        track("arrival_estimate_viewed", appointment=appointment.id)
        return Response(QueueService.get_status(appointment))


class DoctorQueueView(APIView):
    """GET /api/doctors/{id}/queue/?date=YYYY-MM-DD — full ordered timeline
    for a doctor's day (used by the timeline UI)."""

    permission_classes = [PermissionRequired]
    permission_code = "queue.view"

    def get(self, request, pk):
        doctor = get_object_or_404(Doctor, pk=pk, is_active=True)
        date_str = request.query_params.get("date")
        try:
            day = (
                datetime.strptime(date_str, "%Y-%m-%d").date()
                if date_str
                else timezone.localdate()
            )
        except ValueError:
            return Response({"detail": "Invalid date, use YYYY-MM-DD."}, status=400)
        return Response(QueueService.build_timeline(doctor, day))


class QueueAnalyticsView(APIView):
    """GET /api/analytics/queue/?date=YYYY-MM-DD&doctor=<id> — staff-only
    operational metrics for the day."""

    permission_classes = [PermissionRequired]
    permission_code = "analytics.view"

    def get(self, request):
        date_str = request.query_params.get("date")
        try:
            day = (
                datetime.strptime(date_str, "%Y-%m-%d").date()
                if date_str
                else timezone.localdate()
            )
        except ValueError:
            return Response({"detail": "Invalid date, use YYYY-MM-DD."}, status=400)

        appts = Appointment.objects.filter(date=day).exclude(
            status=Appointment.Status.CANCELLED
        )
        doctor_id = request.query_params.get("doctor")
        if doctor_id:
            appts = appts.filter(doctor_id=doctor_id)

        completed = list(
            appts.filter(
                status=Appointment.Status.COMPLETED,
                consultation_started_at__isnull=False,
            ).select_related("doctor")
        )

        waits = []
        delays = []
        punctual = 0
        for appt in completed:
            scheduled = _aware(day, appt.time)
            delta_min = (appt.consultation_started_at - scheduled).total_seconds() / 60
            delays.append(delta_min)
            waits.append(max(0.0, delta_min))
            if delta_min <= RUNNING_LATE_THRESHOLD_MINUTES:
                punctual += 1

        # Average current queue length across doctors with a snapshot today.
        states = DoctorQueueState.objects.filter(date=day)
        if doctor_id:
            states = states.filter(doctor_id=doctor_id)
        queue_lengths = [
            QueueService._compute(s.doctor, day).waiting_count for s in states
        ]

        # Utilization: consulted minutes / scheduled working minutes.
        doctor_ids = {a.doctor_id for a in appts}
        scheduled_minutes = 0
        for block in DoctorSchedule.objects.filter(
            doctor_id__in=doctor_ids, weekday=day.weekday()
        ):
            start = _aware(day, block.start_time)
            end = _aware(day, block.end_time)
            scheduled_minutes += (end - start).total_seconds() / 60
        consulted_minutes = sum(
            (a.actual_duration or a.estimated_duration or DEFAULT_DURATION_MINUTES)
            for a in completed
        )

        n = len(completed)
        return Response(
            {
                "date": day.isoformat(),
                "patients_seen_today": n,
                "average_wait_time": round(sum(waits) / n, 1) if n else 0,
                "average_delay": round(sum(delays) / n, 1) if n else 0,
                "average_queue_length": round(
                    sum(queue_lengths) / len(queue_lengths), 1
                )
                if queue_lengths
                else 0,
                "consultation_punctuality": round(punctual / n, 3) if n else None,
                "doctor_utilization": round(consulted_minutes / scheduled_minutes, 3)
                if scheduled_minutes
                else None,
            }
        )
