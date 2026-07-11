"""
NoShowPredictionService — the AI No-Show Prediction engine (Feature 4).

Phase 1 is a transparent rule engine (see the scoring table below). Once a
patient accumulates enough resolved history, the base rate switches to a
recency-weighted historical average ("ml" source) while the strong situational
modifiers (confirmed / checked-in) still apply — so confirming always reduces
risk and checking in always drives it to zero.

This module only *reads* Appointment/Doctor data and writes its own
AppointmentRiskPrediction / ReminderLog rows. It never mutates booking,
scheduling, waitlist, queue, or duration-prediction state.
"""

from dataclasses import dataclass, field

from django.utils import timezone

from appointments.analytics import track
from appointments.models import Appointment

from .models import (
    AppointmentRiskPrediction,
    PredictionSource,
    RiskLevel,
)

# --- Phase 1 rule weights ------------------------------------------------- #
PTS_PREVIOUS_NO_SHOW = 35
PTS_NEVER_VISITED_DOCTOR = 15
PTS_BOOKED_AHEAD = 10
PTS_MONDAY_MORNING = 5
PTS_FRIDAY_EVENING = 8
PTS_CONFIRMED = -30
PTS_CHECKED_IN = -100
PTS_THREE_SUCCESSFUL = -20
PTS_HIGH_ATTENDANCE = -25

# --- thresholds ----------------------------------------------------------- #
BOOKED_AHEAD_DAYS = 21
MORNING_END_HOUR = 12          # "morning" = before 12:00
EVENING_START_HOUR = 17        # "evening" = 17:00 or later
THREE_SUCCESSFUL = 3
HIGH_ATTENDANCE_RATE = 0.9
HIGH_ATTENDANCE_MIN_SAMPLES = 4
LEARNING_MIN_COMPLETED = 25    # switch to "ml" source at/above this
LEARNING_WINDOW = 20           # recency window for the weighted average

SCORE_MIN = 0
SCORE_MAX = 100


def _clamp(score: float) -> float:
    return float(max(SCORE_MIN, min(SCORE_MAX, score)))


def level_for(score: float) -> str:
    """0-25 LOW · 26-50 MEDIUM · 51-75 HIGH · 76-100 CRITICAL."""
    if score <= 25:
        return RiskLevel.LOW
    if score <= 50:
        return RiskLevel.MEDIUM
    if score <= 75:
        return RiskLevel.HIGH
    return RiskLevel.CRITICAL


@dataclass
class RiskResult:
    risk_score: float
    risk_level: str
    confidence: float
    reasons: list = field(default_factory=list)
    source: str = PredictionSource.RULE_BASED

    def as_dict(self):
        return {
            "risk_score": round(self.risk_score),
            "risk_level": self.risk_level,
            "confidence": round(self.confidence, 2),
            "reasons": list(self.reasons),
        }


@dataclass
class _PatientHistory:
    completed: int
    no_shows: int
    visited_doctor: bool
    weighted_no_show_rate: float | None  # recency-weighted, if resolved history

    @property
    def resolved(self) -> int:
        return self.completed + self.no_shows

    @property
    def attendance_rate(self) -> float | None:
        return (self.completed / self.resolved) if self.resolved else None


