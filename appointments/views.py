from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Avg, Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from authorization.permissions import PermissionRequired
from doctors.models import Doctor
from .analytics import track
from .models import (
    Appointment,
    AppointmentDurationPrediction,
    VisitType,
    WaitlistEntry,
)
from .notifications import NotificationService
from .predictions import DurationPredictionService
from .serializers import (
    AdminAppointmentSerializer,
    AppointmentSerializer,
    VisitTypeSerializer,
    WaitlistEntrySerializer,
)
from .services import (
    BookingConflict,
    BookingService,
    auto_fill_slot,
    expire_stale_offers,
)

SLOT_TAKEN = {"detail": "This slot was just booked by someone else."}


def _conflict_codes(exc: DRFValidationError) -> bool:
    codes = exc.get_codes()
    if not isinstance(codes, dict):
        return False
    if "unique" in codes.get("non_field_errors", []):
        return True
    return "overlap" in codes.get("time", [])


class VisitTypeListView(generics.ListAPIView):
    queryset = VisitType.objects.filter(active=True)
    serializer_class = VisitTypeSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None


class PredictDurationView(APIView):
    """POST /api/predictions/duration/  {doctor, visit_type}"""

    def post(self, request):
        doctor = get_object_or_404(
            Doctor, pk=request.data.get("doctor"), is_active=True
        )
        visit_type = get_object_or_404(
            VisitType, pk=request.data.get("visit_type"), active=True
        )
        track(
            "duration_prediction_requested",
            doctor=doctor.id,
            visit_type=visit_type.id,
            patient=request.user.id,
        )
        prediction = DurationPredictionService.predict_duration(
            doctor, request.user, visit_type
        )
        track(
            "duration_prediction_completed",
            minutes=prediction.minutes,
            confidence=prediction.confidence,
            source=prediction.source,
        )
        return Response(prediction.as_dict())


class AdminAppointmentPagination(PageNumberPagination):
    """Opt-in pagination for the admin appointments list.

    Pagination activates only when a `page_size` (and/or `page`) query param is
    supplied. Without it, `paginate_queryset` returns None and the view falls
    back to the existing full `{"results": [...]}` list, so existing callers are
    unaffected.
    """

    page_size = None
    page_size_query_param = "page_size"
    max_page_size = 200


