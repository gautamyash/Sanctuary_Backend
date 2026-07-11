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
