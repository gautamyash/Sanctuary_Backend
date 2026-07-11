"""
RBAC decorators for function-based views (Feature 7).

Reusable only — not attached to any existing endpoint. Each decorator wraps
a `def view(request, *args, **kwargs)` function and reuses PermissionService
for every actual authorization decision; none of the permission logic
(superuser bypass, anonymous rejection, the UserRole -> Role ->
RolePermission lookup) is reimplemented here.
"""

import functools

from django.core.exceptions import PermissionDenied

from .services import PermissionService


def permission_required(permission_code):
    """Require a single permission code.

    Delegates directly to PermissionService.require_permission(), which
    already raises PermissionDenied on failure — this decorator adds no
    authorization logic of its own.
    """

    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            PermissionService.require_permission(request.user, permission_code)
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def any_permission(permission_codes):
    """Require at least one of the given permission codes.

    The actual check is PermissionService.has_any_permission(); this
    decorator only translates a False result into PermissionDenied.
    """

    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not PermissionService.has_any_permission(
                request.user, permission_codes
            ):
                raise PermissionDenied(
                    "Requires at least one of: " + ", ".join(permission_codes)
                )
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def all_permissions(permission_codes):
    """Require every one of the given permission codes.

    The actual check is PermissionService.has_all_permissions(); this
    decorator only translates a False result into PermissionDenied.
    """

    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not PermissionService.has_all_permissions(
                request.user, permission_codes
            ):
                raise PermissionDenied(
                    "Requires all of: " + ", ".join(permission_codes)
                )
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator
