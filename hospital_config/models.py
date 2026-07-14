"""
Hospital Configuration foundation (Phase 2), refined (Phase 2.1).

Additive/refinement only: introduces the singleton HospitalProfile record
and a generic, schema-free ConfigurationValue store so future product
configuration — booleans, strings, integers, or structured JSON — can be
added by inserting rows instead of new columns/migrations. Nothing here
changes the behavior of any existing app, model, view, or endpoint outside
this one — no other app reads or writes these models yet, and none of
Phase 1's Patient/Doctor/Admin Panel flows are touched.

ConfigurationValue was originally a boolean-only "FeatureFlag" model
(Phase 2). It has been generalized in place — same table, renamed via
migration 0003 — to carry any typed value while preserving the exact
public contract for existing boolean flags (see
views.FeatureFlagPublicView).
"""

import json

from django.conf import settings
from django.db import models


class HospitalProfile(models.Model):
    """Single configuration record describing the hospital running this
    installation.

    Modeled as a one-row table (a common Django "site settings" idiom)
    rather than a separate "is this the active config" flag: `save()`
    always pins the primary key to 1, and `delete()` is a no-op, so there
    can never be more than one row and it can never be removed by
    accident. `load()` is the one supported way to fetch it — it creates
    the row with field defaults on first access, so the public read
    endpoint always has something to serve even before any admin has ever
    opened Hospital Settings.
    """

    name = models.CharField(max_length=200, default="Sanctuary Health")
    short_name = models.CharField(max_length=50, blank=True, default="")
    # A real file upload (served the same way medical_records.LabReport.file
    # and doctors.Doctor.profile_photo already are), not an external URL —
    # keeps branding assets under this app's own control.
    logo = models.FileField(upload_to="hospital/logo/", null=True, blank=True)
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=30, blank=True, default="")
    address = models.TextField(blank=True, default="")
    website = models.URLField(blank=True, default="")
    # Optional identifiers — not every deployment has these.
    gst_number = models.CharField(max_length=30, blank=True, default="")
    registration_number = models.CharField(max_length=60, blank=True, default="")
    timezone = models.CharField(max_length=64, default="UTC")
    currency = models.CharField(max_length=8, default="USD")
    primary_color = models.CharField(max_length=7, default="#0061A4")
    secondary_color = models.CharField(max_length=7, default="#00497D")
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = "Hospital Profile"
        verbose_name_plural = "Hospital Profile"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Singleton: never actually removed via the ORM.
        return

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ConfigurationValue(models.Model):
    """One named, generically-typed configuration entry.

    This is the "generic configuration system instead of hardcoding
    features" the spec asks for: a brand-new setting — whether it's a
    simple on/off toggle, a string, a number, or a structured JSON blob —
    is added by inserting a new row with a new `key`, never by adding a
    column, a model, or a migration.

    `value` is always stored as text; `value_type` says how to decode it.
    Use `get_value()`/`set_value()` rather than reading/writing `value`
    directly so the boolean/string/integer/json distinction is never
    handled ad hoc in more than one place.
    """

    class ValueType(models.TextChoices):
        BOOLEAN = "boolean", "Boolean"
        STRING = "string", "String"
        INTEGER = "integer", "Integer"
        JSON = "json", "JSON"

    key = models.CharField(max_length=100, unique=True)
    value = models.TextField(blank=True, default="")
    value_type = models.CharField(
        max_length=10, choices=ValueType.choices, default=ValueType.BOOLEAN
    )
    label = models.CharField(max_length=150, blank=True, default="")
    description = models.TextField(blank=True, default="")
    category = models.CharField(max_length=80, blank=True, default="")
    display_order = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["category", "display_order", "key"]
        indexes = [
            models.Index(fields=["key"], name="hospcfg_cfg_key_idx"),
            models.Index(fields=["category"], name="hospcfg_cfg_cat_idx"),
        ]

    def __str__(self):
        return f"{self.key} ({self.value_type}={self.get_value()!r})"

    def get_value(self):
        """Decode `value` (always stored as text) into its Python-native
        type per `value_type`."""
        if self.value_type == self.ValueType.BOOLEAN:
            return str(self.value).strip().lower() in ("1", "true", "yes", "on")
        if self.value_type == self.ValueType.INTEGER:
            return int(self.value) if self.value not in (None, "") else 0
        if self.value_type == self.ValueType.JSON:
            return json.loads(self.value) if self.value else None
        return self.value or ""

    def set_value(self, python_value):
        """Encode a Python-native value into `value` per `value_type`."""
        if self.value_type == self.ValueType.JSON:
            self.value = json.dumps(python_value)
        elif self.value_type == self.ValueType.BOOLEAN:
            self.value = "true" if python_value else "false"
        else:
            self.value = "" if python_value is None else str(python_value)

    @classmethod
    def is_enabled(cls, key: str, default: bool = False) -> bool:
        """Backward-compatible boolean lookup carried over unchanged from
        Phase 2's FeatureFlag.is_enabled(): callers that just want "is this
        flag on" keep working exactly as before regardless of the
        underlying value_type generalization. Not called from anywhere in
        this phase — no existing flow is wired to check configuration
        values yet, per the "foundation/refinement only" scope."""
        row = cls.objects.filter(key=key).only("value", "value_type").first()
        if row is None:
            return default
        return bool(row.get_value())
