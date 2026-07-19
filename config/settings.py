"""
Sanctuary Health API — Django settings.

Environment-driven: reads .env in development, real env vars in production.
SQLite by default for zero-config dev; set DATABASE_URL for Postgres.
"""

from datetime import timedelta
from pathlib import Path

import dj_database_url
import os

from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

_INSECURE_SECRET_KEY = "dev-insecure-key-change-me"
_INSECURE_SECRET_KEYS = {_INSECURE_SECRET_KEY, "change-me-in-production"}
SECRET_KEY = os.getenv("SECRET_KEY", _INSECURE_SECRET_KEY)
DEBUG = os.getenv("DEBUG", "True").lower() in ("1", "true", "yes")

if not DEBUG and (not SECRET_KEY or SECRET_KEY in _INSECURE_SECRET_KEYS):
    raise ImproperlyConfigured(
        "SECRET_KEY must be set to a secure, non-default value via the "
        "environment when DEBUG=False."
    )

ALLOWED_HOSTS = [
    h.strip() for h in os.getenv("ALLOWED_HOSTS", "*").split(",") if h.strip()
]

if not DEBUG and (not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS):
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS must be set to explicit hostnames via the "
        'environment when DEBUG=False ("*" is not permitted).'
    )

# Cookie hardening (Django's own "check --deploy" checklist flags both of
# these). Only takes effect once DEBUG=False, so local/dev over plain HTTP
# is completely unaffected — same conditional pattern as the SECRET_KEY/
# ALLOWED_HOSTS guards above. Not paired with SECURE_SSL_REDIRECT/HSTS here
# since those depend on the deployment's TLS-termination topology (e.g. a
# reverse proxy) and risk redirect loops if enabled blindly.
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third-party
    "rest_framework",
    "corsheaders",
    "django_filters",
    # local
    "accounts",
    "doctors",
    "appointments",
    "queues",
    "attendance",
    "medical_records",
    "billing",
    "authorization",
    "notifications",
    "hospital_config",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database: SQLite for dev, DATABASE_URL (Postgres) in production.
# SQLite is configured directly (dj-database-url mangles Windows paths).
if os.getenv("DATABASE_URL"):
    DATABASES = {
        "default": dj_database_url.parse(
            os.getenv("DATABASE_URL"), conn_max_age=600
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded medical documents (LabReport files) — Feature 5.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    # Scoped throttles for specific public endpoints only (see accounts/views.py)
    # — no DEFAULT_THROTTLE_CLASSES is set globally, so every other endpoint's
    # behavior is completely unaffected.
    "DEFAULT_THROTTLE_RATES": {
        "login": "30/min",
        "password_reset_request": "5/min",
        "password_reset_confirm": "5/min",
        "register": "10/min",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
}

# CORS: open in dev, explicit origins in production.
_cors = [
    o.strip()
    for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]
if _cors:
    CORS_ALLOWED_ORIGINS = _cors
elif DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True
else:
    raise ImproperlyConfigured(
        "CORS_ALLOWED_ORIGINS must be set to at least one explicit origin "
        "via the environment when DEBUG=False."
    )

# Email (password reset). Defaults to the console backend so reset emails
# are visible in the server log with zero setup; set EMAIL_BACKEND (and the
# usual EMAIL_HOST/PORT/HOST_USER/HOST_PASSWORD/USE_TLS) via the environment
# to send real email in production — no code change required.
EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() in ("1", "true", "yes")
DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL", "no-reply@sanctuaryhealth.example"
)

