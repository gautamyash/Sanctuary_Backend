from django.urls import path

from .views import (
    DoctorDetailView,
    DoctorLeaveDetailView,
    DoctorLeaveListCreateView,
    DoctorListView,
    DoctorScheduleDetailView,
    DoctorScheduleListCreateView,
    DoctorSlotsView,
    DoctorSmartSlotsView,
    SpecialtyListView,
)

urlpatterns = [
    path("specialties/", SpecialtyListView.as_view(), name="specialty-list"),
    path("doctors/", DoctorListView.as_view(), name="doctor-list"),
    path("doctors/<int:pk>/", DoctorDetailView.as_view(), name="doctor-detail"),
    path("doctors/<int:pk>/slots/", DoctorSlotsView.as_view(), name="doctor-slots"),
    path(
        "doctors/<int:pk>/smart-slots/",
        DoctorSmartSlotsView.as_view(),
        name="doctor-smart-slots",
    ),
    path(
        "doctors/<int:doctor_id>/schedules/",
        DoctorScheduleListCreateView.as_view(),
        name="doctor-schedule-list",
    ),
    path(
        "doctors/<int:doctor_id>/schedules/<int:pk>/",
        DoctorScheduleDetailView.as_view(),
        name="doctor-schedule-detail",
    ),
    path(
        "doctors/<int:doctor_id>/leaves/",
        DoctorLeaveListCreateView.as_view(),
        name="doctor-leave-list",
    ),
    path(
        "doctors/<int:doctor_id>/leaves/<int:pk>/",
        DoctorLeaveDetailView.as_view(),
        name="doctor-leave-detail",
    ),
]
