"""
Centralized staff-profile provisioning (Phase: Automatic Staff Profile
Linking).

Single place that decides "does this role need a linked hospital profile,
and if so, create / reuse / deactivate it" — called explicitly from
`AdminCreateUserSerializer.create()` and `AdminUpdateUserSerializer.update()`
so this behavior lives in exactly one place rather than being duplicated
across the create and edit code paths (per the phase's explicit
architecture requirement: "Do NOT duplicate profile creation logic.
Centralize profile creation inside a service/factory. User creation should
simply call that service.").

Supported roles today: only "Doctor" has a real linked profile model
(`doctors.Doctor`, which already carries a nullable `user` OneToOneField
from the earlier Doctor self-service mobile app phase). Receptionist,
Nurse, Pharmacist, and Lab Technician have no profile model anywhere in
this codebase yet — per the phase spec ("create ... profile if available
... otherwise prepare the infrastructure without changing existing
behavior"), those roles are registered with no-op provisioners below.
Adding a real profile model for one of them later is exactly one new
registry entry here, not a rewrite of this service or its call sites.

Doctor rows are NEVER deleted by this service, only deactivated
(`is_active=False`) and reactivated. `medical_records.MedicalVisit.doctor`
is `on_delete=CASCADE`, so deleting a Doctor row would silently destroy a
patient's medical history — deactivating instead is not just a UX nicety
here, it is the only safe option, matching the phase's explicit "Never
delete medical history" requirement.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from doctors.models import Doctor, Specialty


@dataclass
class ProvisioningResult:
    """What happened to a staff profile as a side effect of a role
    assignment — surfaced in the API response and, for profile creation on
    signup, as a toast in the Admin Panel."""

    profile_type: str  # e.g. "Doctor"
    action: str  # "created" | "reused" | "deactivated" | "reactivated"
    profile_id: Optional[int] = None

    @property
    def message(self) -> Optional[str]:
        if self.action == "created":
            return f"{self.profile_type} profile created"
        if self.action == "reactivated":
            return f"{self.profile_type} profile reactivated"
        if self.action == "deactivated":
            return f"{self.profile_type} profile deactivated"
        # "reused" (nothing actually changed) is deliberately silent —
        # nothing new happened, so nothing new needs announcing.
        return None


def _empty_specialty() -> Specialty:
    """Sentinel Specialty used by auto-provisioned Doctor profiles so
    `Doctor.specialty` (a required, PROTECT-ed FK) can represent "empty
    specialization" without becoming nullable — a much larger, riskier
    schema change that would also require every existing
    `doctor.specialty.name` read (DoctorSerializer, DoctorMeSerializer) to
    be made null-safe. `get_or_create` on the unique `name` field means this
    row is created at most once, ever, and reused after that — never
    duplicated. It's deliberately excluded from `SpecialtyListView` (see
    doctors/views.py) so it never appears as a selectable option anywhere.
    """
    specialty, _ = Specialty.objects.get_or_create(name="")
    return specialty


def _provision_doctor(user) -> ProvisioningResult:
    """Create, reuse, or reactivate the Doctor profile linked to `user`.
    Never creates a second Doctor row for the same user — `Doctor.user` is
    a OneToOneField, so a duplicate would fail at the database level even
    if this check were skipped; the check exists so a legitimate reuse
    (Receptionist -> Doctor after having been Doctor before) reactivates the
    existing row instead of erroring."""
    existing = Doctor.objects.filter(user=user).first()
    if existing is not None:
        if not existing.is_active:
            existing.is_active = True
            existing.save(update_fields=["is_active"])
            return ProvisioningResult("Doctor", "reactivated", existing.id)
        return ProvisioningResult("Doctor", "reused", existing.id)

    doctor = Doctor.objects.create(
        name=user.name,
        specialty=_empty_specialty(),
        hospital="",
        user=user,
        is_active=True,
    )
    return ProvisioningResult("Doctor", "created", doctor.id)


def _deactivate_doctor(user) -> Optional[ProvisioningResult]:
    """Deactivate (never delete) the Doctor profile linked to `user`, if any
    and if not already inactive. Preserves every DoctorSchedule/DoctorLeave/
    MedicalVisit row untouched — deactivation only flips `is_active`."""
    doctor = Doctor.objects.filter(user=user).first()
    if doctor is None or not doctor.is_active:
        return None
    doctor.is_active = False
    doctor.save(update_fields=["is_active"])
    return ProvisioningResult("Doctor", "deactivated", doctor.id)


# Registry: role name -> provisioning function. A role with no entry here
# has no linked profile concept at all in this codebase yet (Owner, Admin,
# Accountant, Patient) and is silently skipped — not an error.
_PROVISIONERS: dict[str, Callable] = {
    "Doctor": _provision_doctor,
}

# Registry: role name -> deactivation function, mirroring _PROVISIONERS.
# Receptionist/Nurse/Pharmacist/Lab Technician are intentionally absent from
# both registries: no profile model exists for them yet, so there is
# nothing to create or deactivate. This is the "prepare the infrastructure
# without changing existing behavior" half of the phase spec — the
# call sites below never need to change when one of those roles gets a
# real profile model; only a new registry entry is needed.
_DEACTIVATORS: dict[str, Callable] = {
    "Doctor": _deactivate_doctor,
}


class StaffProfileProvisioningService:
    """The single entry point both the create-user and edit-user flows call.
    Neither flow contains any profile-creation logic of its own."""

    @staticmethod
    def sync_for_role_change(
        user, old_role_name: Optional[str], new_role_name: Optional[str]
    ) -> list[ProvisioningResult]:
        """Reconcile linked staff profiles after a user's role is set (on
        creation, pass `old_role_name=None`) or changed (on edit, pass the
        role name being replaced).

        Returns an empty list when nothing changed (role reassigned to
        itself) or when neither the old nor the new role has a registered
        profile type.
        """
        if old_role_name == new_role_name:
            return []

        results: list[ProvisioningResult] = []

        if old_role_name:
            deactivate = _DEACTIVATORS.get(old_role_name)
            if deactivate:
                result = deactivate(user)
                if result:
                    results.append(result)

        if new_role_name:
            provision = _PROVISIONERS.get(new_role_name)
            if provision:
                results.append(provision(user))

        return results
