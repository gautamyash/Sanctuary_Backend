"""
Electronic Medical Record (EMR) models — Feature 5.

A completely independent app layered on top of the existing Appointment model.
Nothing here modifies booking, scheduling, queue, attendance, waitlist, or
duration-prediction logic; it only reads appointment data and stores its own
clinical records.
"""

from django.conf import settings
from django.db import models


class PatientRecord(models.Model):
    """One health record per patient."""

    class BloodGroup(models.TextChoices):
        A_POS = "A+"
        A_NEG = "A-"
        B_POS = "B+"
        B_NEG = "B-"
        AB_POS = "AB+"
        AB_NEG = "AB-"
        O_POS = "O+"
        O_NEG = "O-"

    class Smoking(models.TextChoices):
        NEVER = "never"
        FORMER = "former"
        CURRENT = "current"

    class Alcohol(models.TextChoices):
        NONE = "none"
        OCCASIONAL = "occasional"
        REGULAR = "regular"

    patient = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="medical_record",
    )
    blood_group = models.CharField(
        max_length=3, choices=BloodGroup.choices, blank=True, default=""
    )
    height_cm = models.FloatField(null=True, blank=True)
    weight_kg = models.FloatField(null=True, blank=True)
    bmi = models.FloatField(null=True, blank=True)
    smoking_status = models.CharField(
        max_length=8, choices=Smoking.choices, blank=True, default=""
    )
    alcohol = models.CharField(
        max_length=12, choices=Alcohol.choices, blank=True, default=""
    )
    pregnant = models.BooleanField(null=True, blank=True)
    emergency_contact = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # Keep BMI derived from height + weight so it never drifts.
        if self.height_cm and self.weight_kg:
            metres = self.height_cm / 100.0
            if metres > 0:
                self.bmi = round(self.weight_kg / (metres * metres), 1)
        else:
            self.bmi = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Record<{self.patient}>"


class Allergy(models.Model):
    class Severity(models.TextChoices):
        LOW = "LOW"
        MEDIUM = "MEDIUM"
        HIGH = "HIGH"
        LIFE_THREATENING = "LIFE_THREATENING"

    patient_record = models.ForeignKey(
        PatientRecord, on_delete=models.CASCADE, related_name="allergies"
    )
    name = models.CharField(max_length=120)
    severity = models.CharField(
        max_length=16, choices=Severity.choices, default=Severity.LOW
    )
    notes = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-severity", "name"]

    def __str__(self):
        return f"{self.name} ({self.severity})"


class Medication(models.Model):
    patient_record = models.ForeignKey(
        PatientRecord, on_delete=models.CASCADE, related_name="medications"
    )
    name = models.CharField(max_length=120)
    dosage = models.CharField(max_length=80, blank=True, default="")
    frequency = models.CharField(max_length=80, blank=True, default="")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-active", "name"]

    def __str__(self):
        return f"{self.name} ({'active' if self.active else 'stopped'})"


class MedicalVisit(models.Model):
    """A clinical record for one completed appointment."""

    appointment = models.OneToOneField(
        "appointments.Appointment",
        on_delete=models.CASCADE,
        related_name="medical_visit",
    )
    doctor = models.ForeignKey(
        "doctors.Doctor", on_delete=models.CASCADE, related_name="medical_visits"
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="medical_visits",
    )
    chief_complaint = models.CharField(max_length=255, blank=True, default="")
    diagnosis = models.CharField(max_length=255, blank=True, default="")
    clinical_notes = models.TextField(blank=True, default="")
    follow_up_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["patient", "created_at"]),
            models.Index(fields=["doctor", "created_at"]),
            models.Index(fields=["diagnosis"]),
        ]

    def __str__(self):
        return f"Visit<{self.patient} / {self.doctor} #{self.appointment_id}>"


class VitalSigns(models.Model):
    medical_visit = models.OneToOneField(
        MedicalVisit, on_delete=models.CASCADE, related_name="vitals"
    )
    temperature = models.FloatField(null=True, blank=True, help_text="°C")
    pulse = models.PositiveIntegerField(null=True, blank=True, help_text="bpm")
    blood_pressure = models.CharField(
        max_length=12, blank=True, default="", help_text="e.g. 120/80"
    )
    oxygen = models.FloatField(null=True, blank=True, help_text="SpO2 %")
    respiration = models.PositiveIntegerField(null=True, blank=True)
    blood_sugar = models.FloatField(null=True, blank=True, help_text="mg/dL")
    weight = models.FloatField(null=True, blank=True, help_text="kg")
    height = models.FloatField(null=True, blank=True, help_text="cm")

    def __str__(self):
        return f"Vitals<visit {self.medical_visit_id}>"


class Prescription(models.Model):
    medical_visit = models.ForeignKey(
        MedicalVisit, on_delete=models.CASCADE, related_name="prescriptions"
    )
    medicine = models.CharField(max_length=120)
    dosage = models.CharField(max_length=80, blank=True, default="")
    frequency = models.CharField(max_length=80, blank=True, default="")
    duration = models.CharField(max_length=80, blank=True, default="")
    instructions = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.medicine} (visit {self.medical_visit_id})"


class LabReport(models.Model):
    medical_visit = models.ForeignKey(
        MedicalVisit, on_delete=models.CASCADE, related_name="reports"
    )
    title = models.CharField(max_length=160)
    file = models.FileField(upload_to="lab_reports/%Y/%m/")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_reports",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.title} (visit {self.medical_visit_id})"
