"""
Notification abstraction. Currently logs; swap the method bodies for
Expo Push / FCM / APNS later without touching business logic.
"""

import logging

logger = logging.getLogger("sanctuary.notifications")


class NotificationService:
    @staticmethod
    def send_waitlist_offer(entry):
        logger.info(
            "[PUSH] Appointment available! %s — %s at %s. "
            "Offer to %s, accept within %s minutes.",
            entry.doctor.name,
            entry.date,
            entry.offered_time,
            entry.patient.email,
            entry.OFFER_WINDOW_MINUTES,
        )

    @staticmethod
    def send_offer_expired(entry):
        logger.info(
            "[PUSH] Offer expired for %s (%s on %s at %s).",
            entry.patient.email,
            entry.doctor.name,
            entry.date,
            entry.offered_time,
        )

    @staticmethod
    def send_slot_confirmed(appointment):
        logger.info(
            "[PUSH] Slot confirmed for %s: %s on %s at %s.",
            appointment.patient.email,
            appointment.doctor.name,
            appointment.date,
            appointment.time,
        )

    @staticmethod
    def send_appointment_cancelled_leave(appointment):
        """Distinct from a normal patient-initiated cancel (which sends no
        notification at all) — this one exists because the patient didn't
        take the cancelling action themselves and needs to know why."""
        logger.info(
            "[PUSH] %s, your appointment with %s on %s at %s has been "
            "cancelled because the doctor is on approved leave.",
            appointment.patient.email,
            appointment.doctor.name,
            appointment.date,
            appointment.time,
        )

    # --- Real-Time Queue Optimization (Feature 3) -------------------------
    # Log-only today; swap bodies for Expo Push later. Business logic never
    # depends on these returning anything.

    @staticmethod
    def send_delay_notification(appointment, delay_minutes, recommended_arrival=None):
        logger.info(
            "[PUSH] Heads up %s: your %s appointment is running ~%s min behind. "
            "Recommended arrival: %s.",
            appointment.patient.email,
            appointment.doctor.name,
            delay_minutes,
            recommended_arrival,
        )

    @staticmethod
    def send_ready_notification(appointment, room=None):
        logger.info(
            "[PUSH] %s, please proceed to consultation with %s%s.",
            appointment.patient.email,
            appointment.doctor.name,
            f" (Room {room})" if room else "",
        )

    @staticmethod
    def send_doctor_running_late(doctor, date, delay_minutes):
        logger.info(
            "[PUSH] %s is running %s minutes behind schedule on %s.",
            doctor.name,
            delay_minutes,
            date,
        )

    @staticmethod
    def send_queue_updated(state):
        logger.info(
            "[PUSH] Queue updated for %s on %s — delay %s min, finishes ~%s.",
            state.doctor.name,
            state.date,
            state.current_delay_minutes,
            state.estimated_finish_time,
        )

    # --- AI No-Show Prediction & Attendance Intelligence (Feature 4) ------
    # Log-only today; designed for Expo Push. Business logic never depends on
    # these returning anything.

    @staticmethod
    def send_confirmation_request(appointment):
        logger.info(
            "[PUSH] %s, please confirm your appointment with %s on %s at %s.",
            appointment.patient.email,
            appointment.doctor.name,
            appointment.date,
            appointment.time,
        )

    @staticmethod
    def send_high_risk_reminder(appointment):
        logger.info(
            "[PUSH] %s, please confirm to keep your booking with %s on %s at %s.",
            appointment.patient.email,
            appointment.doctor.name,
            appointment.date,
            appointment.time,
        )

    @staticmethod
    def send_final_reminder(appointment):
        logger.info(
            "[PUSH] %s, your appointment with %s starts soon (%s at %s).",
            appointment.patient.email,
            appointment.doctor.name,
            appointment.date,
            appointment.time,
        )

    @staticmethod
    def send_no_show_followup(appointment):
        logger.info(
            "[PUSH] %s, we missed you. Would you like to reschedule with %s?",
            appointment.patient.email,
            appointment.doctor.name,
        )

    # --- Medical Records / EMR (Feature 5) --------------------------------
    # Log-only today; designed for Expo Push. Business logic never depends on
    # these returning anything.

    @staticmethod
    def send_followup_reminder(visit):
        logger.info(
            "[PUSH] %s, a follow-up with %s is due on %s.",
            visit.patient.email,
            visit.doctor.name,
            visit.follow_up_date,
        )

    @staticmethod
    def send_prescription_ready(visit):
        logger.info(
            "[PUSH] %s, your prescription from %s is ready.",
            visit.patient.email,
            visit.doctor.name,
        )

    @staticmethod
    def send_lab_report_uploaded(report):
        logger.info(
            "[PUSH] %s, a new lab report '%s' has been added to your records.",
            report.medical_visit.patient.email,
            report.title,
        )

    # --- Revenue & Billing (Feature 6) ------------------------------------
    # Log-only today; designed for Expo Push. Business logic never depends on
    # these returning anything.

    @staticmethod
    def send_invoice_generated(invoice):
        logger.info(
            "[PUSH] %s, invoice %s has been generated (total %s).",
            invoice.patient.email,
            invoice.invoice_number,
            invoice.total,
        )

    @staticmethod
    def send_payment_received(payment):
        logger.info(
            "[PUSH] %s, we received your payment of %s for %s.",
            payment.invoice.patient.email,
            payment.amount,
            payment.invoice.invoice_number,
        )

    @staticmethod
    def send_refund_processed(refund):
        logger.info(
            "[PUSH] %s, a refund of %s has been processed for %s.",
            refund.invoice.patient.email,
            refund.amount,
            refund.invoice.invoice_number,
        )

    @staticmethod
    def send_payment_reminder(invoice):
        logger.info(
            "[PUSH] %s, invoice %s has a pending balance of %s.",
            invoice.patient.email,
            invoice.invoice_number,
            invoice.balance,
        )
