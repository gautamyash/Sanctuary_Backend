"""
Regression tests for Feature 7 — Role-Based Access Control (RBAC).

Read-only against production code: these tests exercise the existing
Role/Permission/UserRole models, PermissionService, PermissionRequired, the
authorization API, and the RBAC wiring added to Features 1-6 (appointments,
EMR, billing, queue, attendance, analytics). Nothing here modifies any
production model, service, view, serializer, or URL.

Covers:
  - Every seeded role and permission (from the 0002_seed_rbac_data migration).
  - PermissionService.has_permission / has_any_permission /
    has_all_permissions / require_permission.
  - PermissionRequired: authenticated allowed/denied, anonymous denied,
    missing permission_code fails closed.
  - Multiple-role inheritance and permission de-duplication.
  - RBAC API endpoints (roles, permissions, me/permissions, assign-role) and
    the system.admin gate on RBAC administration.
  - EMR clinical_notes serializer field visibility.
  - Ownership behavior preserved across appointments, billing, EMR, queue,
    and attendance now that those endpoints are RBAC-wrapped.
"""

from datetime import time, timedelta

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate
from rest_framework.views import APIView

from accounts.models import User
from appointments.models import Appointment, VisitType
from authorization.models import Permission, Role, UserRole
from authorization.permissions import PermissionRequired
from authorization.services import PermissionService
from billing.models import Invoice
from doctors.models import Doctor, DoctorSchedule, Specialty
from medical_records.models import MedicalVisit

# --------------------------------------------------------------------------- #
# Expected seed state, hardcoded independently of the migration source so
# these tests catch accidental drift in 0002_seed_rbac_data.py rather than
# just re-deriving and re-asserting whatever it currently contains.
# --------------------------------------------------------------------------- #

EXPECTED_ROLE_NAMES = {
    "Owner", "Admin", "Doctor", "Receptionist", "Nurse",
    "Lab Technician", "Pharmacist", "Accountant", "Patient",
}

EXPECTED_PERMISSION_CODES = {
    "appointment.view", "appointment.create", "appointment.edit",
    "appointment.cancel", "appointment.reschedule",
    "emr.view", "emr.edit", "emr.prescription", "emr.upload", "emr.delete",
    "billing.view", "billing.create", "billing.edit", "billing.payment",
    "billing.refund", "billing.analytics",
    "queue.view", "queue.manage",
    "attendance.view", "attendance.manage",
    "analytics.view", "analytics.export",
    "user.view", "user.create", "user.edit", "user.delete", "user.invite",
    "settings.view", "settings.edit",
    "reports.export", "reports.print",
    "system.admin",
}

EXPECTED_ROLE_PERMISSION_COUNTS = {
    "Owner": 32,
    "Admin": 31,
    "Doctor": 15,
    "Receptionist": 13,
    "Nurse": 5,
    "Lab Technician": 4,
    "Pharmacist": 1,
    "Accountant": 6,
    "Patient": 0,
}


class Base(APITestCase):
    """Shared fixtures: a doctor, a full week of schedule, and a handful of
    plain (role-less) users. Individual test classes assign roles as needed."""

    @classmethod
    def setUpTestData(cls):
        cls.specialty = Specialty.objects.create(name="Cardiology")
        cls.doctor = Doctor.objects.create(
            name="Dr. Mitchell", specialty=cls.specialty, hospital="St. Mary's",
        )
        for wd in range(7):
            DoctorSchedule.objects.create(
                doctor=cls.doctor, weekday=wd,
                start_time=time(8, 0), end_time=time(20, 0), slot_minutes=30,
            )
        cls.visit_type = VisitType.objects.create(name="Follow-up", default_duration=20)

        cls.patient = User.objects.create_user(
            email="pat@example.com", password="x", name="Pat"
        )
        cls.other = User.objects.create_user(
            email="other@example.com", password="x", name="Other"
        )
        cls.third = User.objects.create_user(
            email="third@example.com", password="x", name="Third"
        )
        cls.superuser = User.objects.create_superuser(
            email="root@example.com", password="x", name="Root"
        )

    def _assign_role(self, user, role_name):
        role = Role.objects.get(name=role_name)
        UserRole.objects.get_or_create(user=user, role=role)
        return role

    def _appt(self, patient=None, day=None, t=time(10, 0),
              status=Appointment.Status.CONFIRMED):
        return Appointment.objects.create(
            doctor=self.doctor, patient=patient or self.patient,
            date=day or timezone.localdate(), time=t,
            estimated_duration=30, status=status,
        )

    def _complete(self, patient=None):
        """Complete an appointment; returns (appointment, invoice, visit) —
        both auto-created by the existing post-completion signals."""
        appt = self._appt(patient=patient)
        appt.status = Appointment.Status.COMPLETED
        appt.save(update_fields=["status", "updated_at"])
        invoice = Invoice.objects.get(appointment=appt)
        visit = MedicalVisit.objects.get(appointment=appt)
        return appt, invoice, visit


# --------------------------------------------------------------------------- #
class SeededRolesAndPermissionsTests(TestCase):
    """Every seeded role and every seeded permission, per the Feature 7 spec."""

    def test_all_expected_roles_exist(self):
        names = set(Role.objects.values_list("name", flat=True))
        self.assertTrue(EXPECTED_ROLE_NAMES.issubset(names))

    def test_role_count_matches_spec(self):
        self.assertEqual(
            Role.objects.filter(name__in=EXPECTED_ROLE_NAMES).count(),
            len(EXPECTED_ROLE_NAMES),
        )

    def test_all_expected_permissions_exist(self):
        codes = set(Permission.objects.values_list("code", flat=True))
        self.assertTrue(EXPECTED_PERMISSION_CODES.issubset(codes))

    def test_permission_count_matches_spec(self):
        self.assertEqual(
            Permission.objects.filter(code__in=EXPECTED_PERMISSION_CODES).count(),
            len(EXPECTED_PERMISSION_CODES),
        )

    def test_each_role_permission_count_matches_matrix(self):
        for role_name, expected_count in EXPECTED_ROLE_PERMISSION_COUNTS.items():
            role = Role.objects.get(name=role_name)
            actual = role.role_permissions.count()
            self.assertEqual(
                actual, expected_count,
                f"{role_name}: expected {expected_count} permissions, got {actual}",
            )

    def test_patient_role_has_zero_permissions(self):
        patient_role = Role.objects.get(name="Patient")
        self.assertEqual(patient_role.role_permissions.count(), 0)

    def test_owner_role_has_every_permission_including_system_admin(self):
        owner = Role.objects.get(name="Owner")
        codes = set(
            owner.role_permissions.values_list("permission__code", flat=True)
        )
        self.assertEqual(codes, EXPECTED_PERMISSION_CODES)

    def test_admin_role_has_every_permission_except_system_admin(self):
        admin = Role.objects.get(name="Admin")
        codes = set(
            admin.role_permissions.values_list("permission__code", flat=True)
        )
        self.assertEqual(codes, EXPECTED_PERMISSION_CODES - {"system.admin"})


