"""
DurationPredictionService — Feature 2 (AI Appointment Duration Prediction).

Phase 1: business rules seeded from VisitType defaults.
Phase 2 (automatic): once enough completed consultations exist for a
(doctor, visit type) pair, predictions switch to the historical average
("ml" source) and confidence grows with sample size.
"""

from dataclasses import dataclass

from .models import Appointment, VisitType

MIN_MINUTES = 5
MAX_MINUTES = 120
NEW_PATIENT_MINUTES = 45
FREQUENT_VISITOR_THRESHOLD = 5
FREQUENT_VISITOR_FACTOR = 0.8
LEARNING_MIN_SAMPLES = 3
LEARNING_WINDOW = 10


def _round5(minutes: float) -> int:
    return int(max(MIN_MINUTES, min(MAX_MINUTES, round(minutes / 5) * 5)))


@dataclass
class DurationPrediction:
    minutes: int
    confidence: float
    reason: str
    source: str  # rule_based | ml

    def as_dict(self):
        return {
            "estimated_duration": self.minutes,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
            "source": self.source,
        }


class DurationPredictionService:
    @staticmethod
    def predict_duration(doctor, patient, visit_type: VisitType) -> DurationPrediction:
        base = visit_type.default_duration

        completed_total = Appointment.objects.filter(
            patient=patient, status=Appointment.Status.COMPLETED
        ).count()
        completed_with_doctor = Appointment.objects.filter(
            patient=patient,
            doctor=doctor,
            status=Appointment.Status.COMPLETED,
        ).count()

        # Rule: brand-new patients need a longer first consultation.
        if completed_total == 0:
            minutes = _round5(max(base, NEW_PATIENT_MINUTES))
            return DurationPrediction(
                minutes=minutes,
                confidence=0.75,
                reason="New patient — first consultations usually take longer.",
                source="rule_based",
            )

        # Learning engine: average of recent actual durations for this
        # doctor + visit type.
        actuals = list(
            Appointment.objects.filter(
                doctor=doctor,
                visit_type=visit_type,
                status=Appointment.Status.COMPLETED,
                actual_duration__isnull=False,
            )
            .order_by("-consultation_completed_at", "-updated_at")
            .values_list("actual_duration", flat=True)[:LEARNING_WINDOW]
        )

        if len(actuals) >= LEARNING_MIN_SAMPLES:
            avg = sum(actuals) / len(actuals)
            minutes = avg
            reason = (
                f"Based on {len(actuals)} recent {visit_type.name.lower()} "
                f"consultations with this doctor (avg {round(avg)} min)."
            )
            confidence = min(0.95, 0.6 + 0.05 * len(actuals))
            source = "ml"
        else:
            minutes = base
            reason = f"{visit_type.name} visits typically take about {base} minutes."
            confidence = 0.85
            source = "rule_based"

        # Rule: frequent visitors are faster.
        if completed_with_doctor > FREQUENT_VISITOR_THRESHOLD:
            minutes *= FREQUENT_VISITOR_FACTOR
            reason += " Reduced 20% — returning patient the doctor knows well."
            confidence = min(0.95, confidence + 0.02)

        return DurationPrediction(
            minutes=_round5(minutes),
            confidence=confidence,
            reason=reason,
            source=source,
        )
