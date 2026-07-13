"""
Ownership permission for the doctor self-service mobile API.

Deliberately independent of the RBAC `PermissionRequired` class used by the
admin endpoints (authorization app). Admin endpoints ask "does this user
hold capability X anywhere in the hospital"; this asks a narrower question —
"is this request coming from a user who owns exactly one Doctor record" —
and every self-service view then scopes all reads/writes to that one row via
`request.user.doctor_profile`. Never accepts a doctor id from the client.
"""

from rest_framework.permissions import BasePermission


class IsLinkedDoctor(BasePermission):
    """Grants access only if the authenticated user has a linked Doctor
    profile (Doctor.user == request.user)."""

    message = "This account is not linked to a doctor profile."

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and getattr(user, "doctor_profile", None) is not None
        )
