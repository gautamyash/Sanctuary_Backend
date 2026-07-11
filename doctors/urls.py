from django.urls import path

from .views import (
    DoctorDetailView,
    DoctorListView,
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
]