# --------------------------------------------------------------------------- #
class PermissionServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.patient = User.objects.create_user(
            email="svc-pat@example.com", password="x", name="Pat"
        )
        cls.doctor_user = User.objects.create_user(
            email="svc-doc@example.com", password="x", name="DocUser"
        )
        UserRole.objects.create(
            user=cls.doctor_user, role=Role.objects.get(name="Doctor")
        )
        cls.superuser = User.objects.create_superuser(
            email="svc-root@example.com", password="x", name="Root"
        )

    def test_superuser_passes_every_check(self):
        self.assertTrue(
            PermissionService.has_permission(self.superuser, "system.admin")
        )
        self.assertTrue(
            PermissionService.has_any_permission(self.superuser, ["billing.refund"])
        )
        self.assertTrue(
            PermissionService.has_all_permissions(
                self.superuser, ["system.admin", "user.delete"]
            )
        )

    def test_anonymous_and_none_always_fail(self):
        self.assertFalse(PermissionService.has_permission(None, "appointment.view"))
        self.assertFalse(
            PermissionService.has_any_permission(None, ["appointment.view"])
        )
        self.assertFalse(
            PermissionService.has_all_permissions(None, ["appointment.view"])
        )

        class Anon:
            is_authenticated = False

        self.assertFalse(PermissionService.has_permission(Anon(), "appointment.view"))

    def test_user_with_no_role_has_no_permissions(self):
        self.assertFalse(
            PermissionService.has_permission(self.patient, "appointment.view")
        )

    def test_role_based_permission_granted(self):
        self.assertTrue(
            PermissionService.has_permission(self.doctor_user, "appointment.view")
        )
        self.assertTrue(
            PermissionService.has_permission(self.doctor_user, "emr.edit")
        )

    def test_role_based_permission_not_granted_outside_role(self):
        # Doctor role does not include billing.refund.
        self.assertFalse(
            PermissionService.has_permission(self.doctor_user, "billing.refund")
        )

    def test_has_any_permission(self):
        self.assertTrue(
            PermissionService.has_any_permission(
                self.doctor_user, ["billing.refund", "emr.view"]
            )
        )
        self.assertFalse(
            PermissionService.has_any_permission(
                self.doctor_user, ["billing.refund", "user.delete"]
            )
        )
        self.assertFalse(
            PermissionService.has_any_permission(self.doctor_user, [])
        )

    def test_has_all_permissions(self):
        self.assertTrue(
            PermissionService.has_all_permissions(
                self.doctor_user, ["appointment.view", "emr.view"]
            )
        )
        self.assertFalse(
            PermissionService.has_all_permissions(
                self.doctor_user, ["appointment.view", "billing.refund"]
            )
        )
        # Empty requirement set is vacuously satisfied.
        self.assertTrue(PermissionService.has_all_permissions(self.doctor_user, []))

    def test_require_permission_raises_when_missing(self):
        with self.assertRaises(DjangoPermissionDenied):
            PermissionService.require_permission(self.patient, "appointment.view")

    def test_require_permission_does_not_raise_when_present(self):
        try:
            PermissionService.require_permission(self.doctor_user, "appointment.view")
        except DjangoPermissionDenied:
            self.fail("require_permission raised despite the user holding the code")

    def test_permissions_for_superuser_returns_everything(self):
        codes = set(
            PermissionService.permissions_for(self.superuser).values_list(
                "code", flat=True
            )
        )
        self.assertEqual(codes, EXPECTED_PERMISSION_CODES)

    def test_permissions_for_anonymous_returns_none(self):
        self.assertEqual(PermissionService.permissions_for(None).count(), 0)


# --------------------------------------------------------------------------- #
class PermissionRequiredUnitTests(TestCase):
    """Direct unit tests of the PermissionRequired DRF permission class,
    including the "missing permission_code fails closed" case, which has no
    real production view to exercise it against (every production view
    correctly declares permission_code)."""

    @classmethod
    def setUpTestData(cls):
        cls.doctor_user = User.objects.create_user(
            email="pr-doc@example.com", password="x", name="DocUser"
        )
        UserRole.objects.create(
            user=cls.doctor_user, role=Role.objects.get(name="Doctor")
        )
        cls.patient = User.objects.create_user(
            email="pr-pat@example.com", password="x", name="Pat"
        )

    def _drf_request(self, user=None):
        """Build a DRF Request via the public test API. Passing a user
        authenticates it via the documented `force_authenticate()` helper
        (not a private Request attribute); passing none leaves the request
        genuinely anonymous, so `.user` resolves the same way it would for
        a real unauthenticated request (AnonymousUser)."""
        factory = APIRequestFactory()
        django_request = factory.get("/whatever/")
        if user is not None:
            force_authenticate(django_request, user=user)
        return Request(django_request)

    def test_authenticated_user_with_permission_is_allowed(self):
        class DummyView(APIView):
            permission_code = "appointment.view"

        allowed = PermissionRequired().has_permission(
            self._drf_request(self.doctor_user), DummyView()
        )
        self.assertTrue(allowed)

    def test_authenticated_user_without_permission_is_denied(self):
        class DummyView(APIView):
            permission_code = "billing.refund"

        allowed = PermissionRequired().has_permission(
            self._drf_request(self.doctor_user), DummyView()
        )
        self.assertFalse(allowed)

        allowed_patient = PermissionRequired().has_permission(
            self._drf_request(self.patient), DummyView()
        )
        self.assertFalse(allowed_patient)

    def test_anonymous_user_is_denied(self):
        class DummyView(APIView):
            permission_code = "appointment.view"

        allowed = PermissionRequired().has_permission(
            self._drf_request(), DummyView()
        )
        self.assertFalse(allowed)

    def test_missing_permission_code_fails_closed(self):
        class DummyViewNoCode(APIView):
            """Deliberately omits permission_code — test-only, mirrors a
            developer mistake, not a real production view."""

        # Even a superuser must not be let in when the view forgot to
        # declare permission_code: the check must fail before ever
        # reaching PermissionService.
        superuser = User.objects.create_superuser(
            email="pr-root@example.com", password="x", name="Root"
        )
        allowed = PermissionRequired().has_permission(
            self._drf_request(superuser), DummyViewNoCode()
        )
        self.assertFalse(allowed)


# --------------------------------------------------------------------------- #
class MultipleRoleInheritanceAndDedupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="multi@example.com", password="x", name="Multi"
        )

    def test_union_of_two_disjoint_roles(self):
        UserRole.objects.create(user=self.user, role=Role.objects.get(name="Nurse"))
        UserRole.objects.create(
            user=self.user, role=Role.objects.get(name="Pharmacist")
        )
        codes = set(
            PermissionService.permissions_for(self.user).values_list(
                "code", flat=True
            )
        )
        # Nurse (5) + Pharmacist (1), fully disjoint.
        self.assertEqual(len(codes), 6)
        self.assertIn("queue.view", codes)
        self.assertIn("emr.prescription", codes)

    def test_overlapping_roles_deduplicate(self):
        # Nurse's 5 codes (queue.view, queue.manage, attendance.view,
        # attendance.manage, emr.view) are all a subset of Doctor's 15, so
        # the DISTINCT union must equal Doctor's set (15), not the naive
        # sum (20).
        #
        # permissions_for() joins Permission -> RolePermission -> Role ->
        # UserRole for the user without an explicit .distinct(): when two
        # of the user's roles grant the same permission (as Doctor and
        # Nurse do here), that Permission row comes back once per matching
        # join path, so the raw queryset legitimately contains duplicates.
        # This isn't a bug to fix here — permissions_for()'s only real
        # caller, MyPermissionsView, already dedupes at the call site via
        # sorted(set(codes)) — so the test (like that caller, and like the
        # sibling test above) must dedupe via set() before asserting
        # uniqueness, rather than asserting the raw list has no duplicates.
        UserRole.objects.create(user=self.user, role=Role.objects.get(name="Doctor"))
        UserRole.objects.create(user=self.user, role=Role.objects.get(name="Nurse"))
        codes = set(
            PermissionService.permissions_for(self.user).values_list(
                "code", flat=True
            )
        )
        self.assertEqual(len(codes), 15)


