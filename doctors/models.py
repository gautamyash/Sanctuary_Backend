from django.conf import settings
from django.db import models


class Specialty(models.Model):
    name = models.CharField(max_length=80, unique=True)
    icon = models.CharField(
        max_length=60,
        blank=True,
        help_text="Ionicons icon name used by the mobile app",
    )

    class Meta:
        verbose_name_plural = "specialties"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Doctor(models.Model):
    name = models.CharField(max_length=150)
    specialty = models.ForeignKey(
        Specialty, on_delete=models.PROTECT, related_name="doctors"
    )
    hospital = models.CharField(max_length=200)
    address = models.CharField(max_length=255, blank=True)
    distance_km = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    rating = models.DecimalField(max_digits=2, decimal_places=1, default=0)
    reviews = models.PositiveIntegerField(default=0)
    years_experience = models.PositiveIntegerField(default=0)
    fee = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    about = models.TextField(blank=True)
    photo = models.URLField(blank=True, default="")
    color = models.CharField(max_length=9, default="#003d9b")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Live status (Doctor Management module). Independent booleans rather
    # than a single choice field, matching how the design treats them as
    # separate signals (a doctor can be neither on_duty nor on_leave, e.g.
    # off-shift).
    on_duty = models.BooleanField(default=False)
    on_leave = models.BooleanField(default=False)

    # Physical location (Doctor Management module). Free-text, matching the
    # existing `address` field's style rather than a constrained/enum
    # location model, since none is required.
    wing = models.CharField(max_length=50, blank=True, default="")
    floor = models.CharField(max_length=20, blank=True, default="")
    room = models.CharField(max_length=50, blank=True, default="")

    # --- Doctor self-service mobile app (additive) ---------------------
    # Nullable link to a login account. Existing Doctor rows have no user
    # and keep working exactly as before (public directory, admin CRUD,
    # booking, queue, etc. never read this field. Only set when a doctor
    # is given app access; a User can own at most one Doctor record.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="doctor_profile",
        help_text="Login account for this doctor's self-service mobile app, if any.",
    )
    # Self-editable long-form bio for the doctor's own profile screen.
    # Kept separate from `about` (the existing patient-facing description
    # shown on the public doctor detail screen) so neither audience's
    # copy is overwritten by the other.
    bio = models.TextField(blank=True, default="")
    # Doctor-uploaded headshot for the self-service profile. Kept separate
    # from `photo` (existing URL field used by the public directory/cards)
    # since this one is a real file upload, not an external URL.
    profile_photo = models.FileField(
        upload_to="doctor_profiles/%Y/%m/", null=True, blank=True
    )
    # Default consultation length this doctor prefers, used to prefill the
    # self-service schedule/booking UI. Independent of VisitType defaults
    # and of any specific appointment's estimated_duration.
    consultation_duration = models.PositiveIntegerField(
        default=30, help_text="Preferred default consultation length in minutes"
    )

    class Meta:
        ordering = ["-rating", "name"]
        indexes = [
            models.Index(fields=["specialty", "is_active"]),
        ]

    def __str__(self):
        return self.name


class Certification(models.Model):
    """A single certification/credential shown on the doctor's self-service
    profile. Purely additive — read by no other module."""

    doctor = models.ForeignKey(
        Doctor, on_delete=models.CASCADE, related_name="certifications"
    )
    name = models.CharField(max_length=200)
    issuing_body = models.CharField(max_length=200, blank=True, default="")
    year = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-year", "name"]

    def __str__(self):
        return f"{self.name} ({self.issuing_body})" if self.issuing_body else self.name


class Language(models.Model):
    """A language + proficiency level shown on the doctor's self-service
    profile. Purely additive — read by no other module."""

    class Proficiency(models.TextChoices):
        BASIC = "basic"
        CONVERSATIONAL = "conversational"
        FLUENT = "fluent"
        NATIVE = "native"

    doctor = models.ForeignKey(
        Doctor, on_delete=models.CASCADE, related_name="languages"
    )
    name = models.CharField(max_length=80)
    proficiency = models.CharField(
        max_length=16, choices=Proficiency.choices, default=Proficiency.CONVERSATIONAL
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.proficiency})"


class Education(models.Model):
    """A single education entry shown on the doctor's self-service profile.
    Purely additive — read by no other module."""

    doctor = models.ForeignKey(
        Doctor, on_delete=models.CASCADE, related_name="education"
    )
    institution = models.CharField(max_length=200)
    degree = models.CharField(max_length=200, blank=True, default="")
    year = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-year", "institution"]

    def __str__(self):
        return f"{self.degree} — {self.institution}" if self.degree else self.institution


class DoctorSchedule(models.Model):
    """Weekly working hours; free slots are computed from this minus bookings."""

    class Weekday(models.IntegerChoices):
        MONDAY = 0
        TUESDAY = 1
        WEDNESDAY = 2
        THURSDAY = 3
        FRIDAY = 4
        SATURDAY = 5
        SUNDAY = 6

    doctor = models.ForeignKey(
        Doctor, on_delete=models.CASCADE, related_name="schedules"
    )
    weekday = models.IntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    slot_minutes = models.PositiveIntegerField(default=30)

    class Meta:
        unique_together = ("doctor", "weekday", "start_time")
        ordering = ["weekday", "start_time"]

    def __str__(self):
        return f"{self.doctor} {self.get_weekday_display()} {self.start_time}-{self.end_time}"


class DoctorLeave(models.Model):
    """A date-range leave record for a doctor. Originally admin-only
    (Doctor Management module); now also created directly by the doctor
    through the self-service mobile app, then reviewed by staff. Still does
    not feed into scheduling, the queue, or slot availability.

    Deliberately no leave-balance tracking here — that belongs to a future
    dedicated leave-policy system rather than hardcoded per-type totals.
    """

    class LeaveType(models.TextChoices):
        MEDICAL = "medical"
        PERSONAL = "personal"
        STUDY = "study"
        ANNUAL = "annual"

    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    doctor = models.ForeignKey(
        Doctor, on_delete=models.CASCADE, related_name="leaves"
    )
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.CharField(max_length=255, blank=True, default="")
    # --- additive: self-service leave requests ---
    leave_type = models.CharField(
        max_length=10, choices=LeaveType.choices, default=LeaveType.ANNUAL
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    notes = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Reviewer notes (distinct from the doctor's own `reason`).",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leave_reviews",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.doctor} leave {self.start_date}–{self.end_date}"
