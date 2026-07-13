from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    """Manager for the email-login User model."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Patient/staff account. Email is the login identifier."""

    class Gender(models.TextChoices):
        FEMALE = "female"
        MALE = "male"
        OTHER = "other"

    username = None
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=150)

    # --- Patient profile (additive, mobile app) ---
    phone = models.CharField(max_length=32, blank=True, default="")
    gender = models.CharField(
        max_length=10, choices=Gender.choices, blank=True, default=""
    )
    date_of_birth = models.DateField(null=True, blank=True)
    profile_photo = models.FileField(
        upload_to="user_profiles/%Y/%m/", null=True, blank=True
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    objects = UserManager()

    def __str__(self):
        return f"{self.name} <{self.email}>"
