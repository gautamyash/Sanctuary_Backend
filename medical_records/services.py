"""
MedicalTimelineService — assembles a patient's clinical timeline (Feature 5).

Reads appointment lifecycle timestamps (schedule, check-in, consultation) and
attendance status strictly read-only, then layers the visit's clinical events
(diagnosis, prescriptions, lab reports, follow-up) on top. It never mutates any
existing model.
"""

from appointments.models import Appointment


def _iso(dt):
    return dt.isoformat() if dt else None


class MedicalTimelineService:
    # Ordered pipeline stages the frontend renders.
    STAGES = (
        "appointment",
        "checked_in",
        "consultation",
        "diagnosis",
        "prescription",
        "lab_report",
        "follow_up",
    )

    @staticmethod
    def build_for_visit(visit) -> list:
        """Ordered timeline events for a single visit."""
        appt = visit.appointment
        events = []

        events.append(
            {
                "stage": "appointment",
                "title": "Appointment",
                "timestamp": _iso(appt.created_at),
                "detail": f"{visit.doctor.name} · {appt.date} {appt.time.strftime('%H:%M')}",
                "visit_id": visit.id,
            }
        )

        if appt.patient_checked_in_at:
            events.append(
                {
                    "stage": "checked_in",
                    "title": "Checked in",
                    "timestamp": _iso(appt.patient_checked_in_at),
                    "detail": None,
                    "visit_id": visit.id,
                }
            )

        events.append(
            {
                "stage": "consultation",
                "title": "Consultation",
                "timestamp": _iso(
                    appt.consultation_started_at or visit.created_at
                ),
                "detail": visit.chief_complaint or None,
                "visit_id": visit.id,
            }
        )

        if visit.diagnosis:
            events.append(
                {
                    "stage": "diagnosis",
                    "title": "Diagnosis",
                    "timestamp": _iso(visit.created_at),
                    "detail": visit.diagnosis,
                    "visit_id": visit.id,
                }
            )

        prescriptions = list(visit.prescriptions.all())
        if prescriptions:
            events.append(
                {
                    "stage": "prescription",
                    "title": "Prescription",
                    "timestamp": _iso(visit.created_at),
                    "detail": ", ".join(p.medicine for p in prescriptions),
                    "visit_id": visit.id,
                }
            )

        for report in visit.reports.all():
            events.append(
                {
                    "stage": "lab_report",
                    "title": "Lab report",
                    "timestamp": _iso(report.uploaded_at),
                    "detail": report.title,
                    "visit_id": visit.id,
                }
            )

        if visit.follow_up_date:
            events.append(
                {
                    "stage": "follow_up",
                    "title": "Follow-up",
                    "timestamp": None,
                    "detail": str(visit.follow_up_date),
                    "visit_id": visit.id,
                }
            )

        return events

    @staticmethod
    def build_for_patient(patient) -> list:
        """Flat, newest-first timeline across all of a patient's visits."""
        visits = (
            patient.medical_visits.select_related("appointment", "doctor")
            .prefetch_related("prescriptions", "reports")
            .all()
        )
        timeline = []
        for visit in visits:
            timeline.extend(MedicalTimelineService.build_for_visit(visit))
        # Newest first; events without a timestamp (e.g. future follow-up) last.
        timeline.sort(key=lambda e: (e["timestamp"] or ""), reverse=True)
        return timeline
