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
    def _is_booking_allowed(booking):
        doctor = booking["doctor"]
        day = booking["date"]
        start_time = booking["time"]
        duration = booking["estimated_duration"]

        if not within_working_hours(doctor, day, start_time, duration):
            return False
        return find_overlap(doctor, day, start_time, duration) is None

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
