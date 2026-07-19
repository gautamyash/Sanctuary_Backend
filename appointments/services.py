"""
Smart Waitlist auto-fill engine.

When a confirmed/pending appointment is cancelled, `auto_fill_slot`
offers the freed slot to the earliest matching waitlist entry.
Offers expire after WaitlistEntry.OFFER_WINDOW_MINUTES; `expire_stale_offers`
expires them and cascades the offer to the next patient in line.
"""

from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .analytics import track
from .models import (
    Appointment,
    AppointmentDurationPrediction,
    DoctorDayBookingLock,
    WaitlistEntry,
)
from .notifications import NotificationService


class BookingConflict(Exception):
    """Raised when a booking cannot be completed because the slot is no longer available."""


class BookingService:
    """Shared appointment booking engine.

    Booking validation remains in the serializer for compatibility, while the
    service becomes the single persistence point for direct booking creation and
    prediction snapshot creation.
    """

    @classmethod
    def book(
        cls,
        *,
        patient,
        doctor,
        date,
        time,
        visit_type=None,
        estimated_duration=None,
        source=None,
        waitlist_entry=None,
        reason="",
        prediction_confidence=0.8,
    ):
        booking = cls._prepare_booking(
            patient=patient,
            doctor=doctor,
            date=date,
            time=time,
            visit_type=visit_type,
            estimated_duration=estimated_duration,
            source=source,
            waitlist_entry=waitlist_entry,
            reason=reason,
            prediction_confidence=prediction_confidence,
        )
        cls._validate_booking(booking)
        with transaction.atomic():
            lock = cls._get_booking_lock(booking["doctor"], booking["date"])
            lock = DoctorDayBookingLock.objects.select_for_update().get(pk=lock.pk)
            if not cls._is_booking_allowed(booking):
                raise BookingConflict
            try:
                appointment = cls._create_booking(booking)
            except IntegrityError as exc:
                raise BookingConflict from exc
            cls._create_prediction(appointment, booking)
            return appointment

    @classmethod
    def reschedule(
        cls,
        appointment,
        *,
        doctor=None,
        date,
        time,
        estimated_duration=None,
        visit_type=None,
        reason=None,
    ):
        """Move `appointment` to a new doctor/date/time in place (Phase:
        Appointment Rescheduling) — the same row, same id, so its history,
        any Billing invoice/payment linked to it, and its
        AppointmentDurationPrediction snapshot all stay attached exactly as
        they are. Not a cancel+create: this never creates a new Appointment
        or a new prediction row.

        Reuses the exact lock + _is_booking_allowed check book() uses, with
        the appointment's own current row excluded from the overlap check
        via exclude_id (it is moving, not double-booking itself)."""
        doctor = doctor or appointment.doctor
        duration = (
            estimated_duration
            or appointment.estimated_duration
            or (visit_type.default_duration if visit_type else None)
            or 30
        )

        booking = {
            "patient": appointment.patient,
            "doctor": doctor,
            "date": date,
            "time": time,
            "estimated_duration": duration,
        }
        cls._validate_booking(booking)

        with transaction.atomic():
            lock = cls._get_booking_lock(doctor, date)
            lock = DoctorDayBookingLock.objects.select_for_update().get(pk=lock.pk)
            if not cls._is_booking_allowed(booking, exclude_id=appointment.id):
                raise BookingConflict

            appointment.doctor = doctor
            appointment.date = date
            appointment.time = time
            appointment.estimated_duration = duration
            if visit_type is not None:
                appointment.visit_type = visit_type
            if reason is not None:
                appointment.reason = reason
            # The old timestamp belonged to the previous slot; CheckInView
            # requires date == today and not-yet-checked-in, so a stale
            # check-in from the old date must not block checking in for the
            # new one.
            appointment.patient_checked_in_at = None
            try:
                appointment.save(
                    update_fields=[
                        "doctor",
                        "date",
                        "time",
                        "estimated_duration",
                        "visit_type",
                        "reason",
                        "patient_checked_in_at",
                        "updated_at",
                    ]
                )
            except IntegrityError as exc:
                raise BookingConflict from exc
            return appointment

    @staticmethod
    def _prepare_booking(
        *,
        patient,
        doctor,
        date,
        time,
        visit_type=None,
        estimated_duration=None,
        source=None,
        waitlist_entry=None,
        reason="",
        prediction_confidence=0.8,
    ):
        duration = estimated_duration or (
            visit_type.default_duration if visit_type else 30
        )
        if waitlist_entry is not None and not reason:
            reason = "Booked from waitlist"
        return {
            "patient": patient,
            "doctor": doctor,
            "date": date,
            "time": time,
            "visit_type": visit_type,
            "estimated_duration": duration,
            "source": source or AppointmentDurationPrediction.Source.RULE_BASED,
            "waitlist_entry": waitlist_entry,
            "reason": reason,
            "prediction_confidence": prediction_confidence,
        }

    @staticmethod
    def _validate_booking(booking):
        required_fields = ["patient", "doctor", "date", "time"]
        missing = [field for field in required_fields if booking.get(field) is None]
        if missing:
            raise ValueError(f"Missing required booking fields: {', '.join(missing)}")
        return None

    @staticmethod
    def _get_booking_lock(doctor, date):
        """Create the per-doctor/day lock row used to serialize concurrent bookings.

        This helper is intentionally inert for now: it only materializes the
        lock row so the next step can acquire it without changing booking
        behavior.
        """
        return DoctorDayBookingLock.objects.get_or_create(doctor=doctor, date=date)[0]

    @staticmethod
    def _is_booking_allowed(booking, exclude_id=None):
        doctor = booking["doctor"]
        day = booking["date"]
        start_time = booking["time"]
        duration = booking["estimated_duration"]

        # Single authoritative check: every booking path funnels through
        # book() -> _is_booking_allowed (self-service, admin follow-up
        # booking, and WaitlistAcceptView, which calls BookingService.book()
        # directly and never passes through AppointmentSerializer.validate()).
        # So this is where an approved leave must be enforced to actually be
        # airtight, not just at the serializer layer.
        #
        # exclude_id (Phase: Appointment Rescheduling) — only ever passed by
        # reschedule(), so the appointment being moved never registers as a
        # conflict with its own current slot. book()'s call site below never
        # passes it, so nothing changes for fresh bookings.
        if is_doctor_on_leave(doctor, day):
            return False
        if not within_working_hours(doctor, day, start_time, duration):
            return False
        return (
            find_overlap(doctor, day, start_time, duration, exclude_id=exclude_id)
            is None
        )

    @staticmethod
    def _create_booking(booking):
        """Single persistence point for direct booking creation."""
        return Appointment.objects.create(
            **BookingService._appointment_kwargs(booking)
        )

    @staticmethod
    def _appointment_kwargs(booking):
        return {
            "doctor": booking["doctor"],
            "patient": booking["patient"],
            "date": booking["date"],
            "time": booking["time"],
            "visit_type": booking["visit_type"],
            "estimated_duration": booking["estimated_duration"],
            "status": Appointment.Status.CONFIRMED,
            "reason": booking["reason"],
        }

    @staticmethod
    def _create_prediction(appointment, booking):
        if not appointment.visit_type_id:
            return None
        return AppointmentDurationPrediction.objects.create(
            doctor=appointment.doctor,
            patient=appointment.patient,
            visit_type=appointment.visit_type,
            appointment=appointment,
            predicted_minutes=appointment.estimated_duration,
            confidence=booking["prediction_confidence"],
            prediction_source=booking["source"],
        )


