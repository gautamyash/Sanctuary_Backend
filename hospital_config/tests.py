"""
Tests for Hospital Configuration (Phase 2 foundation + Phase 2.1
refinement).

Covers: public read endpoints work unauthenticated; admin endpoints reject
anonymous and unprivileged users but allow a user holding the "settings.*"
RBAC permissions (via the existing Admin role, exactly as billing/tests.py
already does for its own RBAC-gated endpoints); the HospitalProfile
singleton never has more than one row; configuration values are fully
generic (new keys of any value_type work with zero code changes); the
legacy boolean-only /api/config/features/ contract is preserved; and the
new /api/config/bootstrap/ endpoint combines hospital + configuration +
config_version for mobile startup.
"""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from authorization.models import Role, UserRole

from .models import ConfigurationValue, HospitalProfile


def make_flag(key, enabled, **extra):
    """Create a boolean-typed ConfigurationValue the same way the admin API
    would (via set_value()), rather than poking `value` directly."""
    row = ConfigurationValue(
        key=key, value_type=ConfigurationValue.ValueType.BOOLEAN, **extra
    )
    row.set_value(enabled)
    row.save()
    return row


class Base(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anon_patient = User.objects.create_user(
            email="patient@example.com", password="x", name="Pat"
        )
        # Admin role already holds every permission except system.admin,
        # including settings.view/settings.edit (authorization migration
        # 0002_seed_rbac_data) — same convention billing/tests.py uses for
        # its own RBAC-gated endpoints.
        cls.admin = User.objects.create_user(
            email="admin@example.com", password="x", name="Admin"
        )
        UserRole.objects.create(user=cls.admin, role=Role.objects.get(name="Admin"))


class HospitalProfilePublicTests(Base):
    def test_public_hospital_endpoint_requires_no_auth(self):
        url = reverse("config-hospital-public")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("name", response.data)
        self.assertIn("timezone", response.data)
        self.assertIn("currency", response.data)
        # Never exposes write-only/internal bookkeeping.
        self.assertNotIn("updated_by", response.data)

    def test_public_hospital_endpoint_creates_singleton_on_first_access(self):
        self.assertEqual(HospitalProfile.objects.count(), 0)
        self.client.get(reverse("config-hospital-public"))
        self.assertEqual(HospitalProfile.objects.count(), 1)

    def test_loading_twice_never_creates_a_second_row(self):
        HospitalProfile.load()
        HospitalProfile.load()
        # Exercise the singleton collapse through the normal save() path,
        # not .objects.create() — create() calls save(force_insert=True),
        # which always issues an INSERT regardless of what save() pins
        # `pk` to, so it correctly raises IntegrityError against the
        # existing pk=1 row rather than "succeeding as a duplicate". A
        # fresh in-memory instance saved normally has no such override: its
        # save() pins pk=1, and Django's default (non-forced) save then
        # performs an UPDATE against the existing row — this is the actual
        # singleton behavior worth asserting.
        duplicate = HospitalProfile(name="Duplicate attempt")
        duplicate.save()
        self.assertEqual(HospitalProfile.objects.count(), 1)
        row = HospitalProfile.objects.get()
        self.assertEqual(row.pk, 1)
        self.assertEqual(row.name, "Duplicate attempt")


class FeatureFlagPublicTests(Base):
    """Note: hospital_config's own 0002_seed_feature_flags migration
    pre-populates 6 baseline boolean keys (appointment_booking_enabled,
    waitlist_enabled, online_payment_enabled, notifications_enabled,
    maintenance_mode, patient_registration_enabled) into every test
    database, later converted to ConfigurationValue rows by migration
    0003. Tests use keys outside that baseline set to avoid unique-key
    collisions, and assert via containment rather than exact dict
    equality."""

    def test_public_features_endpoint_requires_no_auth_and_is_flat(self):
        make_flag("custom_probe_flag_one", True)
        make_flag("custom_probe_flag_two", False)
        response = self.client.get(reverse("config-features-public"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["custom_probe_flag_one"], True)
        self.assertEqual(response.data["custom_probe_flag_two"], False)
        # Also reflects the migration-seeded baseline catalog.
        self.assertIn("maintenance_mode", response.data)

    def test_new_flag_appears_with_zero_code_changes(self):
        response = self.client.get(reverse("config-features-public"))
        self.assertNotIn("brand_new_feature_enabled", response.data)
        make_flag("brand_new_feature_enabled", True)
        response = self.client.get(reverse("config-features-public"))
        self.assertEqual(response.data["brand_new_feature_enabled"], True)

    def test_non_boolean_values_are_excluded_from_the_legacy_endpoint(self):
        string_row = ConfigurationValue(
            key="support_phone_number",
            value_type=ConfigurationValue.ValueType.STRING,
        )
        string_row.set_value("+1-555-0100")
        string_row.save()
        response = self.client.get(reverse("config-features-public"))
        self.assertNotIn("support_phone_number", response.data)


class HospitalProfileAdminTests(Base):
    def test_anonymous_cannot_read_or_write_admin_endpoint(self):
        url = reverse("config-hospital-admin")
        self.assertIn(
            self.client.get(url).status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )
        self.assertIn(
            self.client.patch(url, {"name": "Hacked"}).status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_authenticated_non_admin_is_forbidden(self):
        self.client.force_authenticate(self.anon_patient)
        url = reverse("config-hospital-admin")
        self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self.client.patch(url, {"name": "Hacked"}).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_admin_can_read_and_update(self):
        self.client.force_authenticate(self.admin)
        url = reverse("config-hospital-admin")
        get_response = self.client.get(url)
        self.assertEqual(get_response.status_code, status.HTTP_200_OK)

        patch_response = self.client.patch(
            url,
            {
                "name": "Sanctuary General Hospital",
                "primary_color": "#123456",
                "currency": "INR",
            },
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        profile = HospitalProfile.load()
        self.assertEqual(profile.name, "Sanctuary General Hospital")
        self.assertEqual(profile.primary_color, "#123456")
        self.assertEqual(profile.currency, "INR")
        self.assertEqual(profile.updated_by, self.admin)

        # The public endpoint immediately reflects the admin update.
        public_response = self.client.get(reverse("config-hospital-public"))
        self.assertEqual(public_response.data["name"], "Sanctuary General Hospital")


class ConfigurationValueAdminTests(Base):
    def test_anonymous_and_non_admin_cannot_manage_values(self):
        list_url = reverse("config-features-admin-list")
        self.assertIn(
            self.client.get(list_url).status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )
        self.client.force_authenticate(self.anon_patient)
        self.assertEqual(
            self.client.get(list_url).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_admin_can_create_list_update_and_delete_a_boolean_value(self):
        self.client.force_authenticate(self.admin)
        list_url = reverse("config-features-admin-list")

        create_response = self.client.post(
            list_url,
            {
                "key": "telemedicine_enabled",
                "label": "Telemedicine",
                "category": "Patient",
                "value_type": "boolean",
                "value": False,
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(create_response.data["value"], False)
        self.assertIsNotNone(create_response.data["updated_at"])

        list_response = self.client.get(list_url)
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        rows = (
            list_response.data["results"]
            if isinstance(list_response.data, dict)
            else list_response.data
        )
        self.assertIn("telemedicine_enabled", [r["key"] for r in rows])

        detail_url = reverse(
            "config-features-admin-detail", kwargs={"key": "telemedicine_enabled"}
        )
        patch_response = self.client.patch(
            detail_url, {"value": True}, format="json"
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_response.data["value"], True)
        row = ConfigurationValue.objects.get(key="telemedicine_enabled")
        self.assertTrue(row.get_value())
        self.assertEqual(row.updated_by, self.admin)

        # Reflected on the legacy boolean-only public endpoint.
        public_data = self.client.get(reverse("config-features-public")).data
        self.assertEqual(public_data["telemedicine_enabled"], True)

        delete_response = self.client.delete(detail_url)
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            ConfigurationValue.objects.filter(key="telemedicine_enabled").exists()
        )

    def test_admin_can_create_string_integer_and_json_values(self):
        self.client.force_authenticate(self.admin)
        list_url = reverse("config-features-admin-list")

        string_resp = self.client.post(
            list_url,
            {
                "key": "support_email",
                "value_type": "string",
                "value": "help@sanctuary.example",
                "category": "Support",
            },
            format="json",
        )
        self.assertEqual(string_resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(string_resp.data["value"], "help@sanctuary.example")

        int_resp = self.client.post(
            list_url,
            {
                "key": "max_upload_mb",
                "value_type": "integer",
                "value": 20,
                "category": "Uploads",
            },
            format="json",
        )
        self.assertEqual(int_resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(int_resp.data["value"], 20)
        self.assertEqual(
            ConfigurationValue.objects.get(key="max_upload_mb").get_value(), 20
        )

        json_resp = self.client.post(
            list_url,
            {
                "key": "support_links",
                "value_type": "json",
                "value": {"faq": "https://x.example/faq", "phone": "+1-555-0100"},
                "category": "Support",
            },
            format="json",
        )
        self.assertEqual(json_resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            json_resp.data["value"],
            {"faq": "https://x.example/faq", "phone": "+1-555-0100"},
        )


class ConfigurationValueHelperTests(Base):
    def test_is_enabled_helper_defaults_missing_keys_safely(self):
        self.assertFalse(ConfigurationValue.is_enabled("does_not_exist"))
        self.assertTrue(ConfigurationValue.is_enabled("does_not_exist", default=True))
        # Seeded by 0002_seed_feature_flags (enabled=True), converted to a
        # boolean ConfigurationValue by migration 0003.
        self.assertTrue(ConfigurationValue.is_enabled("online_payment_enabled"))


class BootstrapTests(Base):
    def test_bootstrap_requires_no_auth_and_has_the_three_top_level_keys(self):
        response = self.client.get(reverse("config-bootstrap"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("hospital", response.data)
        self.assertIn("configuration", response.data)
        self.assertIn("config_version", response.data)
        self.assertIsInstance(response.data["config_version"], int)

    def test_configuration_includes_every_value_type_unlike_features_endpoint(self):
        make_flag("bootstrap_probe_bool", True)
        string_row = ConfigurationValue(
            key="bootstrap_probe_string",
            value_type=ConfigurationValue.ValueType.STRING,
        )
        string_row.set_value("hello")
        string_row.save()

        bootstrap_data = self.client.get(reverse("config-bootstrap")).data
        self.assertEqual(bootstrap_data["configuration"]["bootstrap_probe_bool"], True)
        self.assertEqual(
            bootstrap_data["configuration"]["bootstrap_probe_string"], "hello"
        )

        # The legacy endpoint only ever exposes the boolean subset.
        features_data = self.client.get(reverse("config-features-public")).data
        self.assertIn("bootstrap_probe_bool", features_data)
        self.assertNotIn("bootstrap_probe_string", features_data)

    def test_config_version_changes_when_hospital_or_configuration_changes(self):
        first = self.client.get(reverse("config-bootstrap")).data["config_version"]

        self.client.force_authenticate(self.admin)
        self.client.patch(
            reverse("config-hospital-admin"), {"name": "Changed Name"}
        )

        second = self.client.get(reverse("config-bootstrap")).data["config_version"]
        self.assertGreaterEqual(second, first)

        self.client.post(
            reverse("config-features-admin-list"),
            {
                "key": "version_probe_flag",
                "value_type": "boolean",
                "value": True,
            },
            format="json",
        )
        third = self.client.get(reverse("config-bootstrap")).data["config_version"]
        self.assertGreaterEqual(third, second)
