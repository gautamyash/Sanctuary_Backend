from datetime import date as date_cls, datetime, timedelta

from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Doctor, Specialty
from .serializers import DoctorSerializer, SpecialtySerializer


class SpecialtyListView(generics.ListAPIView):
    queryset = Specialty.objects.all()
    serializer_class = SpecialtySerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None


class DoctorListView(generics.ListAPIView):
    """GET /api/doctors/?specialty=Cardiology&search=sarah"""

    serializer_class = DoctorSerializer
    permission_classes = [permissions.AllowAny]
    search_fields = ("name", "hospital", "specialty__name")
    ordering_fields = ("rating", "fee", "distance_km")
    filterset_fields = {"specialty__name": ["exact"]}

    def get_queryset(self):
        qs = Doctor.objects.filter(is_active=True).select_related("specialty")
        specialty = self.request.query_params.get("specialty")
        if specialty and specialty.lower() != "all":
            qs = qs.filter(specialty__name__iexact=specialty)
        return qs


class DoctorDetailView(generics.RetrieveAPIView):
    queryset = Doctor.objects.filter(is_active=True).select_related("specialty")
    serializer_class = DoctorSerializer
    permission_classes = [permissions.AllowAny]


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
