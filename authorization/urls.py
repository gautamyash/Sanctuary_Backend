from django.urls import path

from .views import (
    AssignRoleView,
    MyPermissionsView,
    PermissionListView,
    RoleListCreateView,
    RoleUpdateView,
)

urlpatterns = [
    path("roles/", RoleListCreateView.as_view(), name="role-list-create"),
    path("roles/<int:pk>/", RoleUpdateView.as_view(), name="role-update"),
    path("permissions/", PermissionListView.as_view(), name="permission-list"),
    path("users/<int:pk>/role/", AssignRoleView.as_view(), name="assign-role"),
    path("me/permissions/", MyPermissionsView.as_view(), name="my-permissions"),
]
