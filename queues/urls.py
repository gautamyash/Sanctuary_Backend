from django.urls import path

from .views import (
    AppointmentNoShowView,
    CheckInView,
    ConsultationStartView,
    DoctorQueueView,
    QueueAnalyticsView,
    QueueStatusView,
)

urlpatterns = [
    path(
        "appointments/<int:pk>/check-in/",
        CheckInView.as_view(),
        name="appointment-check-in",
    ),
    path(
        "appointments/<int:pk>/start/",
        ConsultationStartView.as_view(),
        name="appointment-start",
    ),
    path(
        "appointments/<int:pk>/no-show/",
        AppointmentNoShowView.as_view(),
        name="appointment-no-show",
    ),
    path(
        "appointments/<int:pk>/queue-status/",
        QueueStatusView.as_view(),
        name="appointment-queue-status",
    ),
    path(
        "doctors/<int:pk>/queue/",
        DoctorQueueView.as_view(),
        name="doctor-queue",
    ),
    path(
        "analytics/queue/",
        QueueAnalyticsView.as_view(),
        name="queue-analytics",
    ),
]
