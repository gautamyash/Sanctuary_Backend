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
    """GET /api/specialties/ — public specialty catalog, used by the patient
    directory filter and the admin Doctor onboarding form.

    Excludes the blank-name sentinel Specialty (Phase: Automatic Staff
    Profile Linking) that auto-provisioned Doctor profiles are linked to
    before an admin sets a real specialization — it exists only so
    `Doctor.specialty` (a required FK) can represent "empty specialization"
    without becoming nullable; it was never meant to be a selectable option
    anywhere."""

    queryset = Specialty.objects.exclude(name="")
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
        leave = serializer.save(doctor=doctor)
        if leave.status == DoctorLeave.Status.APPROVED:
            # A leave can be created already-approved in one step (this
            # serializer's `status` is writable) rather than only reaching
            # approved via a later PATCH — same conflict handling either way.
            from appointments.services import handle_leave_conflicts

            handle_leave_conflicts(leave)


class DoctorLeaveDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PUT/PATCH/DELETE /api/doctors/{doctor_id}/leaves/{id}/

    Reviewing a self-service leave request (created by a doctor via
    /api/doctors/me/leaves/) happens here: when staff PATCH `status` to
    approved/rejected, `approved_by`/`approved_at` are stamped
    automatically — additive, the admin panel does not need to send them."""

    serializer_class = DoctorLeaveSerializer
    permission_classes = [PermissionRequired]
    permission_code = "system.admin"

    def get_queryset(self):
        return DoctorLeave.objects.filter(doctor_id=self.kwargs["doctor_id"])

    def perform_update(self, serializer):
        status_changed = (
            "status" in serializer.validated_data
            and serializer.validated_data["status"] != serializer.instance.status
        )
        if status_changed:
            from django.utils import timezone

            leave = serializer.save(
                approved_by=self.request.user, approved_at=timezone.now()
            )
            if leave.status == DoctorLeave.Status.APPROVED:
                # Newly approved — any appointment already booked in this
                # date range is now a conflict; cancel it the same way
                # AppointmentCancelView does (waitlist auto-fill + queue
                # recalculation), just triggered from the leave side.
                from appointments.services import handle_leave_conflicts

                handle_leave_conflicts(leave)
        else:
            serializer.save()


class DoctorSlotsView(APIView):
    """
    GET /api/doctors/{id}/slots/?date=YYYY-MM-DD

    Returns every slot from the doctor's schedule for that weekday,
    marking which are already taken by an active appointment.
    """

    permission_classes = [permissions.AllowAny]

    def get(self, request, pk):
        from appointments.services import (
            active_intervals,
            intervals_overlap,
            is_doctor_on_leave,
        )

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

        # Phase: Advanced Doctor Schedule & Leave Management — an approved
        # leave covering this date means there is nothing to offer, no
        # matter what the weekly schedule says. Additive "on_leave" flag;
        # the existing "slots" contract is unchanged (just empty) so no
        # existing consumer of this endpoint breaks.
        if is_doctor_on_leave(doctor, day):
            return Response({"date": day.isoformat(), "slots": [], "on_leave": True})

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
        from appointments.services import generate_smart_slots, is_doctor_on_leave

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

        # Phase: Advanced Doctor Schedule & Leave Management — same
        # short-circuit as DoctorSlotsView above: an approved leave means no
        # slots at all that day, regardless of the weekly schedule.
        if is_doctor_on_leave(doctor, day):
            return Response(
                {
                    "date": day.isoformat(),
                    "duration": duration,
                    "slots": [],
                    "booked": [],
                    "on_leave": True,
                }
            )

        free, booked = generate_smart_slots(doctor, day, duration)
        return Response(
            {
                "date": day.isoformat(),
                "duration": duration,
                "slots": free,
                "booked": booked,
            }
        )
