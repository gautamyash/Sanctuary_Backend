from datetime import date as date_cls, datetime, timedelta

from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from authorization.permissions import PermissionRequired

from .models import Doctor, DoctorLeave, DoctorSchedule, Specialty
from .serializers import (
    DoctorLeaveSerializer,
    DoctorScheduleSerializer,
    DoctorSerializer,
    DoctorWriteSerializer,
    SpecialtySerializer,
)


class SpecialtyListView(generics.ListAPIView):
    queryset = Specialty.objects.all()
    serializer_class = SpecialtySerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None


class DoctorListView(generics.ListCreateAPIView):
    """GET /api/doctors/?specialty=Cardiology&search=sarah — public directory
    (unchanged). POST /api/doctors/ — onboard a new doctor (Doctor Management
    module, requires system.admin; no dedicated doctor.* permission code
    exists in the RBAC catalog yet, so this reuses the existing
    administrative code rather than inventing one)."""

    search_fields = ("name", "hospital", "specialty__name")
    ordering_fields = ("rating", "fee", "distance_km")
    filterset_fields = {"specialty__name": ["exact"]}
    permission_code = "system.admin"

    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [PermissionRequired()]

    def get_serializer_class(self):
        return DoctorSerializer if self.request.method == "GET" else DoctorWriteSerializer

    def get_queryset(self):
        qs = Doctor.objects.filter(is_active=True).select_related("specialty")
        specialty = self.request.query_params.get("specialty")
        if specialty and specialty.lower() != "all":
            qs = qs.filter(specialty__name__iexact=specialty)
        return qs

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        doctor = Doctor.objects.select_related("specialty").get(pk=response.data["id"])
        response.data = DoctorSerializer(doctor).data
        return response


class DoctorDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET /api/doctors/{id}/ — unchanged public detail. PUT/PATCH — edit
    (Doctor Management, system.admin). DELETE — soft delete: sets
    is_active=False (the flag the rest of the app already uses to hide a
    doctor), it does not remove the row."""

    permission_code = "system.admin"

    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [PermissionRequired()]

    def get_serializer_class(self):
        return DoctorSerializer if self.request.method == "GET" else DoctorWriteSerializer

    def get_queryset(self):
        return Doctor.objects.filter(is_active=True).select_related("specialty")

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        doctor = Doctor.objects.select_related("specialty").get(pk=response.data["id"])
        response.data = DoctorSerializer(doctor).data
        return response

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active"])


class DoctorScheduleListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/doctors/{doctor_id}/schedules/ — a doctor's weekly
    working-hour blocks (Doctor Management, system.admin)."""

    serializer_class = DoctorScheduleSerializer
    permission_classes = [PermissionRequired]
    permission_code = "system.admin"

    def get_queryset(self):
        return DoctorSchedule.objects.filter(doctor_id=self.kwargs["doctor_id"])

    def perform_create(self, serializer):
        doctor = get_object_or_404(Doctor, pk=self.kwargs["doctor_id"])
        serializer.save(doctor=doctor)


class DoctorScheduleDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PUT/PATCH/DELETE /api/doctors/{doctor_id}/schedules/{id}/"""

    serializer_class = DoctorScheduleSerializer
    permission_classes = [PermissionRequired]
    permission_code = "system.admin"

    def get_queryset(self):
        return DoctorSchedule.objects.filter(doctor_id=self.kwargs["doctor_id"])


class DoctorLeaveListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/doctors/{doctor_id}/leaves/ — simple date-range leave
    records (Doctor Management, system.admin)."""

    serializer_class = DoctorLeaveSerializer
    permission_classes = [PermissionRequired]
    permission_code = "system.admin"

    def get_queryset(self):
        return DoctorLeave.objects.filter(doctor_id=self.kwargs["doctor_id"])

    def perform_create(self, serializer):
        doctor = get_object_or_404(Doctor, pk=self.kwargs["doctor_id"])
        serializer.save(doctor=doctor)


class DoctorLeaveDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PUT/PATCH/DELETE /api/doctors/{doctor_id}/leaves/{id}/"""

    serializer_class = DoctorLeaveSerializer
    permission_classes = [PermissionRequired]
    permission_code = "system.admin"

    def get_queryset(self):
        return DoctorLeave.objects.filter(doctor_id=self.kwargs["doctor_id"])


class DoctorSlotsView(APIView):
    """
    GET /api/doctors/{id}/slots/?date=YYYY-MM-DD

    Returns every slot from the doctor's schedule for that weekday,
    marking which are already taken by an active appointment.
    """

    permission_classes = [permissions.AllowAny]

    def get(self, request, pk):
        from appointments.services import active_intervals, intervals_overlap

        doctor = get_object_or_404(Doctor, pk=pk, is_active=True)
        date_str = request.query_params.get("date")
        try:
            day = (
                datetime.strptime(date_str, "%Y-%m-%d").date()
                if date_str
                else date_cls.today()
            )
        except ValueError:
            return Response({"detail": "Invalid date, use YYYY-MM-DD."}, status=400)

        schedules = doctor.schedules.filter(weekday=day.weekday())
        # Same interval-overlap engine used by smart-slots and booking
        # validation: a fixed-grid slot is unavailable if its span overlaps
        # ANY active appointment, regardless of matching start times. Loaded
        # once for the day so we test every slot in memory.
        busy = active_intervals(doctor, day)

        slots = []
        for block in schedules:
            cursor = datetime.combine(day, block.start_time)
            end = datetime.combine(day, block.end_time)
            step = timedelta(minutes=block.slot_minutes)
            while cursor + step <= end:
                t = cursor.time()
                slot_end = cursor + step
                available = intervals_overlap(cursor, slot_end, busy) is None
                slots.append(
                    {
                        "time": t.strftime("%H:%M"),
                        "label": t.strftime("%I:%M %p").lstrip("0"),
                        "available": available,
                    }
                )
                cursor += step

        return Response({"date": day.isoformat(), "slots": slots})


class DoctorSmartSlotsView(APIView):
    """
    GET /api/doctors/{id}/smart-slots/?date=YYYY-MM-DD&duration=20

    Dynamically packed start times for the requested consultation length,
    fitted around existing variable-duration bookings.
    """

    permission_classes = [permissions.AllowAny]

    def get(self, request, pk):
        from appointments.services import generate_smart_slots

        doctor = get_object_or_404(Doctor, pk=pk, is_active=True)
        date_str = request.query_params.get("date")
        try:
            day = (
                datetime.strptime(date_str, "%Y-%m-%d").date()
                if date_str
                else date_cls.today()
            )
        except ValueError:
            return Response({"detail": "Invalid date, use YYYY-MM-DD."}, status=400)

        try:
            duration = int(request.query_params.get("duration", 30))
        except ValueError:
            return Response({"detail": "duration must be a number."}, status=400)
        duration = max(5, min(240, duration))

        free, booked = generate_smart_slots(doctor, day, duration)
        return Response(
            {
                "date": day.isoformat(),
                "duration": duration,
                "slots": free,
                "booked": booked,
            }
        )
