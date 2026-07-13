from django.urls import path

from .views import (
    NotificationListView,
    NotificationMarkAllReadView,
    NotificationMarkReadView,
)

urlpatterns = [
    path("notifications/", NotificationListView.as_view(), name="notification-list"),
    path(
        "notifications/read-all/",
        NotificationMarkAllReadView.as_view(),
        name="notification-read-all",
    ),
    path(
        "notifications/<int:pk>/read/",
        NotificationMarkReadView.as_view(),
        name="notification-read",
    ),
]