def slot_end(day, start_time, minutes: int):
    """End time of a slot starting at `start_time` lasting `minutes`."""
    return (datetime.combine(day, start_time) + timedelta(minutes=minutes)).time()


def within_working_hours(doctor, day, start_time, minutes: int) -> bool:
    """True if [start, start+minutes] fits inside one of the doctor's
    working blocks for that weekday."""
    start_dt = datetime.combine(day, start_time)
    end_dt = start_dt + timedelta(minutes=minutes)
    for block in doctor.schedules.filter(weekday=day.weekday()):
        block_start = datetime.combine(day, block.start_time)
        block_end = datetime.combine(day, block.end_time)
        if start_dt >= block_start and end_dt <= block_end:
            return True
    return False


def is_doctor_on_leave(doctor, day) -> bool:
    """True if `doctor` has an *approved* DoctorLeave covering `day`
    (Phase: Advanced Doctor Schedule & Leave Management).

    Only approved leaves block scheduling — pending/rejected leaves have
    no scheduling effect, matching the existing staff-approval workflow
    already on DoctorLeave.status (a leave a doctor merely *requested*
    shouldn't silently close their calendar before anyone reviews it).

    Imports DoctorLeave lazily, mirroring the existing cross-app import
    convention already used elsewhere (e.g. doctors/views.py importing
    from appointments.services inside method bodies) rather than a
    module-level import, since `doctors` has no reason to otherwise
    depend on `appointments` at import time.

    Reused by every booking/slot-generation call site (BookingService.
    _is_booking_allowed, AppointmentSerializer.validate, DoctorSlotsView,
    DoctorSmartSlotsView) so they all agree on exactly what "on leave"
    means — the same way find_overlap/within_working_hours are already
    shared across those same call sites, rather than each reimplementing
    its own leave check."""
    from doctors.models import DoctorLeave

    return DoctorLeave.objects.filter(
        doctor=doctor,
        status=DoctorLeave.Status.APPROVED,
        start_date__lte=day,
        end_date__gte=day,
    ).exists()


