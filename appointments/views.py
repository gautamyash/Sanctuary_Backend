from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
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
    doctor_scope_denied,
    expire_stale_offers,
)

User = get_user_model()

SLOT_TAKEN = {"detail": "This slot was just booked by someone else."}


def _conflict_codes(exc: DRFValidationError) -> bool:
    codes = exc.get_codes()
    if not isinstance(codes, dict):
        return False
    if "unique" in codes.get("non_field_errors", []):
        return True
    return "overlap" in codes.get("time", [])


def _get_object_or_404_safe(klass, **kwargs):
    """get_object_or_404, but also 404s on a malformed (non-numeric) pk
    (Production hardening) instead of letting Django's int() conversion
    raise an uncaught ValueError — confirmed via this Django version's
    get_object_or_404, which only catches DoesNotExist, not ValueError/
    TypeError. Only needed for pks that come from the request body/query
    params; URL-path pks are already safe via the <int:pk> converter."""
    try:
        return get_object_or_404(klass, **kwargs)
    except (ValueError, TypeError):
        raise Http404


class VisitTypeListView(generics.ListAPIView):
    queryset = VisitType.objects.filter(active=True)
    serializer_class = VisitTypeSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None


class PredictDurationView(APIView):
    """POST /api/predictions/duration/  {doctor, visit_type}"""

    def post(self, request):
        doctor = _get_object_or_404_safe(
            Doctor, pk=request.data.get("doctor"), is_active=True
        )
        visit_type = _get_object_or_404_safe(
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

    Filters: ?status= ?doctor=<id> ?patient=<id> ?date=YYYY-MM-DD ?date_from=
    ?date_to= ?q= (patient or doctor name/email). Reuses AdminAppointmentSerializer
    (adds read-only patient identity). Requires "appointment.view". This is
    additive and does not alter the patient-scoped GET /api/appointments/ list.

    ?patient= (Phase: Admin Medical Visit Management) lets the Admin Panel's
    "Add Visit" flow list a specific patient's own appointments so staff can
    pick one to complete — reusing this existing list rather than adding a
    new patient-scoped appointments endpoint.

    Pagination (count/next/previous/results) is applied only when a page_size
    query param is supplied; otherwise the full {"results": [...]} list is
    returned, unchanged.

    POST /api/admin/appointments/  {patient, doctor, date, time, visit_type?,
    estimated_duration?, reason?} — staff books an appointment on behalf of a
    specific patient (Phase: Admin Follow-up & Care Plan). Before this, no
    endpoint in the system let staff book for someone else: the patient-facing
    AppointmentListCreateView.create() hardcodes `patient=request.user`, so it
    can only ever book for whoever is authenticated. This reuses that exact
    same booking engine rather than writing a parallel one — AppointmentSerializer
    validates doctor/date/time/visit_type/estimated_duration/reason (the same
    validation, including working-hours and overlap checks, the patient-facing
    view already runs), and BookingService.book() performs the same
    locking/conflict-checked booking. The only difference is which user the
    resulting Appointment belongs to. Gated on "appointment.create" — a
    permission code already seeded in RBAC (granted to Doctor/Receptionist/
    Admin/Owner) but not wired to any view until now.
    """

    def get_permissions(self):
        self.permission_code = (
            "appointment.create" if self.request.method == "POST" else "appointment.view"
        )
        return [PermissionRequired()]

    def get_throttles(self):
        # Rate-limited (Production hardening) on POST only — this books an
        # appointment on someone else's behalf, the same class of action as
        # the patient-facing booking endpoint, so it gets the same style of
        # cap. GET (the admin appointments list/search table) is untouched:
        # scoping the throttle to POST via get_throttles() rather than a
        # class-wide throttle_classes avoids rate-limiting normal list
        # browsing/searching.
        if self.request.method == "POST":
            self.throttle_scope = "appointment_booking_admin"
            return [ScopedRateThrottle()]
        return []

    def get(self, request):
        qs = Appointment.objects.select_related(
            "doctor", "doctor__specialty", "visit_type", "patient"
        )
        p = request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        # Production hardening: doctor/patient are FK ids and date/date_from/
        # date_to are DateFields — Django's ORM raises a bare ValueError (ids)
        # or django.core.exceptions.ValidationError (dates) once the queryset
        # is evaluated, and DRF's exception handler only translates Http404/
        # PermissionDenied, so either would otherwise surface as an uncaught
        # 500 for a malformed query param. Converting/parsing up front, before
        # the value ever reaches the ORM, keeps this a clean 400.
        if p.get("doctor"):
            try:
                qs = qs.filter(doctor_id=int(p["doctor"]))
            except (TypeError, ValueError):
                return Response({"detail": "doctor must be a valid id."}, status=400)
        if p.get("patient"):
            try:
                qs = qs.filter(patient_id=int(p["patient"]))
            except (TypeError, ValueError):
                return Response({"detail": "patient must be a valid id."}, status=400)
        if p.get("date"):
            parsed = parse_date(p["date"])
            if parsed is None:
                return Response(
                    {"detail": "date must be in YYYY-MM-DD format."}, status=400
                )
            qs = qs.filter(date=parsed)
        if p.get("date_from"):
            parsed = parse_date(p["date_from"])
            if parsed is None:
                return Response(
                    {"detail": "date_from must be in YYYY-MM-DD format."}, status=400
                )
            qs = qs.filter(date__gte=parsed)
        if p.get("date_to"):
            parsed = parse_date(p["date_to"])
            if parsed is None:
                return Response(
                    {"detail": "date_to must be in YYYY-MM-DD format."}, status=400
                )
            qs = qs.filter(date__lte=parsed)
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

    def post(self, request):
        patient_id = request.data.get("patient")
        if not patient_id:
            return Response({"detail": "patient is required."}, status=400)
        patient = _get_object_or_404_safe(User, pk=patient_id)
        # Patient Archive hardening: self-service booking and waitlist
        # accept/join are already blocked for a deactivated account by
        # SimpleJWT's own CHECK_USER_IS_ACTIVE check (it rejects the token
        # outright), since those always book as request.user. This is the
        # one booking path where staff choose the patient explicitly, so it
        # needs its own explicit check.
        if not patient.is_active:
            return Response(
                {"detail": "This patient's account is inactive and cannot be booked."},
                status=400,
            )

        serializer = AppointmentSerializer(data=request.data)
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
                patient=patient,
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
        # Notification completeness: WaitlistAcceptView already notifies the
        # patient via this exact call for its own BookingService.book() call
        # site; the two direct-booking call sites (this one and the
        # patient-facing AppointmentListCreateView below) never did. This is
        # staff booking on the patient's behalf, so the patient especially
        # needs to be told — reusing the same generic, already-existing
        # message rather than adding a new one.
        NotificationService.send_slot_confirmed(appointment)
        return Response(
            AdminAppointmentSerializer(appointment, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
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

    def get_throttles(self):
        # Rate-limited (Production hardening) on POST (booking) only — GET
        # (a patient's own appointment list) is untouched so normal app
        # polling/refreshing is never rate-limited.
        if self.request.method == "POST":
            self.throttle_scope = "appointment_booking"
            return [ScopedRateThrottle()]
        return []

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
        # Notification completeness: same reasoning as AdminAppointmentListView.post()
        # below it and WaitlistAcceptView above — every BookingService.book()
        # call site should notify the patient consistently.
        NotificationService.send_slot_confirmed(appointment)
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
        if doctor_scope_denied(request.user, appointment):
            return Response(
                {"detail": "You can only complete your own appointments."},
                status=403,
            )
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

        # Production hardening: this touches two models (Appointment and, when
        # a duration prediction exists, AppointmentDurationPrediction) as one
        # logical "complete this consultation" operation — previously not
        # atomic, so a failure between the two saves could leave the
        # appointment marked completed without its prediction reconciled.
        with transaction.atomic():
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


class AppointmentRescheduleView(APIView):
    """POST /api/appointments/{id}/reschedule/  {date, time, doctor?,
    visit_type?, estimated_duration?, reason?}

    A true reschedule: moves the SAME Appointment row to a new slot instead
    of cancelling and creating a new one, so its history, any Billing
    invoice/payment linked to it, and its AppointmentDurationPrediction
    snapshot all stay attached exactly as they are — nothing here ever
    creates a new Appointment or a new prediction row.

    Reuses the existing dual-layer validation: AppointmentSerializer.validate()
    for a friendly error first (now exclude_id-aware so the appointment's own
    current slot never counts as a conflict with itself), then
    BookingService.reschedule() for the airtight, lock-protected check and
    the actual persistence — the same pattern every other booking call site
    already uses. Frees the vacated old slot through the existing Smart
    Waitlist auto-fill (only when the slot actually changed) and
    recalculates the queue for both the old and new day, mirroring
    AppointmentCancelView/AppointmentCompleteView's existing side-effect
    pattern. Gated on "appointment.reschedule" — a permission code already
    seeded in RBAC (granted to Doctor/Receptionist/Admin/Owner) but unused
    until now."""

    permission_classes = [PermissionRequired]
    permission_code = "appointment.reschedule"
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "appointment_reschedule"

    def post(self, request, pk):
        appointment = get_object_or_404(
            Appointment.objects.select_related("doctor"), pk=pk
        )
        # Object ownership (Production hardening): "appointment.reschedule" is
        # granted to Doctor too, same as "appointment.edit" on
        # AppointmentCompleteView — without this check, one doctor could
        # reschedule another doctor's appointment. Reuses the exact same
        # doctor_scope_denied() helper; Receptionist/Admin/Owner are
        # unaffected (it always returns False for them).
        if doctor_scope_denied(request.user, appointment):
            return Response(
                {"detail": "You can only reschedule your own appointments."},
                status=403,
            )
        if appointment.status not in Appointment.ACTIVE_STATUSES:
            return Response(
                {"detail": "Only active appointments can be rescheduled."},
                status=400,
            )
        if appointment.consultation_started_at is not None:
            return Response(
                {
                    "detail": "This consultation has already started and "
                    "cannot be rescheduled."
                },
                status=400,
            )

        data = {
            "doctor": request.data.get("doctor", appointment.doctor_id),
            "date": request.data.get("date"),
            "time": request.data.get("time"),
            "visit_type": request.data.get("visit_type", appointment.visit_type_id),
            "estimated_duration": request.data.get(
                "estimated_duration", appointment.estimated_duration
            ),
            "reason": request.data.get("reason", appointment.reason),
        }
        serializer = AppointmentSerializer(
            data=data,
            context={"request": request, "exclude_id": appointment.id},
        )
        try:
            serializer.is_valid(raise_exception=True)
        except DRFValidationError as exc:
            if _conflict_codes(exc):
                return Response(SLOT_TAKEN, status=status.HTTP_409_CONFLICT)
            raise

        old_doctor = appointment.doctor
        old_date = appointment.date
        old_time = appointment.time
        old_duration = appointment.estimated_duration or 30

        try:
            appointment = BookingService.reschedule(
                appointment,
                doctor=serializer.validated_data["doctor"],
                date=serializer.validated_data["date"],
                time=serializer.validated_data["time"],
                estimated_duration=serializer.validated_data.get("estimated_duration"),
                visit_type=serializer.validated_data.get("visit_type"),
                reason=serializer.validated_data.get("reason"),
            )
        except BookingConflict:
            return Response(SLOT_TAKEN, status=status.HTTP_409_CONFLICT)

        # Notification completeness: unlike a patient-initiated cancel (no
        # notification, documented on send_appointment_cancelled_leave — the
        # patient already knows because they did it), a reschedule here is
        # always staff-initiated ("appointment.reschedule" is not granted for
        # patient self-service), so the patient genuinely needs to be told
        # their slot moved. Reuses the same generic confirmation message
        # every other successful booking/reschedule now sends.
        NotificationService.send_slot_confirmed(appointment)

        track(
            "appointment_rescheduled",
            appointment=appointment.id,
            from_date=str(old_date),
            from_time=str(old_time),
            to_date=str(appointment.date),
            to_time=str(appointment.time),
        )

        moved = (
            old_doctor.id != appointment.doctor_id
            or old_date != appointment.date
            or old_time != appointment.time
        )
        if moved:
            # Only offer the vacated slot if it is actually vacated — a
            # no-op reschedule (identical doctor/date/time) leaves this
            # appointment still sitting in it, and auto_fill_slot trusts the
            # caller rather than re-checking that itself.
            auto_fill_slot(old_doctor, old_date, old_time, old_duration)

        # Queue layer reacts on both affected days (independent of booking).
        from queues.services import QueueService

        QueueService.recalculate_queue(old_doctor, old_date)
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

    def get_throttles(self):
        # Rate-limited (Production hardening) on POST (join) only — GET (a
        # patient's own waitlist entries) is untouched.
        if self.request.method == "POST":
            self.throttle_scope = "waitlist_join"
            return [ScopedRateThrottle()]
        return []

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