class NoShowPredictionService:
    # ------------------------------------------------------------------ #
    # History gathering (read-only)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _history(appointment) -> _PatientHistory:
        others = Appointment.objects.filter(patient=appointment.patient).exclude(
            pk=appointment.pk
        )
        completed = others.filter(status=Appointment.Status.COMPLETED).count()
        no_shows = others.filter(
            attendance_status=Appointment.Attendance.NO_SHOW
        ).count()
        visited_doctor = others.filter(
            doctor=appointment.doctor, status=Appointment.Status.COMPLETED
        ).exists()

        # Recency-weighted no-show rate over the resolved timeline
        # (attended = completed, missed = no_show), most-recent first.
        timeline = []
        for appt in (
            others.filter(status=Appointment.Status.COMPLETED)
            .order_by("-date", "-time")
            .values_list("date", flat=True)[:LEARNING_WINDOW]
        ):
            timeline.append(0)  # attended
        for appt in (
            others.filter(attendance_status=Appointment.Attendance.NO_SHOW)
            .order_by("-date", "-time")
            .values_list("date", flat=True)[:LEARNING_WINDOW]
        ):
            timeline.append(1)  # missed

        weighted = None
        if timeline:
            # Weight earlier items (more recent, since we prepended recent) more.
            total_w = 0.0
            acc = 0.0
            for i, missed in enumerate(timeline):
                w = 1.0 / (1 + i * 0.1)
                acc += w * missed
                total_w += w
            weighted = acc / total_w if total_w else None

        return _PatientHistory(
            completed=completed,
            no_shows=no_shows,
            visited_doctor=visited_doctor,
            weighted_no_show_rate=weighted,
        )

    # ------------------------------------------------------------------ #
    # Public prediction API
    # ------------------------------------------------------------------ #
    @staticmethod
    def predict(appointment) -> RiskResult:
        """Return a RiskResult for an appointment. Pure computation — reads
        history, writes nothing."""
        hist = NoShowPredictionService._history(appointment)
        reasons: list[str] = []

        confirmed = (
            appointment.confirmed_at is not None
            or appointment.attendance_status == Appointment.Attendance.CONFIRMED
        )
        checked_in = (
            appointment.patient_checked_in_at is not None
            or appointment.attendance_status == Appointment.Attendance.CHECKED_IN
        )

        # Decide the base score: ML (weighted history) once experienced, else
        # the rule floor of 0.
        use_ml = (
            hist.completed >= LEARNING_MIN_COMPLETED
            and hist.weighted_no_show_rate is not None
        )
        if use_ml:
            score = hist.weighted_no_show_rate * 100
            source = PredictionSource.ML
            reasons.append(
                f"Learned from {hist.completed} past visits "
                f"({round(hist.weighted_no_show_rate * 100)}% recent no-show rate)."
            )
        else:
            score = 0.0
            source = PredictionSource.RULE_BASED

        # --- additive risk drivers ---
        if hist.no_shows > 0:
            score += PTS_PREVIOUS_NO_SHOW
            reasons.append("Previous no-show on record")
        if not hist.visited_doctor:
            score += PTS_NEVER_VISITED_DOCTOR
            reasons.append("Never visited this doctor before")

        days_ahead = (appointment.date - appointment.created_at.date()).days if (
            appointment.created_at
        ) else (appointment.date - timezone.localdate()).days
        if days_ahead > BOOKED_AHEAD_DAYS:
            score += PTS_BOOKED_AHEAD
            reasons.append(f"Booked {days_ahead} days ahead")

        weekday = appointment.date.weekday()
        hour = appointment.time.hour
        if weekday == 0 and hour < MORNING_END_HOUR:
            score += PTS_MONDAY_MORNING
            reasons.append("Monday morning slot")
        if weekday == 4 and hour >= EVENING_START_HOUR:
            score += PTS_FRIDAY_EVENING
            reasons.append("Friday evening slot")

        # --- protective drivers ---
        if confirmed:
            score += PTS_CONFIRMED
            reasons.append("Patient confirmed attendance")
        if hist.completed >= THREE_SUCCESSFUL:
            score += PTS_THREE_SUCCESSFUL
            reasons.append("Three or more successful visits")
        if (
            hist.attendance_rate is not None
            and hist.attendance_rate >= HIGH_ATTENDANCE_RATE
            and hist.resolved >= HIGH_ATTENDANCE_MIN_SAMPLES
        ):
            score += PTS_HIGH_ATTENDANCE
            reasons.append("Strong attendance history")
        if checked_in:
            score += PTS_CHECKED_IN
            reasons = ["Patient has checked in"]

        score = _clamp(score)
        level = level_for(score)
        confidence = NoShowPredictionService._confidence(
            hist, source, confirmed, checked_in
        )
        if not reasons:
            reasons.append("No elevated no-show risk factors")

        return RiskResult(
            risk_score=score,
            risk_level=level,
            confidence=confidence,
            reasons=reasons,
            source=source,
        )

    @staticmethod
    def _confidence(hist, source, confirmed, checked_in) -> float:
        if checked_in:
            return 0.99
        if source == PredictionSource.ML:
            return min(0.95, 0.7 + 0.01 * hist.completed)
        # rule-based: grows a little with resolved history; confirming helps.
        base = 0.6 + min(0.2, 0.02 * hist.resolved)
        if confirmed:
            base = max(base, 0.9)
        return round(min(0.95, base), 2)

    # ------------------------------------------------------------------ #
    # Persistence + auto-prediction
    # ------------------------------------------------------------------ #
    @staticmethod
    def predict_and_store(appointment) -> AppointmentRiskPrediction:
        """Compute and persist the risk snapshot for an appointment, emitting
        the analytics events. Idempotent (update_or_create)."""
        result = NoShowPredictionService.predict(appointment)
        confirmed = (
            appointment.confirmed_at is not None
            or appointment.attendance_status == Appointment.Attendance.CONFIRMED
        )
        prediction, _ = AppointmentRiskPrediction.objects.update_or_create(
            appointment=appointment,
            defaults={
                "patient": appointment.patient,
                "doctor": appointment.doctor,
                "risk_score": result.risk_score,
                "risk_level": result.risk_level,
                "confidence": result.confidence,
                "prediction_source": result.source,
                "reasons": result.reasons,
                "confirmed": confirmed,
            },
        )
        track(
            "prediction_generated",
            appointment=appointment.id,
            risk_level=result.risk_level,
            risk_score=round(result.risk_score),
            source=result.source,
        )
        if result.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            track(
                "high_risk_detected",
                appointment=appointment.id,
                risk_level=result.risk_level,
            )
        return prediction