def handle_leave_conflicts(leave) -> list:
    """When a DoctorLeave transitions to approved, any appointment already
    booked before the leave existed/was reviewed can now fall inside the
    doctor's blocked date range — is_doctor_on_leave() only stops *new*
    bookings, it doesn't retroactively touch ones made earlier, so without
    this the doctor's calendar and the booking stay silently out of sync.

    Reuses the exact same cancellation side-effects AppointmentCancelView
    already uses (free the slot to the waitlist via auto_fill_slot,
    recalculate that day's queue) rather than a second cancellation path,
    plus a dedicated notification so the patient knows why. A consultation
    already in progress is left alone, mirroring the same guard
    AppointmentRescheduleView uses, since a leave doesn't undo a visit
    that's already happening.

    Called from doctors/views.py (both the direct-create-as-approved and
    approve-via-update paths) with a lazy import back into this module, the
    same cross-app convention is_doctor_on_leave()'s docstring documents.

    Returns the list of cancelled appointments."""
    conflicts = list(
        Appointment.objects.filter(
            doctor=leave.doctor,
            date__gte=leave.start_date,
            date__lte=leave.end_date,
            status__in=Appointment.ACTIVE_STATUSES,
            consultation_started_at__isnull=True,
        ).select_related("doctor", "patient")
    )
    if not conflicts:
        return []

    from queues.services import QueueService

    affected_dates = set()
    for appointment in conflicts:
        appointment.status = Appointment.Status.CANCELLED
        appointment.save(update_fields=["status", "updated_at"])
        affected_dates.add(appointment.date)

        auto_fill_slot(
            appointment.doctor,
            appointment.date,
            appointment.time,
            appointment.estimated_duration or 30,
        )
        NotificationService.send_appointment_cancelled_leave(appointment)
        track(
            "appointment_cancelled_leave",
            appointment=appointment.id,
            leave=leave.id,
        )

    for d in affected_dates:
        QueueService.recalculate_queue(leave.doctor, d)

    return conflicts


def doctor_scope_denied(user, appointment) -> bool:
    """True if `user` holds a Doctor account (a linked Doctor profile) and
    that profile is not the one assigned to `appointment` — i.e. a doctor
    reaching for another doctor's appointment via the live-queue actions
    (start consultation / complete / mark no-show).

    Reuses the exact same ownership signal doctors/me_views.py and
    IsLinkedDoctor already use (`user.doctor_profile`) rather than checking
    role names directly — a user only has this attribute set at all if
    their account is provisioned as a Doctor (see
    authorization/profile_provisioning.py), so Admin/Owner/Receptionist/
    Nurse accounts have no `doctor_profile` and this is always False for
    them, leaving their existing (broader) access on these endpoints
    completely unchanged."""
    doctor_profile = getattr(user, "doctor_profile", None)
    return doctor_profile is not None and doctor_profile.id != appointment.doctor_id


def active_intervals(doctor, day, exclude_id=None):
    """The busy [start_dt, end_dt) intervals of a doctor's active
    (confirmed/pending) appointments on `day`, each paired with its
    appointment. Loaded in a single query so callers can test many
    candidate slots without re-hitting the database per slot.

    Returns a list of (start_dt, end_dt, appointment) tuples.
    """
    qs = Appointment.objects.filter(
        doctor=doctor, date=day, status__in=Appointment.ACTIVE_STATUSES
    )
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    intervals = []
    for appt in qs:
        a_start = datetime.combine(day, appt.time)
        a_end = a_start + timedelta(minutes=appt.estimated_duration or 30)
        intervals.append((a_start, a_end, appt))
    return intervals


def intervals_overlap(start_dt, end_dt, intervals):
    """Return the first (start, end, appointment) in `intervals` whose
    interval overlaps the half-open range [start_dt, end_dt), or None.

    Half-open semantics mean back-to-back (adjacent) appointments do not
    count as overlapping: an appointment ending exactly at start_dt is fine.
    """
    for a_start, a_end, appt in intervals:
        if start_dt < a_end and end_dt > a_start:
            return a_start, a_end, appt
    return None


