from django.shortcuts import get_object_or_404
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Notification
from .serializers import NotificationSerializer


class NotificationListView(generics.ListAPIView):
    """GET /api/notifications/?unread=true — the caller's own notifications.
    Works identically for a patient User or a doctor-linked User, since
    `recipient` is always `request.user`."""

    serializer_class = NotificationSerializer

    def get_queryset(self):
        qs = Notification.objects.filter(recipient=self.request.user)
        if self.request.query_params.get("unread") == "true":
            qs = qs.filter(is_read=False)
        return qs


class NotificationMarkReadView(APIView):
    """POST /api/notifications/{id}/read/"""

    def post(self, request, pk):
        notification = get_object_or_404(
            Notification, pk=pk, recipient=request.user
        )
        if not notification.is_read:
            notification.is_read = True
            notification.save(update_fields=["is_read"])
        return Response(NotificationSerializer(notification).data)


class NotificationMarkAllReadView(APIView):
    """POST /api/notifications/read-all/"""

    def post(self, request):
        Notification.objects.filter(recipient=request.user, is_read=False).update(
            is_read=True
        )
        return Response({"detail": "All notifications marked as read."})
