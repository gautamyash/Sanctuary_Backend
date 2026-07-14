"""
Seed data for the initial Feature Flag catalog (Phase 2 foundation).

Idempotent: every row is created with get_or_create(), so re-running this
migration (e.g. after a squash, or a fake-replay) never duplicates data.

These are examples named directly in the Phase 2 specification. Seeding
them here just gives an admin something to see/toggle immediately; no
existing code path checks any of these flags yet (Phase 2 is foundation
only — appointment booking, waitlist, payments, and notifications all keep
working exactly as before regardless of these values). Any flag added
after this migration is just a new row via the admin API — it never needs
a schema change or a new migration.
"""

from django.db import migrations

# (key, label, category, enabled)
FEATURE_FLAGS = [
    (
        "appointment_booking_enabled",
        "Appointment Booking",
        "Patient",
        True,
    ),
    ("waitlist_enabled", "Smart Waitlist", "Patient", True),
    ("online_payment_enabled", "Online Payment", "Billing", True),
    ("notifications_enabled", "Notifications", "System", True),
    (
        "maintenance_mode",
        "Maintenance Mode",
        "System",
        False,
    ),
    (
        "patient_registration_enabled",
        "Patient Self-Registration",
        "Patient",
        True,
    ),
]


def seed_feature_flags(apps, schema_editor):
    FeatureFlag = apps.get_model("hospital_config", "FeatureFlag")
    for key, label, category, enabled in FEATURE_FLAGS:
        FeatureFlag.objects.get_or_create(
            key=key,
            defaults={
                "label": label,
                "category": category,
                "enabled": enabled,
            },
        )


def unseed_feature_flags(apps, schema_editor):
    FeatureFlag = apps.get_model("hospital_config", "FeatureFlag")
    FeatureFlag.objects.filter(
        key__in=[key for key, _, _, _ in FEATURE_FLAGS]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("hospital_config", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_feature_flags, unseed_feature_flags),
    ]
