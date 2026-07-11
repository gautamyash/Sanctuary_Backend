"""
Attendance Intelligence endpoints (Feature 4).

All additive. They never alter booking, scheduling, duration prediction,
waitlist, or queue behaviour — they read/stamp the attendance fields and ask
the prediction engine to recompute.
"""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from appointments.analytics import track
from appointments.models import Appointment
from authorization.permissions import PermissionRequired
from authorization.services import PermissionService

from .models import AppointmentRiskPrediction, ReminderLog, RiskLevel
from .services import NoShowPredictionService


def _risk_payload(prediction) -> dict:
    """Endpoint shape: 0-100 score, 0-100 confidence (percent), reasons."""
    return {
        "risk_score": round(prediction.risk_score),
        "risk_level": prediction.risk_level,
        "confidence": round(prediction.confidence * 100),
        "reasons": list(prediction.reasons or []),
    }


def _owned_appointment_or_none(pk, user):
    try:
        return Appointment.objects.select_related("doctor").get(
            pk=pk, patient=user
        )
    except Appointment.DoesNotExist:
        return None


class ConfirmAttendanceView(APIView):
    """POST /api/appointments/{id}/confirm/ — patient confirms attendance.

    Stores confirmed_at + attendance_status, records the response, and
    recomputes risk (the confirmation lowers the score)."""

    def post(self, request, pk):
        appointment = _owned_appointment_or_none(pk, request.user)
        if appointment is None:
            return Response({"detail": "Not found."}, status=404)
        if appointment.status not in Appointment.ACTIVE_STATUSES:
            return Response(
                {"detail": "Only upcoming appointments can be confirmed."},
                status=400,
            )

        appointment.confirmed_at = timezone.now()
        appointment.attendance_status = Appointment.Attendance.CONFIRMED
        appointment.last_patient_response = ReminderLog.Response.CONFIRMED
        appointment.save(
            update_fields=[
                "confirmed_at",
                "attendance_status",
                "last_patient_response",
                "updated_at",
            ]
        )
        # The post_save signal recomputes risk synchronously; fetch it, and
        # lazily compute if (defensively) it was not produced.
        prediction = AppointmentRiskPrediction.objects.filter(
            appointment=appointment
        ).first()
        if prediction is None:
            prediction = NoShowPredictionService.predict_and_store(appointment)

        track("patient_confirmed", appointment=appointment.id)
        return Response(_risk_payload(prediction))


class AppointmentRiskView(APIView):
    """GET /api/appointments/{id}/risk/ — current no-show risk for an
    appointment (owner, or staff for any)."""

    def get(self, request, pk):
        if PermissionService.has_permission(request.user, "attendance.view"):
            appointment = get_object_or_404(
                Appointment.objects.select_related("doctor"), pk=pk
            )
        else:
            appointment = _owned_appointment_or_none(pk, request.user)
            if appointment is None:
                return Response({"detail": "Not found."}, status=404)

        prediction = AppointmentRiskPrediction.objects.filter(
            appointment=appointment
        ).first()
        if prediction is None:
            prediction = NoShowPredictionService.predict_and_store(appointment)
        return Response(_risk_payload(prediction))


class AttendanceAnalyticsView(APIView):
    """GET /api/analytics/attendance/ — staff-only attendance metrics."""

    permission_classes = [PermissionRequired]
    permission_code = "analytics.view"

    def get(self, request):
        no_show = Appointment.objects.filter(
            attendance_status=Appointment.Attendance.NO_SHOW
        ).count()
        completed = Appointment.objects.filter(
            status=Appointment.Status.COMPLETED
        ).count()
        resolved = no_show + completed
        avg_no_show_rate = (
            round(no_show / resolved * 100, 1) if resolved else 0.0
        )

        high_risk_patients = (
            AppointmentRiskPrediction.objects.filter(
                risk_level__in=[RiskLevel.HIGH, RiskLevel.CRITICAL],
                appointment__status__in=Appointment.ACTIVE_STATUSES,
            )
            .values("patient")
            .distinct()
            .count()
        )

        appointments_confirmed = Appointment.objects.filter(
            confirmed_at__isnull=False
        ).count()

        reconciled = AppointmentRiskPrediction.objects.filter(
            was_correct__isnull=False
        )
        n = reconciled.count()
        correct = reconciled.filter(was_correct=True).count()
        prediction_accuracy = round(correct / n * 100, 1) if n else 0.0

        # Active appointments currently predicted to miss (dashboard).
        predicted_no_shows = AppointmentRiskPrediction.objects.filter(
            risk_level__in=[RiskLevel.HIGH, RiskLevel.CRITICAL],
            appointment__status__in=Appointment.ACTIVE_STATUSES,
        ).count()

        # Reminder performance (from the log).
        reminders_sent = ReminderLog.objects.count()
        reminders_responded = ReminderLog.objects.filter(responded=True).count()
        reminder_response_rate = (
            round(reminders_responded / reminders_sent * 100, 1)
            if reminders_sent
            else 0.0
        )

        return Response(
            {
                # core metrics (unchanged, backward compatible)
                "average_no_show_rate": avg_no_show_rate,
                "high_risk_patients": high_risk_patients,
                "appointments_confirmed": appointments_confirmed,
                "appointments_no_show": no_show,
                "prediction_accuracy": prediction_accuracy,
                # additive dashboard metrics
                "predicted_no_shows": predicted_no_shows,
                "reminders_sent": reminders_sent,
                "reminders_responded": reminders_responded,
                "reminder_response_rate": reminder_response_rate,
            }
        )
