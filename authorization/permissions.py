"""
DRF permission class for the RBAC layer (Feature 7).

Reusable only — not yet attached to any view. A view opts in later by
setting `permission_classes = [PermissionRequired]` and a `permission_code`
attribute; none of that wiring happens here.
"""

from rest_framework.permissions import BasePermission

from .services import PermissionService


class PermissionRequired(BasePermission):
    """Grants access only if PermissionService confirms the requesting user
    holds `view.permission_code`.

    Example:
        class RefundView(APIView):
            permission_classes = [PermissionRequired]
            permission_code = "billing.refund"

    All evaluation (superuser bypass, anonymous rejection, the actual
    UserRole -> Role -> RolePermission lookup) is delegated to
    PermissionService.has_permission(); this class does not duplicate any
    of that logic.
    """

    message = "You do not have permission to perform this action."

    def has_permission(self, request, view) -> bool:
        permission_code = getattr(view, "permission_code", None)
        if not permission_code:
            # Fail closed: a view that forgets to declare permission_code
            # must not be silently left open.
            return False
        return PermissionService.has_permission(request.user, permission_code)
