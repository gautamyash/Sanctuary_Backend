from django.urls import path

from .views import (
    AppointmentCancelView,
    AppointmentCompleteView,
    AppointmentListCreateView,
    PredictDurationView,
    SchedulingAnalyticsView,
    VisitTypeListView,
    WaitlistAcceptView,
    WaitlistLeaveView,
    WaitlistListCreateView,
)

urlpatterns = [
    path("appointments/", AppointmentListCreateView.as_view(), name="appointments"),
    path(
        "appointments/<int:pk>/cancel/",
        AppointmentCancelView.as_view(),
        name="appointment-cancel",
    ),
    path(
        "appointments/<int:pk>/complete/",
        AppointmentCompleteView.as_view(),
        name="appointment-complete",
    ),
    path("visit-types/", VisitTypeListView.as_view(), name="visit-types"),
    path(
        "predictions/duration/",
        PredictDurationView.as_view(),
        name="predict-duration",
    ),
    path(
        "analytics/scheduling/",
        SchedulingAnalyticsView.as_view(),
        name="scheduling-analytics",
    ),
    path("waitlist/", WaitlistListCreateView.as_view(), name="waitlist"),
    path("waitlist/<int:pk>/", WaitlistLeaveView.as_view(), name="waitlist-leave"),
    path(
        "waitlist/<int:pk>/accept/",
        WaitlistAcceptView.as_view(),
        name="waitlist-accept",
    ),
]