# --------------------------------------------------------------------------- #
class RbacApiTests(Base):
    def test_roles_list_available_to_any_authenticated_user(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("role-list-create"))
        self.assertEqual(resp.status_code, 200)
        # RoleListCreateView does not override pagination_class, so it uses
        # the project default (PageNumberPagination) — the response is
        # always the paginated {"results": [...]} shape.
        names = {r["name"] for r in resp.data["results"]}
        self.assertTrue(EXPECTED_ROLE_NAMES.issubset(names))

    def test_permissions_list_available_to_any_authenticated_user(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("permission-list"))
        self.assertEqual(resp.status_code, 200)
        codes = {p["code"] for p in resp.data}
        self.assertTrue(EXPECTED_PERMISSION_CODES.issubset(codes))

    def test_my_permissions_requires_authentication(self):
        resp = self.client.get(reverse("my-permissions"))
        self.assertEqual(resp.status_code, 401)

    def test_my_permissions_empty_for_roleless_user(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("my-permissions"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["roles"], [])
        self.assertEqual(resp.data["permissions"], [])

    def test_my_permissions_deduplicated_and_sorted(self):
        self._assign_role(self.patient, "Doctor")
        self._assign_role(self.patient, "Nurse")
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("my-permissions"))
        self.assertEqual(resp.status_code, 200)
        perms = resp.data["permissions"]
        self.assertEqual(len(perms), 15)
        self.assertEqual(perms, sorted(perms))
        self.assertEqual(set(resp.data["roles"]), {"Doctor", "Nurse"})

    def test_assign_role_requires_system_admin(self):
        self.client.force_authenticate(self.patient)
        role = Role.objects.get(name="Nurse")
        resp = self.client.post(
            reverse("assign-role", args=[self.other.id]),
            {"role": role.id}, format="json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(
            UserRole.objects.filter(user=self.other, role=role).exists()
        )

    def test_assign_role_succeeds_for_system_admin_holder(self):
        self._assign_role(self.patient, "Owner")  # Owner includes system.admin
        self.client.force_authenticate(self.patient)
        role = Role.objects.get(name="Nurse")
        resp = self.client.post(
            reverse("assign-role", args=[self.other.id]),
            {"role": role.id}, format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(
            UserRole.objects.filter(user=self.other, role=role).exists()
        )

    def test_assign_role_succeeds_for_superuser(self):
        self.client.force_authenticate(self.superuser)
        role = Role.objects.get(name="Nurse")
        resp = self.client.post(
            reverse("assign-role", args=[self.other.id]),
            {"role": role.id}, format="json",
        )
        self.assertEqual(resp.status_code, 201)

    def test_role_create_requires_system_admin(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("role-list-create"),
            {"name": "Temp Role", "description": "x"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_role_create_succeeds_for_system_admin_holder(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("role-list-create"),
            {"name": "Temp Role", "description": "x"}, format="json",
        )
        self.assertEqual(resp.status_code, 201)

    def test_role_update_requires_system_admin(self):
        role = Role.objects.get(name="Nurse")
        self.client.force_authenticate(self.patient)
        resp = self.client.patch(
            reverse("role-update", args=[role.id]),
            {"description": "hacked"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)
        role.refresh_from_db()
        self.assertNotEqual(role.description, "hacked")

    def test_role_update_succeeds_for_system_admin_holder(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        role = Role.objects.get(name="Nurse")
        resp = self.client.patch(
            reverse("role-update", args=[role.id]),
            {"description": "updated by admin"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        role.refresh_from_db()
        self.assertEqual(role.description, "updated by admin")


# --------------------------------------------------------------------------- #
class AdminCreateUserTests(Base):
    """POST /api/auth/users/ — admin-managed user creation (Phase: Admin-
    managed User Creation). Exercises the new endpoint added to the existing
    UserListView; GET behavior on the same view is covered separately by
    RbacApiTests and is untouched by this feature."""

    def _payload(self, **overrides):
        role = Role.objects.get(name="Nurse")
        payload = {
            "name": "New Hire",
            "email": "new.hire@example.com",
            "phone": "+1 555 0100",
            "gender": "female",
            "password": "S3curePassw0rd!",
            "role": role.id,
            "is_active": True,
        }
        payload.update(overrides)
        return payload

    def test_requires_authentication(self):
        resp = self.client.post(
            reverse("user-list"), self._payload(), format="json"
        )
        self.assertEqual(resp.status_code, 401)

    def test_requires_user_create_permission(self):
        # Patient has no role at all, so no permissions.
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("user-list"), self._payload(), format="json"
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(User.objects.filter(email="new.hire@example.com").exists())

    def test_user_view_alone_is_not_sufficient(self):
        # Nurse holds no "Users" category codes at all (user.view included),
        # so this also covers "some unrelated permission isn't enough".
        self._assign_role(self.patient, "Nurse")
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("user-list"), self._payload(), format="json"
        )
        self.assertEqual(resp.status_code, 403)

    def test_succeeds_for_user_create_holder_and_assigns_role(self):
        self._assign_role(self.patient, "Owner")  # Owner includes user.create
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("user-list"), self._payload(), format="json"
        )
        self.assertEqual(resp.status_code, 201)

        created = User.objects.get(email="new.hire@example.com")
        self.assertEqual(created.name, "New Hire")
        self.assertEqual(created.phone, "+1 555 0100")
        self.assertEqual(created.gender, "female")
        self.assertTrue(created.is_active)

        # Password hashed via the existing auth flow, not stored/returned raw.
        self.assertTrue(created.check_password("S3curePassw0rd!"))
        self.assertNotIn("password", resp.data["user"])

        # Role assignment reuses UserRole, not a parallel mechanism.
        self.assertTrue(
            UserRole.objects.filter(user=created, role__name="Nurse").exists()
        )
        self.assertEqual(resp.data["role"]["name"], "Nurse")

    def test_succeeds_for_superuser(self):
        self.client.force_authenticate(self.superuser)
        resp = self.client.post(
            reverse("user-list"), self._payload(), format="json"
        )
        self.assertEqual(resp.status_code, 201)

    def test_is_active_defaults_true_when_omitted(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        payload = self._payload()
        del payload["is_active"]
        resp = self.client.post(reverse("user-list"), payload, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(User.objects.get(email="new.hire@example.com").is_active)

    def test_is_active_false_creates_inactive_account(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("user-list"), self._payload(is_active=False), format="json"
        )
        self.assertEqual(resp.status_code, 201)
        self.assertFalse(User.objects.get(email="new.hire@example.com").is_active)

    def test_duplicate_email_rejected(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("user-list"),
            self._payload(email=self.other.email),
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("email", resp.data)

    def test_invalid_role_rejected(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("user-list"), self._payload(role=999999), format="json"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("role", resp.data)
        self.assertFalse(User.objects.filter(email="new.hire@example.com").exists())

    def test_weak_password_rejected(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("user-list"), self._payload(password="12345"), format="json"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("password", resp.data)
        self.assertFalse(User.objects.filter(email="new.hire@example.com").exists())

    def test_missing_required_fields_rejected(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        resp = self.client.post(reverse("user-list"), {}, format="json")
        self.assertEqual(resp.status_code, 400)
        for field in ("name", "email", "password", "role"):
            self.assertIn(field, resp.data)

    def test_get_still_works_unchanged_after_post_added(self):
        # Guards against the get_permissions()/post() addition regressing the
        # pre-existing GET behavior on this same view.
        self.client.force_authenticate(self.patient)  # no role, no permission
        self.assertEqual(self.client.get(reverse("user-list")).status_code, 403)

        self._assign_role(self.patient, "Owner")
        resp = self.client.get(reverse("user-list"))
        self.assertEqual(resp.status_code, 200)
        emails = {u["email"] for u in resp.data["results"]}
        self.assertIn(self.patient.email, emails)

    def test_created_patient_can_authenticate_with_temp_password(self):
        """Mirrors the mobile login contract: JWT token endpoint accepts the
        temporary password immediately after admin creation, exactly like a
        self-registered account."""
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        patient_role = Role.objects.get(name="Patient")
        resp = self.client.post(
            reverse("user-list"),
            self._payload(
                email="mobile.patient@example.com",
                role=patient_role.id,
            ),
            format="json",
        )
        self.assertEqual(resp.status_code, 201)

        self.client.force_authenticate(None)  # simulate a fresh, logged-out client
        token_resp = self.client.post(
            reverse("token_obtain_pair"),
            {"email": "mobile.patient@example.com", "password": "S3curePassw0rd!"},
            format="json",
        )
        self.assertEqual(token_resp.status_code, 200)
        self.assertIn("access", token_resp.data)

    def test_created_staff_user_gets_role_permissions_immediately(self):
        """Mirrors the Admin Panel login contract: the assigned role's
        permissions are effective immediately, no separate activation step."""
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        doctor_role = Role.objects.get(name="Doctor")
        resp = self.client.post(
            reverse("user-list"),
            self._payload(email="new.doctor@example.com", role=doctor_role.id),
            format="json",
        )
        self.assertEqual(resp.status_code, 201)

        new_staff = User.objects.get(email="new.doctor@example.com")
        self.assertTrue(
            PermissionService.has_permission(new_staff, "appointment.view")
        )
        self.assertTrue(PermissionService.has_permission(new_staff, "emr.edit"))
        self.assertFalse(
            PermissionService.has_permission(new_staff, "billing.refund")
        )


# --------------------------------------------------------------------------- #
class UserListRoleFilterTests(Base):
    """GET /api/auth/users/?role=<name> — additive RBAC-role filter (bug fix:
    the Admin Panel's Patients module used to identify patients via
    `!user.isStaff`, but `is_staff` is Django's own admin-site flag and is
    False for every role — staff and patients alike — so it never actually
    scoped anything. This filter joins through the existing UserRole/Role
    relation instead, with no new field or model."""

    def setUp(self):
        self._assign_role(self.patient, "Owner")  # includes user.view
        self.client.force_authenticate(self.patient)

    def _get(self, **params):
        return self.client.get(reverse("user-list"), params)

    def test_role_param_absent_preserves_prior_behavior(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        emails = {u["email"] for u in resp.data["results"]}
        self.assertIn(self.patient.email, emails)
        self.assertIn(self.other.email, emails)
        self.assertIn(self.third.email, emails)

    def test_role_patient_returns_only_patient_role_holders(self):
        self._assign_role(self.other, "Patient")
        self._assign_role(self.third, "Doctor")
        resp = self._get(role="Patient")
        self.assertEqual(resp.status_code, 200)
        emails = {u["email"] for u in resp.data["results"]}
        self.assertIn(self.other.email, emails)
        self.assertNotIn(self.third.email, emails)

    def test_role_filter_excludes_every_staff_role(self):
        staff_roles = ["Doctor", "Receptionist", "Nurse", "Pharmacist", "Lab Technician"]
        staff_users = []
        for role_name in staff_roles:
            u = User.objects.create_user(
                email=f"{role_name.lower().replace(' ', '')}@example.com",
                password="x", name=role_name,
            )
            self._assign_role(u, role_name)
            staff_users.append(u)

        resp = self._get(role="Patient")
        self.assertEqual(resp.status_code, 200)
        emails = {u["email"] for u in resp.data["results"]}
        for u in staff_users:
            self.assertNotIn(
                u.email, emails, f"{u.name} incorrectly appears in the Patient filter"
            )

    def test_role_filter_is_case_insensitive(self):
        self._assign_role(self.other, "Patient")
        resp = self._get(role="patient")
        self.assertEqual(resp.status_code, 200)
        emails = {u["email"] for u in resp.data["results"]}
        self.assertIn(self.other.email, emails)

    def test_role_filter_unknown_role_returns_empty(self):
        resp = self._get(role="NotARealRole")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["results"], [])

    def test_role_filter_still_requires_user_view_permission(self):
        self.client.force_authenticate(self.other)  # no role assigned
        resp = self._get(role="Patient")
        self.assertEqual(resp.status_code, 403)

    def test_role_filter_combines_with_search(self):
        self._assign_role(self.other, "Patient")
        resp = self._get(role="Patient", q=self.other.name)
        self.assertEqual(resp.status_code, 200)
        emails = {u["email"] for u in resp.data["results"]}
        self.assertEqual(emails, {self.other.email})

    def test_role_filter_no_duplicate_rows_for_single_role(self):
        # Guards the .distinct() call: assigning one role should still
        # produce exactly one row for that user, not a join-multiplied one.
        self._assign_role(self.other, "Patient")
        resp = self._get(role="Patient")
        emails = [u["email"] for u in resp.data["results"]]
        self.assertEqual(emails.count(self.other.email), 1)


# --------------------------------------------------------------------------- #
class AdminUpdateUserTests(Base):
    """PATCH /api/auth/users/{id}/ — admin-managed user edit (Phase: Complete
    User Management)."""

    def test_requires_authentication(self):
        resp = self.client.patch(
            reverse("user-detail", args=[self.other.id]),
            {"name": "Changed"}, format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_requires_user_edit_permission(self):
        self._assign_role(self.patient, "Nurse")  # Nurse holds no Users codes
        self.client.force_authenticate(self.patient)
        resp = self.client.patch(
            reverse("user-detail", args=[self.other.id]),
            {"name": "Changed"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)
        self.other.refresh_from_db()
        self.assertNotEqual(self.other.name, "Changed")

    def test_succeeds_for_admin_role_holder_too(self):
        # Admin holds every code except system.admin, including user.edit —
        # confirms the gate isn't accidentally scoped to Owner only.
        self._assign_role(self.patient, "Admin")
        self.client.force_authenticate(self.patient)
        resp = self.client.patch(
            reverse("user-detail", args=[self.other.id]),
            {"name": "Edited By Admin"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.other.refresh_from_db()
        self.assertEqual(self.other.name, "Edited By Admin")

    def test_succeeds_for_user_edit_holder_updates_fields(self):
        self._assign_role(self.patient, "Owner")  # Owner includes user.edit
        self.client.force_authenticate(self.patient)
        resp = self.client.patch(
            reverse("user-detail", args=[self.other.id]),
            {
                "name": "Updated Name",
                "phone": "+1 555 9999",
                "gender": "other",
                "is_active": False,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.other.refresh_from_db()
        self.assertEqual(self.other.name, "Updated Name")
        self.assertEqual(self.other.phone, "+1 555 9999")
        self.assertEqual(self.other.gender, "other")
        self.assertFalse(self.other.is_active)

    def test_email_is_not_editable(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        original_email = self.other.email
        resp = self.client.patch(
            reverse("user-detail", args=[self.other.id]),
            {"email": "changed@example.com"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.other.refresh_from_db()
        self.assertEqual(self.other.email, original_email)

    def test_role_change_replaces_existing_role(self):
        self._assign_role(self.patient, "Owner")
        self._assign_role(self.other, "Nurse")
        self.client.force_authenticate(self.patient)
        doctor_role = Role.objects.get(name="Doctor")
        resp = self.client.patch(
            reverse("user-detail", args=[self.other.id]),
            {"role": doctor_role.id}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        roles = set(
            UserRole.objects.filter(user=self.other).values_list(
                "role__name", flat=True
            )
        )
        self.assertEqual(roles, {"Doctor"})
        # Doctor's permissions are now effective...
        self.assertTrue(
            PermissionService.has_permission(self.other, "appointment.view")
        )
        self.assertTrue(PermissionService.has_permission(self.other, "emr.edit"))
        # ...and Nurse's role (and anything exclusive to it) is gone, not
        # merely superseded.
        self.assertFalse(
            UserRole.objects.filter(user=self.other, role__name="Nurse").exists()
        )

    def test_role_omitted_leaves_role_unchanged(self):
        self._assign_role(self.patient, "Owner")
        self._assign_role(self.other, "Nurse")
        self.client.force_authenticate(self.patient)
        resp = self.client.patch(
            reverse("user-detail", args=[self.other.id]),
            {"name": "Still Nurse"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            UserRole.objects.filter(user=self.other, role__name="Nurse").exists()
        )
        self.assertEqual(resp.data["role"]["name"], "Nurse")

    def test_invalid_role_rejected(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        resp = self.client.patch(
            reverse("user-detail", args=[self.other.id]),
            {"role": 999999}, format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_get_still_works_unchanged_after_patch_added(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("user-detail", args=[self.other.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["user"]["email"], self.other.email)

    def test_deactivated_user_cannot_obtain_token(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        target = User.objects.create_user(
            email="deactivate-me@example.com", password="OrigPass123!", name="Bye"
        )
        resp = self.client.patch(
            reverse("user-detail", args=[target.id]),
            {"is_active": False}, format="json",
        )
        self.assertEqual(resp.status_code, 200)

        self.client.force_authenticate(None)
        token_resp = self.client.post(
            reverse("token_obtain_pair"),
            {"email": "deactivate-me@example.com", "password": "OrigPass123!"},
            format="json",
        )
        self.assertEqual(token_resp.status_code, 401)

    def test_deactivated_user_existing_token_is_rejected_on_next_request(self):
        """Confirms SimpleJWT's CHECK_USER_IS_ACTIVE (on by default, not
        overridden in settings) enforces deactivation on every authenticated
        request — not just at login — with zero custom code.

        Must use a real JWT access token (obtained via token_obtain_pair and
        sent through the Authorization header), not force_authenticate():
        force_authenticate() injects request.user directly and bypasses DRF's
        authentication classes entirely, so it never actually calls
        JWTAuthentication.get_user() — the exact method that raises
        AuthenticationFailed on CHECK_USER_IS_ACTIVE. A test using
        force_authenticate() here would pass even if that enforcement were
        deleted from SimpleJWT, defeating the point of the test.
        """
        self._assign_role(self.patient, "Owner")
        target = User.objects.create_user(
            email="live-session@example.com", password="OrigPass123!", name="Live"
        )
        token_resp = self.client.post(
            reverse("token_obtain_pair"),
            {"email": "live-session@example.com", "password": "OrigPass123!"},
            format="json",
        )
        self.assertEqual(token_resp.status_code, 200)
        access_token = token_resp.data["access"]

        # Sanity check: the real token works while the account is active.
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        self.assertEqual(
            self.client.get(reverse("my-permissions")).status_code, 200
        )

        self.client.credentials()  # clear so the next call authenticates as patient
        self.client.force_authenticate(self.patient)
        resp = self.client.patch(
            reverse("user-detail", args=[target.id]),
            {"is_active": False}, format="json",
        )
        self.assertEqual(resp.status_code, 200)

        # Same still-valid, unexpired access token — now rejected because the
        # account behind it was deactivated in between requests.
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        resp = self.client.get(reverse("my-permissions"))
        self.assertEqual(resp.status_code, 401)

    def test_reactivating_restores_login(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        target = User.objects.create_user(
            email="reactivate-me@example.com",
            password="OrigPass123!",
            name="Again",
            is_active=False,
        )
        resp = self.client.patch(
            reverse("user-detail", args=[target.id]),
            {"is_active": True}, format="json",
        )
        self.assertEqual(resp.status_code, 200)

        self.client.force_authenticate(None)
        token_resp = self.client.post(
            reverse("token_obtain_pair"),
            {"email": "reactivate-me@example.com", "password": "OrigPass123!"},
            format="json",
        )
        self.assertEqual(token_resp.status_code, 200)


# --------------------------------------------------------------------------- #
class AdminResetPasswordTests(Base):
    """POST /api/auth/users/{id}/reset-password/ (Phase: Complete User
    Management)."""

    def test_requires_authentication(self):
        resp = self.client.post(
            reverse("reset-password", args=[self.other.id]),
            {"new_password": "BrandNewPass123!"}, format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_requires_user_edit_permission(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("reset-password", args=[self.other.id]),
            {"new_password": "BrandNewPass123!"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_succeeds_for_user_edit_holder_and_hashes_password(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("reset-password", args=[self.other.id]),
            {"new_password": "BrandNewPass123!"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.other.refresh_from_db()
        self.assertTrue(self.other.check_password("BrandNewPass123!"))
        self.assertNotEqual(self.other.password, "BrandNewPass123!")

    def test_new_password_works_immediately_for_login(self):
        """Mirrors both the Admin Panel and Mobile App login contract — both
        share the same JWT token endpoint."""
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        self.client.post(
            reverse("reset-password", args=[self.other.id]),
            {"new_password": "BrandNewPass123!"}, format="json",
        )
        self.client.force_authenticate(None)
        resp = self.client.post(
            reverse("token_obtain_pair"),
            {"email": self.other.email, "password": "BrandNewPass123!"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)

        # Old password no longer works.
        resp = self.client.post(
            reverse("token_obtain_pair"),
            {"email": self.other.email, "password": "x"},
            format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_weak_password_rejected(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("reset-password", args=[self.other.id]),
            {"new_password": "12345"}, format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("new_password", resp.data)

    def test_missing_password_rejected(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("reset-password", args=[self.other.id]), {}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_reset_password_on_deactivated_user_does_not_reactivate_them(self):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        target = User.objects.create_user(
            email="inactive-reset@example.com",
            password="OrigPass123!",
            name="Inactive",
            is_active=False,
        )
        resp = self.client.post(
            reverse("reset-password", args=[target.id]),
            {"new_password": "BrandNewPass123!"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        target.refresh_from_db()
        self.assertFalse(target.is_active)

        self.client.force_authenticate(None)
        token_resp = self.client.post(
            reverse("token_obtain_pair"),
            {"email": "inactive-reset@example.com", "password": "BrandNewPass123!"},
            format="json",
        )
        self.assertEqual(token_resp.status_code, 401)


# --------------------------------------------------------------------------- #
class AutomaticStaffProfileLinkingTests(Base):
    """Phase: Automatic Staff Profile Linking — admin-managed user creation
    and role edits automatically create/reuse/deactivate the appropriate
    hospital profile via StaffProfileProvisioningService."""

    def _create_user(self, role_name, **overrides):
        self._assign_role(self.patient, "Owner")
        self.client.force_authenticate(self.patient)
        role = Role.objects.get(name=role_name)
        payload = {
            "name": "New Hire",
            "email": "new.hire@example.com",
            "password": "S3curePassw0rd!",
            "role": role.id,
        }
        payload.update(overrides)
        return self.client.post(reverse("user-list"), payload, format="json")

    # --- Doctor: create --------------------------------------------------

    def test_creating_doctor_user_auto_creates_doctor_profile(self):
        resp = self._create_user("Doctor")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["profile_messages"], ["Doctor profile created"])

        created = User.objects.get(email="new.hire@example.com")
        doctor = Doctor.objects.get(user=created)
        self.assertTrue(doctor.is_active)
        self.assertEqual(doctor.specialty.name, "")
        self.assertEqual(doctor.schedules.count(), 0)
        self.assertEqual(doctor.license_number, "")
        self.assertEqual(doctor.name, "New Hire")

    def test_created_doctor_profile_appears_in_public_directory_immediately(self):
        resp = self._create_user("Doctor")
        self.assertEqual(resp.status_code, 201)
        created = User.objects.get(email="new.hire@example.com")
        doctor = Doctor.objects.get(user=created)

        self.client.force_authenticate(None)
        list_resp = self.client.get(reverse("doctor-list"))
        self.assertEqual(list_resp.status_code, 200)
        ids = {d["id"] for d in list_resp.data["results"]}
        self.assertIn(doctor.id, ids)

    def test_created_doctor_can_authenticate(self):
        resp = self._create_user("Doctor")
        self.assertEqual(resp.status_code, 201)
        self.client.force_authenticate(None)
        token_resp = self.client.post(
            reverse("token_obtain_pair"),
            {"email": "new.hire@example.com", "password": "S3curePassw0rd!"},
            format="json",
        )
        self.assertEqual(token_resp.status_code, 200)

    def test_empty_specialty_sentinel_reused_not_duplicated(self):
        self._create_user("Doctor")
        self._create_user("Doctor", email="second.doctor@example.com")
        self.assertEqual(Specialty.objects.filter(name="").count(), 1)

    def test_empty_specialty_sentinel_excluded_from_public_specialty_list(self):
        self._create_user("Doctor")
        self.client.force_authenticate(None)
        resp = self.client.get(reverse("specialty-list"))
        self.assertEqual(resp.status_code, 200)
        names = {s["name"] for s in resp.data}
        self.assertNotIn("", names)

    # --- Non-Doctor roles: no profile model exists yet --------------------

    def test_creating_receptionist_user_does_not_error_and_creates_no_doctor(self):
        resp = self._create_user("Receptionist")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["profile_messages"], [])
        created = User.objects.get(email="new.hire@example.com")
        self.assertFalse(Doctor.objects.filter(user=created).exists())

    def test_creating_nurse_pharmacist_labtech_users_do_not_error(self):
        for role_name, email in [
            ("Nurse", "nurse.hire@example.com"),
            ("Pharmacist", "pharma.hire@example.com"),
            ("Lab Technician", "lab.hire@example.com"),
        ]:
            resp = self._create_user(role_name, email=email)
            self.assertEqual(resp.status_code, 201, role_name)
            self.assertEqual(resp.data["profile_messages"], [], role_name)

    def test_creating_patient_or_accountant_creates_no_doctor_profile(self):
        for role_name, email in [
            ("Patient", "patient.hire@example.com"),
            ("Accountant", "acct.hire@example.com"),
        ]:
            resp = self._create_user(role_name, email=email)
            self.assertEqual(resp.status_code, 201, role_name)
            self.assertEqual(resp.data["profile_messages"], [], role_name)

    # --- Role edit: Doctor -> Receptionist deactivates, never deletes ----

    def test_role_change_doctor_to_receptionist_deactivates_profile(self):
        create_resp = self._create_user("Doctor")
        target = User.objects.get(email="new.hire@example.com")
        doctor = Doctor.objects.get(user=target)

        receptionist = Role.objects.get(name="Receptionist")
        patch_resp = self.client.patch(
            reverse("user-detail", args=[target.id]),
            {"role": receptionist.id},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(
            patch_resp.data["profile_messages"], ["Doctor profile deactivated"]
        )

        doctor.refresh_from_db()
        self.assertFalse(doctor.is_active)
        # Never deleted — the row and its id are unchanged.
        self.assertTrue(Doctor.objects.filter(pk=doctor.pk).exists())

    def test_deactivated_doctor_profile_disappears_from_public_directory(self):
        self._create_user("Doctor")
        target = User.objects.get(email="new.hire@example.com")
        doctor = Doctor.objects.get(user=target)

        receptionist = Role.objects.get(name="Receptionist")
        self.client.patch(
            reverse("user-detail", args=[target.id]),
            {"role": receptionist.id},
            format="json",
        )

        self.client.force_authenticate(None)
        list_resp = self.client.get(reverse("doctor-list"))
        ids = {d["id"] for d in list_resp.data["results"]}
        self.assertNotIn(doctor.id, ids)

    # --- Role edit: Receptionist -> Doctor reuses, never duplicates ------

    def test_role_change_receptionist_to_doctor_reactivates_existing_profile(self):
        self._create_user("Doctor")
        target = User.objects.get(email="new.hire@example.com")
        original_doctor = Doctor.objects.get(user=target)

        receptionist = Role.objects.get(name="Receptionist")
        self.client.patch(
            reverse("user-detail", args=[target.id]),
            {"role": receptionist.id},
            format="json",
        )
        original_doctor.refresh_from_db()
        self.assertFalse(original_doctor.is_active)

        doctor_role = Role.objects.get(name="Doctor")
        patch_resp = self.client.patch(
            reverse("user-detail", args=[target.id]),
            {"role": doctor_role.id},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(
            patch_resp.data["profile_messages"], ["Doctor profile reactivated"]
        )

        # Exactly one Doctor row for this user — reused, never duplicated.
        self.assertEqual(Doctor.objects.filter(user=target).count(), 1)
        original_doctor.refresh_from_db()
        self.assertTrue(original_doctor.is_active)
        self.assertEqual(original_doctor.pk, Doctor.objects.get(user=target).pk)

    def test_receptionist_to_doctor_with_no_prior_profile_creates_one(self):
        resp = self._create_user("Receptionist")
        target = User.objects.get(email="new.hire@example.com")
        self.assertFalse(Doctor.objects.filter(user=target).exists())

        doctor_role = Role.objects.get(name="Doctor")
        patch_resp = self.client.patch(
            reverse("user-detail", args=[target.id]),
            {"role": doctor_role.id},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(
            patch_resp.data["profile_messages"], ["Doctor profile created"]
        )
        self.assertEqual(Doctor.objects.filter(user=target).count(), 1)

    # --- Idempotency / no duplication -------------------------------------

    def test_reassigning_same_role_creates_no_duplicate_and_no_message(self):
        self._create_user("Doctor")
        target = User.objects.get(email="new.hire@example.com")
        doctor_role = Role.objects.get(name="Doctor")

        patch_resp = self.client.patch(
            reverse("user-detail", args=[target.id]),
            {"role": doctor_role.id},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(patch_resp.data["profile_messages"], [])
        self.assertEqual(Doctor.objects.filter(user=target).count(), 1)

    def test_editing_unrelated_field_without_role_does_not_touch_profile(self):
        self._create_user("Doctor")
        target = User.objects.get(email="new.hire@example.com")
        doctor = Doctor.objects.get(user=target)

        patch_resp = self.client.patch(
            reverse("user-detail", args=[target.id]),
            {"name": "Renamed"},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(patch_resp.data["profile_messages"], [])
        doctor.refresh_from_db()
        self.assertTrue(doctor.is_active)

    # --- Medical history is never deleted ---------------------------------

    def test_deactivating_doctor_profile_preserves_medical_history(self):
        self._create_user("Doctor")
        target = User.objects.get(email="new.hire@example.com")
        doctor = Doctor.objects.get(user=target)
        # Make this doctor own a completed visit, exactly like a real
        # in-service doctor would.
        appt, invoice, visit = self._complete(patient=self.other)
        visit.doctor = doctor
        visit.save(update_fields=["doctor"])
        appt.doctor = doctor
        appt.save(update_fields=["doctor"])

        receptionist = Role.objects.get(name="Receptionist")
        self.client.patch(
            reverse("user-detail", args=[target.id]),
            {"role": receptionist.id},
            format="json",
        )

        # The medical visit row (and its doctor link) must still exist —
        # deactivation never cascades, unlike a delete would.
        visit.refresh_from_db()
        self.assertEqual(visit.doctor_id, doctor.id)


# --------------------------------------------------------------------------- #
class EmrClinicalNotesVisibilityTests(Base):
    def test_owner_sees_own_clinical_notes(self):
        _, _, visit = self._complete(patient=self.patient)
        visit.clinical_notes = "Confidential doctor notes"
        visit.save(update_fields=["clinical_notes"])
        self.client.force_authenticate(self.patient)
        resp = self.client.get(reverse("record-visit-detail", args=[visit.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["clinical_notes"], "Confidential doctor notes")

    def test_user_with_emr_edit_sees_notes_on_any_visit(self):
        _, _, visit = self._complete(patient=self.patient)
        visit.clinical_notes = "Confidential doctor notes"
        visit.save(update_fields=["clinical_notes"])
        self._assign_role(self.other, "Doctor")  # Doctor holds emr.edit
        self.client.force_authenticate(self.other)
        resp = self.client.get(reverse("record-visit-detail", args=[visit.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["clinical_notes"], "Confidential doctor notes")

    def test_user_with_only_emr_view_can_read_visit_but_not_notes(self):
        _, _, visit = self._complete(patient=self.patient)
        visit.clinical_notes = "Confidential doctor notes"
        visit.save(update_fields=["clinical_notes"])
        self._assign_role(self.third, "Nurse")  # Nurse holds emr.view, not emr.edit
        self.client.force_authenticate(self.third)
        resp = self.client.get(reverse("record-visit-detail", args=[visit.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data["clinical_notes"])

    def test_user_with_no_role_and_no_ownership_cannot_read_visit_at_all(self):
        _, _, visit = self._complete(patient=self.patient)
        self.client.force_authenticate(self.other)
        resp = self.client.get(reverse("record-visit-detail", args=[visit.id]))
        self.assertEqual(resp.status_code, 404)


# --------------------------------------------------------------------------- #
class OwnershipPreservedTests(Base):
    """Patient ownership, billing ownership, booking, queue privacy, and
    attendance ownership must behave exactly as before RBAC was layered on."""

    def test_patient_ownership_billing_unchanged(self):
        _, invoice, _ = self._complete(patient=self.patient)
        self.client.force_authenticate(self.patient)
        self.assertEqual(
            self.client.get(reverse("invoice-detail", args=[invoice.id])).status_code,
            200,
        )
        self.client.force_authenticate(self.other)
        self.assertEqual(
            self.client.get(reverse("invoice-detail", args=[invoice.id])).status_code,
            404,
        )

    def test_billing_view_permission_grants_access_to_any_invoice(self):
        _, invoice, _ = self._complete(patient=self.patient)
        self._assign_role(self.other, "Accountant")  # holds billing.view
        self.client.force_authenticate(self.other)
        self.assertEqual(
            self.client.get(reverse("invoice-detail", args=[invoice.id])).status_code,
            200,
        )

    def test_appointment_booking_flow_unchanged(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("appointments"),
            {
                "doctor": self.doctor.id,
                "date": (timezone.localdate() + timedelta(days=1)).isoformat(),
                "time": "11:00", "reason": "Checkup",
            }, format="json",
        )
        self.assertEqual(resp.status_code, 201)

    def test_appointment_cancel_remains_ownership_only_no_rbac(self):
        appt = self._appt()
        self.client.force_authenticate(self.other)
        resp = self.client.post(reverse("appointment-cancel", args=[appt.id]))
        self.assertEqual(resp.status_code, 404)
        self.client.force_authenticate(self.patient)
        resp = self.client.post(reverse("appointment-cancel", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)

    def test_appointment_complete_requires_appointment_edit(self):
        appt = self._appt()
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("appointment-complete", args=[appt.id]), {}, format="json"
        )
        self.assertEqual(resp.status_code, 403)
        self._assign_role(self.other, "Doctor")  # holds appointment.edit
        self.client.force_authenticate(self.other)
        resp = self.client.post(
            reverse("appointment-complete", args=[appt.id]), {}, format="json"
        )
        self.assertEqual(resp.status_code, 200)

    def test_queue_checkin_remains_ownership_only_no_rbac(self):
        appt = self._appt()
        self.client.force_authenticate(self.patient)
        self.assertEqual(
            self.client.post(reverse("appointment-check-in", args=[appt.id])).status_code,
            200,
        )

    def test_doctor_queue_privacy_requires_queue_view(self):
        # Unprivileged patient: denied (privacy boundary preserved).
        self.client.force_authenticate(self.patient)
        resp = self.client.get(
            reverse("doctor-queue", args=[self.doctor.id]),
            {"date": timezone.localdate().isoformat()},
        )
        self.assertEqual(resp.status_code, 403)
        # User holding queue.view: allowed.
        self._assign_role(self.other, "Receptionist")  # holds queue.view
        self.client.force_authenticate(self.other)
        resp = self.client.get(
            reverse("doctor-queue", args=[self.doctor.id]),
            {"date": timezone.localdate().isoformat()},
        )
        self.assertEqual(resp.status_code, 200)

    def test_doctor_queue_anonymous_denied(self):
        resp = self.client.get(
            reverse("doctor-queue", args=[self.doctor.id]),
            {"date": timezone.localdate().isoformat()},
        )
        # Genuinely anonymous (no force_authenticate): JWTAuthentication
        # finds no credentials, so request.successful_authenticator stays
        # None and DRF's APIView.permission_denied() raises NotAuthenticated
        # (401), not PermissionDenied (403) — the same rule that applies to
        # test_my_permissions_requires_authentication below, regardless of
        # which permission class (IsAuthenticated vs. PermissionRequired)
        # rejected the request.
        self.assertEqual(resp.status_code, 401)

    def test_queue_status_ownership_and_widening_preserved(self):
        appt = self._appt()
        # Owner can view their own status.
        self.client.force_authenticate(self.patient)
        self.assertEqual(
            self.client.get(
                reverse("appointment-queue-status", args=[appt.id])
            ).status_code,
            200,
        )
        # A stranger with no permission cannot.
        self.client.force_authenticate(self.other)
        self.assertEqual(
            self.client.get(
                reverse("appointment-queue-status", args=[appt.id])
            ).status_code,
            404,
        )
        # A user holding queue.view can view any appointment's status.
        self._assign_role(self.third, "Receptionist")
        self.client.force_authenticate(self.third)
        self.assertEqual(
            self.client.get(
                reverse("appointment-queue-status", args=[appt.id])
            ).status_code,
            200,
        )

    def test_consultation_start_requires_queue_manage(self):
        appt = self._appt()
        self.client.force_authenticate(self.patient)
        resp = self.client.post(reverse("appointment-start", args=[appt.id]))
        self.assertEqual(resp.status_code, 403)
        self._assign_role(self.other, "Doctor")  # holds queue.manage
        self.client.force_authenticate(self.other)
        resp = self.client.post(reverse("appointment-start", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)

    def test_attendance_confirm_remains_ownership_only_no_rbac(self):
        appt = self._appt(day=timezone.localdate() + timedelta(days=1))
        self.client.force_authenticate(self.other)
        resp = self.client.post(reverse("appointment-confirm", args=[appt.id]))
        self.assertEqual(resp.status_code, 404)
        self.client.force_authenticate(self.patient)
        resp = self.client.post(reverse("appointment-confirm", args=[appt.id]))
        self.assertEqual(resp.status_code, 200)

    def test_attendance_risk_ownership_and_widening_preserved(self):
        appt = self._appt()
        self.client.force_authenticate(self.patient)
        self.assertEqual(
            self.client.get(reverse("appointment-risk", args=[appt.id])).status_code,
            200,
        )
        self.client.force_authenticate(self.other)
        self.assertEqual(
            self.client.get(reverse("appointment-risk", args=[appt.id])).status_code,
            404,
        )
        self._assign_role(self.third, "Nurse")  # holds attendance.view
        self.client.force_authenticate(self.third)
        self.assertEqual(
            self.client.get(reverse("appointment-risk", args=[appt.id])).status_code,
            200,
        )


# --------------------------------------------------------------------------- #
class AnalyticsRbacTests(Base):
    """All five analytics endpoints across Features 1-6 are now RBAC-gated
    with analytics.view (billing.analytics for billing) instead of
    IsAdminUser/is_staff."""

    def test_scheduling_analytics_requires_analytics_view(self):
        self.client.force_authenticate(self.patient)
        self.assertEqual(
            self.client.get(reverse("scheduling-analytics")).status_code, 403
        )
        self._assign_role(self.patient, "Admin")
        self.assertEqual(
            self.client.get(reverse("scheduling-analytics")).status_code, 200
        )

    def test_records_analytics_requires_analytics_view(self):
        self.client.force_authenticate(self.patient)
        self.assertEqual(
            self.client.get(reverse("records-analytics")).status_code, 403
        )
        self._assign_role(self.patient, "Admin")
        self.assertEqual(
            self.client.get(reverse("records-analytics")).status_code, 200
        )

    def test_queue_analytics_requires_analytics_view(self):
        self.client.force_authenticate(self.patient)
        self.assertEqual(
            self.client.get(reverse("queue-analytics")).status_code, 403
        )
        self._assign_role(self.patient, "Admin")
        self.assertEqual(
            self.client.get(reverse("queue-analytics")).status_code, 200
        )

    def test_attendance_analytics_requires_analytics_view(self):
        self.client.force_authenticate(self.patient)
        self.assertEqual(
            self.client.get(reverse("attendance-analytics")).status_code, 403
        )
        self._assign_role(self.patient, "Admin")
        self.assertEqual(
            self.client.get(reverse("attendance-analytics")).status_code, 200
        )

    def test_billing_analytics_requires_billing_analytics(self):
        self.client.force_authenticate(self.patient)
        self.assertEqual(
            self.client.get(reverse("billing-analytics")).status_code, 403
        )
        self._assign_role(self.patient, "Accountant")  # holds billing.analytics
        self.assertEqual(
            self.client.get(reverse("billing-analytics")).status_code, 200
        )


# --------------------------------------------------------------------------- #
class Features1Through6BackwardCompatibleTests(Base):
    """Spot-check the pre-RBAC baseline behaviors that must survive
    unchanged: waitlist join and records/me remain purely ownership-based
    and untouched by RBAC."""

    def test_waitlist_unchanged(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            reverse("waitlist"),
            {
                "doctor": self.doctor.id,
                "date": (timezone.localdate() + timedelta(days=2)).isoformat(),
            }, format="json",
        )
        self.assertEqual(resp.status_code, 201)

    def test_records_me_unchanged(self):
        self.client.force_authenticate(self.patient)
        self.assertEqual(self.client.get(reverse("record-me")).status_code, 200)

    def test_service_catalog_unaffected_by_rbac(self):
        self.client.force_authenticate(self.patient)
        self.assertEqual(self.client.get(reverse("billing-services")).status_code, 200)
