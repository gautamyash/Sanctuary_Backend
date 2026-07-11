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

    class Meta:
        ordering = ["-rating", "name"]
        indexes = [
            models.Index(fields=["specialty", "is_active"]),
        ]

    def __str__(self):
        return self.name


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