def find_overlap(doctor, day, start_time, minutes: int, exclude_id=None):
    """Return the first active appointment whose interval overlaps
    [start, start+minutes), or None.

    Thin wrapper over the shared interval-overlap engine
    (active_intervals + intervals_overlap) so booking validation, the
    smart-slots endpoint, and the legacy slots endpoint all agree on
    exactly what "taken" means. Public return contract is unchanged:
    the overlapping Appointment, or None.
    """
    start_dt = datetime.combine(day, start_time)
    end_dt = start_dt + timedelta(minutes=minutes)
    hit = intervals_overlap(
        start_dt, end_dt, active_intervals(doctor, day, exclude_id)
    )
    return hit[2] if hit else None


def generate_smart_slots(doctor, day, duration: int):
    """Dynamically pack bookable start times of `duration` minutes around
    existing variable-length appointments. Returns (free, booked)."""
    busy = []
    for appt in Appointment.objects.filter(
        doctor=doctor, date=day, status__in=Appointment.ACTIVE_STATUSES
    ).order_by("time"):
        a_start = datetime.combine(day, appt.time)
        a_end = a_start + timedelta(minutes=appt.estimated_duration or 30)
        busy.append((a_start, a_end))

    step = timedelta(minutes=duration)
    free = []
    for block in doctor.schedules.filter(weekday=day.weekday()):
        cursor = datetime.combine(day, block.start_time)
        block_end = datetime.combine(day, block.end_time)
        while cursor + step <= block_end:
            candidate_end = cursor + step
            clash = next(
                (b for b in busy if cursor < b[1] and candidate_end > b[0]),
                None,
            )
            if clash:
                # jump to the end of the clashing appointment and re-pack
                cursor = clash[1]
                continue
            free.append(
                {
                    "time": cursor.time().strftime("%H:%M"),
                    "label": cursor.time().strftime("%I:%M %p").lstrip("0"),
                    "end": candidate_end.time().strftime("%H:%M"),
                    "end_label": candidate_end.time()
                    .strftime("%I:%M %p")
                    .lstrip("0"),
                    "duration": duration,
                }
            )
            cursor = candidate_end

    booked = [
        {
            "time": b[0].time().strftime("%H:%M"),
            "label": b[0].time().strftime("%I:%M %p").lstrip("0"),
            "duration": int((b[1] - b[0]).total_seconds() // 60),
        }
        for b in busy
    ]
    return free, booked


def auto_fill_slot(doctor, date, time, duration: int = 30) -> WaitlistEntry | None:
    """Offer the freed (doctor, date, time) slot to the first eligible
    waiting patient. Returns the entry that received the offer, if any."""
    with transaction.atomic():
        candidates = (
            WaitlistEntry.objects.select_for_update()
            .filter(doctor=doctor, date=date, status=WaitlistEntry.Status.WAITING)
            .filter(Q(preferred_time__isnull=True) | Q(preferred_time=time))
            .order_by("joined_at")
        )
        for entry in candidates:
            # Skip patients who already hold an active appointment in
            # this exact slot (edge case, e.g. booked directly meanwhile).
            clash = Appointment.objects.filter(
                patient=entry.patient,
                doctor=doctor,
                date=date,
                time=time,
                status__in=Appointment.ACTIVE_STATUSES,
            ).exists()
            if clash:
                continue

            now = timezone.now()
            entry.status = WaitlistEntry.Status.OFFERED
            entry.offered_time = time
            entry.offered_duration = duration
            entry.offered_at = now
            entry.expires_at = now + timedelta(
                minutes=WaitlistEntry.OFFER_WINDOW_MINUTES
            )
            entry.save(
                update_fields=[
                    "status",
                    "offered_time",
                    "offered_duration",
                    "offered_at",
                    "expires_at",
                ]
            )
            NotificationService.send_waitlist_offer(entry)
            track(
                "offer_sent",
                entry=entry.id,
                doctor=doctor.id,
                date=str(date),
                time=str(time),
            )
            return entry
    return None


def expire_stale_offers() -> int:
    """Expire overdue offers and cascade each freed slot to the next
    waiting patient. Returns the number of offers expired."""
    expired_count = 0
    while True:
        with transaction.atomic():
            entry = (
                WaitlistEntry.objects.select_for_update()
                .filter(
                    status=WaitlistEntry.Status.OFFERED,
                    expires_at__lt=timezone.now(),
                )
                .order_by("expires_at")
                .first()
            )
            if entry is None:
                break
            entry.status = WaitlistEntry.Status.EXPIRED
            entry.save(update_fields=["status"])
            freed = (
                entry.doctor,
                entry.date,
                entry.offered_time,
                entry.offered_duration or 30,
            )

        expired_count += 1
        NotificationService.send_offer_expired(entry)
        track("offer_expired", entry=entry.id)
        # Offer the same slot to the next person in line.
        auto_fill_slot(*freed)
    return expired_count
