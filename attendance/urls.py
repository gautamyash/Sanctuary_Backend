from django.urls import path

from .views import (
    AppointmentRiskView,
    AttendanceAnalyticsView,
    ConfirmAttendanceView,
)

urlpatterns = [
    path(
        "appointments/<int:pk>/confirm/",
        ConfirmAttendanceView.as_view(),
        name="appointment-confirm",
    ),
    path(
        "appointments/<int:pk>/risk/",
        AppointmentRiskView.as_view(),
        name="appointment-risk",
    ),
    path(
        "analytics/attendance/",
        AttendanceAnalyticsView.as_view(),
        name="attendance-analytics",
    ),
]
