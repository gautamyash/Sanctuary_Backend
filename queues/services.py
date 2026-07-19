"""
QueueService — Real-Time Queue Optimization & Delay Management (Feature 3).

An independent layer that sits on top of existing appointments. It reads
appointment lifecycle timestamps (schedule, check-in, actual start/finish)
and derives live queue positions, wait times, and doctor delay — WITHOUT
modifying the booking engine, duration prediction, or the waitlist.

Estimation model ("anchor to schedule + actual events"):
  * Completed consultations contribute their real start/finish to a running
    clock.
  * The in-progress consultation (started, not finished) projects its finish
    from its actual start + predicted duration (never earlier than now).
  * Upcoming consultations start at max(scheduled time, running clock, now)
    and run for their predicted duration.
  * Doctor delay is signed: how far the current consultation's finish (or the
    next patient's start) has drifted from its scheduled time. It can be 0 or
    negative when the doctor is on time or ahead.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from appointments.analytics import track
from appointments.models import Appointment
from appointments.notifications import NotificationService

from .models import DoctorQueueState

DEFAULT_DURATION_MINUTES = 30
ARRIVAL_BUFFER_MINUTES = 10
RUNNING_LATE_THRESHOLD_MINUTES = 5

# Row states surfaced by the queue (distinct from Appointment.Status).
STATE_COMPLETED = "completed"
STATE_IN_PROGRESS = "in_progress"
STATE_WAITING = "waiting"


@dataclass
class QueueRow:
    appointment: Appointment
    estimated_start: datetime
    estimated_finish: datetime
    position: int | None  # 1-based among not-completed; None once completed
    state: str


@dataclass
class QueueComputation:
    doctor_id: int
    date: object
    rows: list  # list[QueueRow] in schedule order
    current: Appointment | None
    delay_minutes: int
    queue_started_at: datetime | None
    estimated_finish_time: datetime | None

    def row_for(self, appointment_id):
        for row in self.rows:
            if row.appointment.id == appointment_id:
                return row
        return None

    @property
    def waiting_count(self):
        return sum(1 for r in self.rows if r.state in (STATE_WAITING, STATE_IN_PROGRESS))


def _aware(day, time_obj):
    """Combine a date + time into a timezone-aware datetime in the current tz."""
    naive = datetime.combine(day, time_obj)
    if timezone.is_aware(naive):
        return naive
    return timezone.make_aware(naive, timezone.get_current_timezone())


class QueueService:
    # ------------------------------------------------------------------ #
    # Core computation
    # ------------------------------------------------------------------ #
    @staticmethod
    def _compute(doctor, date) -> QueueComputation:
        now = timezone.now()
        appts = list(
            Appointment.objects.filter(doctor=doctor, date=date)
            .exclude(status=Appointment.Status.CANCELLED)
            .order_by("time", "id")
        )

        rows: list[QueueRow] = []
        cursor = None  # running clock: estimated finish of the previous consult
        current = None
        position = 0
        started_stamps = []

        for appt in appts:
            scheduled_start = _aware(date, appt.time)
            duration = appt.estimated_duration or DEFAULT_DURATION_MINUTES

            if appt.status == Appointment.Status.COMPLETED:
                started = appt.consultation_started_at or scheduled_start
                finished = appt.consultation_completed_at or (
                    started + timedelta(minutes=appt.actual_duration or duration)
                )
                if appt.consultation_started_at:
                    started_stamps.append(appt.consultation_started_at)
                cursor = finished
                rows.append(
                    QueueRow(appt, started, finished, None, STATE_COMPLETED)
                )
                continue

            # Not completed, not cancelled -> confirmed/pending (queue member)
            position += 1
            if appt.consultation_started_at is not None:
                # In progress: project finish from the real start.
                started = appt.consultation_started_at
                started_stamps.append(started)
                est_finish = max(now, started + timedelta(minutes=duration))
                cursor = est_finish
                current = appt
                rows.append(
                    QueueRow(appt, started, est_finish, position, STATE_IN_PROGRESS)
                )
            else:
                # Waiting: earliest feasible start given schedule, the running
                # clock, and the fact we cannot start in the past.
                base = scheduled_start
                if cursor is not None and cursor > base:
                    base = cursor
                if base < now:
                    base = now
                est_start = base
                est_finish = est_start + timedelta(minutes=duration)
                cursor = est_finish
                rows.append(
                    QueueRow(appt, est_start, est_finish, position, STATE_WAITING)
                )

        delay_minutes = QueueService._delay_from_rows(rows, date)
        queue_started_at = min(started_stamps) if started_stamps else None
        return QueueComputation(
            doctor_id=doctor.id,
            date=date,
            rows=rows,
            current=current,
            delay_minutes=delay_minutes,
            queue_started_at=queue_started_at,
            estimated_finish_time=cursor,
        )

    @staticmethod
    def _delay_from_rows(rows, date) -> int:
        """Signed doctor delay in minutes.

        Uses the current in-progress consultation's projected finish vs its
        scheduled finish; if none is in progress, uses the next waiting
        patient's projected start vs their scheduled start.
        """
        for row in rows:
            if row.state == STATE_IN_PROGRESS:
                scheduled_start = _aware(date, row.appointment.time)
                duration = (
                    row.appointment.estimated_duration or DEFAULT_DURATION_MINUTES
                )
                scheduled_finish = scheduled_start + timedelta(minutes=duration)
                return round(
                    (row.estimated_finish - scheduled_finish).total_seconds() / 60
                )
            if row.state == STATE_WAITING:
                scheduled_start = _aware(date, row.appointment.time)
                return round(
                    (row.estimated_start - scheduled_start).total_seconds() / 60
                )
        return 0

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    @staticmethod
    def recalculate_queue(doctor, date) -> DoctorQueueState:
        """Rebuild and persist the queue snapshot for a doctor/day, refresh
        each appointment's queue_position, and emit notifications. Returns the
        saved DoctorQueueState. Safe to call from any lifecycle event."""
        comp = QueueService._compute(doctor, date)

        # Production hardening: this can write several Appointment rows
        # (queue_position) plus the DoctorQueueState snapshot as one logical
        # "rebuild the queue" operation — previously not atomic, so a failure
        # partway through could leave some appointments' positions updated
        # against a stale (or missing) snapshot.
        with transaction.atomic():
            # Persist queue_position on each appointment (None once completed).
            for row in comp.rows:
                new_pos = row.position
                if row.appointment.queue_position != new_pos:
                    row.appointment.queue_position = new_pos
                    row.appointment.save(update_fields=["queue_position", "updated_at"])

            state, _ = DoctorQueueState.objects.update_or_create(
                doctor=doctor,
                date=date,
                defaults={
                    "current_appointment": comp.current,
                    "queue_started_at": comp.queue_started_at,
                    "current_delay_minutes": comp.delay_minutes,
                    "estimated_finish_time": comp.estimated_finish_time,
                },
            )

        QueueService._notify(comp, state)
        track(
            "queue_updated",
            doctor=doctor.id,
            date=str(date),
            delay_minutes=comp.delay_minutes,
            waiting=comp.waiting_count,
        )
        return state

    @staticmethod
    def _notify(comp: QueueComputation, state: DoctorQueueState):
        NotificationService.send_queue_updated(state)
        running_late = comp.delay_minutes >= RUNNING_LATE_THRESHOLD_MINUTES
        if running_late:
            NotificationService.send_doctor_running_late(
                state.doctor, state.date, comp.delay_minutes
            )
        for row in comp.rows:
            if row.state != STATE_WAITING:
                continue
            if row.position == 1 or (comp.current is None and row.position == 1):
                # Front of the line and no one in the room -> they're up next.
                NotificationService.send_ready_notification(row.appointment)
            elif running_late:
                arrival = QueueService.estimate_arrival_time(
                    row.appointment, _comp=comp
                )
                NotificationService.send_delay_notification(
                    row.appointment, comp.delay_minutes, arrival
                )
                track("delay_notification_sent", appointment=row.appointment.id)

    @staticmethod
    def get_status(appointment) -> dict:
        """The queue-status payload for a single appointment."""
        comp = QueueService._compute(appointment.doctor, appointment.date)
        row = comp.row_for(appointment.id)
        now = timezone.now()

        if row is None:
            # Cancelled or not part of the active queue.
            return {
                "queue_position": None,
                "estimated_wait_minutes": None,
                "estimated_start": None,
                "estimated_finish": None,
                "doctor_running_late": comp.delay_minutes
                >= RUNNING_LATE_THRESHOLD_MINUTES,
                "delay_minutes": comp.delay_minutes,
            }

        if row.state == STATE_COMPLETED:
            wait_minutes = 0
        else:
            wait_minutes = max(
                0, round((row.estimated_start - now).total_seconds() / 60)
            )

        return {
            "queue_position": row.position,
            "estimated_wait_minutes": wait_minutes,
            "estimated_start": row.estimated_start.isoformat()
            if row.estimated_start
            else None,
            "estimated_finish": row.estimated_finish.isoformat()
            if row.estimated_finish
            else None,
            "doctor_running_late": comp.delay_minutes
            >= RUNNING_LATE_THRESHOLD_MINUTES,
            "delay_minutes": comp.delay_minutes,
        }

    @staticmethod
    def estimate_wait_time(appointment, _comp: QueueComputation | None = None) -> int:
        """Minutes from now until this appointment's estimated start (>= 0)."""
        comp = _comp or QueueService._compute(appointment.doctor, appointment.date)
        row = comp.row_for(appointment.id)
        if row is None or row.state == STATE_COMPLETED:
            return 0
        now = timezone.now()
        return max(0, round((row.estimated_start - now).total_seconds() / 60))

    @staticmethod
    def estimate_arrival_time(
        appointment, _comp: QueueComputation | None = None
    ) -> datetime | None:
        """Recommended arrival: a buffer before the estimated start, never in
        the past."""
        comp = _comp or QueueService._compute(appointment.doctor, appointment.date)
        row = comp.row_for(appointment.id)
        if row is None or row.state == STATE_COMPLETED or row.estimated_start is None:
            return None
        arrival = row.estimated_start - timedelta(minutes=ARRIVAL_BUFFER_MINUTES)
        now = timezone.now()
        return max(arrival, now)

    @staticmethod
    def build_timeline(doctor, date) -> list:
        """Ordered, serializable timeline of the day's queue for the doctor
        queue endpoint / frontend timeline view."""
        comp = QueueService._compute(doctor, date)
        timeline = []
        for row in comp.rows:
            appt = row.appointment
            timeline.append(
                {
                    "appointment_id": appt.id,
                    "patient_name": getattr(appt.patient, "name", None)
                    or appt.patient.email,
                    "scheduled_time": appt.time.strftime("%H:%M"),
                    "queue_position": row.position,
                    "state": row.state,
                    "estimated_start": row.estimated_start.isoformat()
                    if row.estimated_start
                    else None,
                    "estimated_finish": row.estimated_finish.isoformat()
                    if row.estimated_finish
                    else None,
                    "checked_in": appt.patient_checked_in_at is not None,
                    # Phase: Live Consultation & Queue Workflow — additive.
                    # Both already exist on Appointment (patient_checked_in_at
                    # backs the "checked_in" boolean above; attendance_status
                    # is the independent no-show layer's field); neither was
                    # previously serialized here. Existing consumers of this
                    # dict (DoctorQueueView, DoctorMeQueueView) only read the
                    # keys they already knew about, so this is safe to add.
                    "checked_in_at": appt.patient_checked_in_at.isoformat()
                    if appt.patient_checked_in_at
                    else None,
                    "attendance_status": appt.attendance_status,
                }
            )
        return {
            "doctor_id": doctor.id,
            "date": date.isoformat(),
            "delay_minutes": comp.delay_minutes,
            "doctor_running_late": comp.delay_minutes
            >= RUNNING_LATE_THRESHOLD_MINUTES,
            "estimated_finish_time": comp.estimated_finish_time.isoformat()
            if comp.estimated_finish_time
            else None,
            "timeline": timeline,
        }