class AdminAppointmentListView(APIView):
    """GET /api/admin/appointments/ — all appointments across the hospital,
    for staff.

    Filters: ?status= ?doctor=<id> ?date=YYYY-MM-DD ?date_from= ?date_to=
    ?q= (patient or doctor name/email). Reuses AdminAppointmentSerializer
    (adds read-only patient identity). Requires "appointment.view". This is
    additive and does not alter the patient-scoped GET /api/appointments/ list.

    Pagination (count/next/previous/results) is applied only when a page_size
    query param is supplied; otherwise the full {"results": [...]} list is
    returned, unchanged.
    """

    permission_classes = [PermissionRequired]
    permission_code = "appointment.view"

    def get(self, request):
        qs = Appointment.objects.select_related(
            "doctor", "doctor__specialty", "visit_type", "patient"
        )
        p = request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("doctor"):
            qs = qs.filter(doctor_id=p["doctor"])
        if p.get("date"):
            qs = qs.filter(date=p["date"])
        if p.get("date_from"):
            qs = qs.filter(date__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(date__lte=p["date_to"])
        if p.get("q"):
            term = p["q"]
            qs = qs.filter(
                Q(patient__name__icontains=term)
                | Q(patient__email__icontains=term)
                | Q(doctor__name__icontains=term)
            )
        qs = qs.order_by("-date", "-time")

        paginator = AdminAppointmentPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        if page is not None:
            data = AdminAppointmentSerializer(
                page, many=True, context={"request": request}
            ).data
            return paginator.get_paginated_response(data)

        return Response(
            {
                "results": AdminAppointmentSerializer(
                    qs, many=True, context={"request": request}
                ).data
            }
        )


class AppointmentListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/appointments/          → the authenticated user's appointments
    POST /api/appointments/          → book a slot (409 if taken/overlapping)
    """

    serializer_class = AppointmentSerializer

    def get_queryset(self):
        qs = Appointment.objects.filter(patient=self.request.user).select_related(
            "doctor", "doctor__specialty", "visit_type"
        )
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except DRFValidationError as exc:
            if _conflict_codes(exc):
                return Response(SLOT_TAKEN, status=status.HTTP_409_CONFLICT)
            raise
        source = serializer.validated_data.get("prediction_source", "rule_based")
        confidence = serializer.validated_data.get("prediction_confidence", 0.8)
        try:
            appointment = BookingService.book(
                patient=request.user,
                doctor=serializer.validated_data["doctor"],
                date=serializer.validated_data["date"],
                time=serializer.validated_data["time"],
                visit_type=serializer.validated_data.get("visit_type"),
                estimated_duration=serializer.validated_data.get("estimated_duration"),
                source=source,
                prediction_confidence=confidence,
                reason=serializer.validated_data.get("reason", ""),
            )
        except BookingConflict:
            return Response(SLOT_TAKEN, status=status.HTTP_409_CONFLICT)
        if source == "manual":
            track(
                "doctor_override_duration",
                appointment=appointment.id,
                minutes=appointment.estimated_duration,
            )
        return Response(
            self.get_serializer(appointment).data, status=status.HTTP_201_CREATED
        )


class AppointmentCancelView(APIView):
    """POST /api/appointments/{id}/cancel/ — cancel, then auto-fill the
    freed slot from the waitlist."""

    def post(self, request, pk):
        try:
            appointment = Appointment.objects.get(pk=pk, patient=request.user)
        except Appointment.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        if appointment.status not in Appointment.ACTIVE_STATUSES:
            return Response(
                {"detail": "Only upcoming appointments can be cancelled."},
                status=400,
            )
        appointment.status = Appointment.Status.CANCELLED
        appointment.save(update_fields=["status", "updated_at"])

        # Smart Waitlist: offer the freed slot to the first person in line.
        auto_fill_slot(
            appointment.doctor,
            appointment.date,
            appointment.time,
            appointment.estimated_duration or 30,
        )

        # Queue layer reacts to the lifecycle event (independent of booking).
        from queues.services import QueueService

        QueueService.recalculate_queue(appointment.doctor, appointment.date)

        return Response(AppointmentSerializer(appointment).data)


class AppointmentCompleteView(APIView):
    """POST /api/appointments/{id}/complete/  {actual_minutes?}

    Records the real consultation length so the prediction engine
    learns from it."""

    permission_classes = [PermissionRequired]
    permission_code = "appointment.edit"

    def post(self, request, pk):
        try:
            appointment = Appointment.objects.get(pk=pk)
        except Appointment.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        if appointment.status not in Appointment.ACTIVE_STATUSES:
            return Response(
                {"detail": "Only active appointments can be completed."},
                status=400,
            )

        actual = request.data.get("actual_minutes")
        try:
            actual = int(actual) if actual is not None else None
        except (TypeError, ValueError):
            return Response({"detail": "actual_minutes must be a number."}, status=400)
        if actual is not None and not 1 <= actual <= 480:
            return Response(
                {"detail": "actual_minutes must be between 1 and 480."}, status=400
            )

        started = timezone.make_aware(
            datetime.combine(appointment.date, appointment.time)
        )
        actual = actual or appointment.estimated_duration or 30

        appointment.status = Appointment.Status.COMPLETED
        appointment.actual_duration = actual
        appointment.consultation_started_at = (
            appointment.consultation_started_at or started
        )
        appointment.consultation_completed_at = (
            appointment.consultation_started_at + timedelta(minutes=actual)
        )
        appointment.save(
            update_fields=[
                "status",
                "actual_duration",
                "consultation_started_at",
                "consultation_completed_at",
                "updated_at",
            ]
        )

        prediction = getattr(appointment, "duration_prediction", None)
        if prediction:
            prediction.actual_minutes = actual
            prediction.save(update_fields=["actual_minutes"])
            error = abs(prediction.predicted_minutes - actual)
            track(
                "prediction_accuracy",
                appointment=appointment.id,
                predicted=prediction.predicted_minutes,
                actual=actual,
                error_minutes=error,
            )
        track("consultation_completed", appointment=appointment.id, actual=actual)

        # Queue layer reacts to completion (independent of booking/scheduling).
        from queues.services import QueueService

        QueueService.recalculate_queue(appointment.doctor, appointment.date)

        return Response(AppointmentSerializer(appointment).data)


class SchedulingAnalyticsView(APIView):
    """GET /api/analytics/scheduling/ — staff-only prediction metrics."""

    permission_classes = [PermissionRequired]
    permission_code = "analytics.view"

    def get(self, request):
        reconciled = AppointmentDurationPrediction.objects.filter(
            actual_minutes__isnull=False
        )
        totals = reconciled.aggregate(
            n=Count("id"),
            avg_predicted=Avg("predicted_minutes"),
            avg_actual=Avg("actual_minutes"),
        )
        overtime = 0
        idle = 0
        accuracy_sum = 0.0
        for p in reconciled:
            diff = p.actual_minutes - p.predicted_minutes
            if diff > 0:
                overtime += diff
            else:
                idle += -diff
            accuracy_sum += max(
                0.0, 1 - abs(diff) / max(p.actual_minutes, 1)
            )
        n = totals["n"] or 0
        return Response(
            {
                "reconciled_predictions": n,
                "avg_prediction_accuracy": round(accuracy_sum / n, 3) if n else None,
                "avg_predicted_minutes": round(totals["avg_predicted"] or 0, 1),
                "avg_consultation_minutes": round(totals["avg_actual"] or 0, 1),
                "total_overtime_minutes": overtime,
                "total_idle_minutes": idle,
            }
        )


class WaitlistListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/waitlist/   → the authenticated user's waitlist entries
    POST /api/waitlist/   → join a waitlist for a doctor + date (+ time)
    """

    serializer_class = WaitlistEntrySerializer

    def get_queryset(self):
        # Lazy sweep so expired offers cascade even without the cron job.
        expire_stale_offers()
        return (
            WaitlistEntry.objects.filter(patient=self.request.user)
            .select_related("doctor", "doctor__specialty")
            .order_by("-joined_at")
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = serializer.save(patient=request.user)
        track(
            "waitlist_joined",
            entry=entry.id,
            doctor=entry.doctor_id,
            date=str(entry.date),
        )
        return Response(
            {
                "message": "Added to waitlist",
                "entry": self.get_serializer(entry).data,
            },
            status=status.HTTP_201_CREATED,
        )


class WaitlistLeaveView(APIView):
    """DELETE /api/waitlist/{id}/ — leave the waitlist (or decline an offer)."""

    def delete(self, request, pk):
        with transaction.atomic():
            try:
                entry = WaitlistEntry.objects.select_for_update().get(
                    pk=pk, patient=request.user
                )
            except WaitlistEntry.DoesNotExist:
                return Response({"detail": "Not found."}, status=404)
            if entry.status not in WaitlistEntry.ACTIVE_STATUSES:
                return Response(
                    {"detail": "This waitlist entry is no longer active."},
                    status=400,
                )
            was_offered = entry.status == WaitlistEntry.Status.OFFERED
            entry.status = WaitlistEntry.Status.CANCELLED
            entry.save(update_fields=["status"])
            freed = (
                entry.doctor,
                entry.date,
                entry.offered_time,
                entry.offered_duration or 30,
            )

        if was_offered:
            track("offer_declined", entry=entry.id)
            # Declined: pass the held slot to the next person in line.
            auto_fill_slot(*freed)
        return Response(WaitlistEntrySerializer(entry).data)


class WaitlistAcceptView(APIView):
    """POST /api/waitlist/{id}/accept/ — accept an active offer.
    Exactly one patient can win the slot; others get 409."""

    def post(self, request, pk):
        expire_stale_offers()
        try:
            with transaction.atomic():
                try:
                    entry = WaitlistEntry.objects.select_for_update().get(
                        pk=pk, patient=request.user
                    )
                except WaitlistEntry.DoesNotExist:
                    return Response({"detail": "Not found."}, status=404)

                if entry.status != WaitlistEntry.Status.OFFERED:
                    return Response(
                        {"detail": "This entry has no active offer."}, status=400
                    )
                if entry.expires_at and entry.expires_at < timezone.now():
                    return Response(
                        {"detail": "This offer has expired."}, status=400
                    )

                appointment = BookingService.book(
                    patient=request.user,
                    doctor=entry.doctor,
                    date=entry.date,
                    time=entry.offered_time,
                    estimated_duration=entry.offered_duration or 30,
                    source="waitlist",
                    waitlist_entry=entry,
                    reason="Booked from waitlist",
                )
                entry.status = WaitlistEntry.Status.ACCEPTED
                entry.accepted_at = timezone.now()
                entry.save(update_fields=["status", "accepted_at"])
        except BookingConflict:
            return Response(SLOT_TAKEN, status=status.HTTP_409_CONFLICT)

        NotificationService.send_slot_confirmed(appointment)
        track("offer_accepted", entry=entry.id)
        track("slot_filled", appointment=appointment.id, doctor=entry.doctor_id)
        return Response(
            AppointmentSerializer(appointment).data, status=status.HTTP_201_CREATED
        )
