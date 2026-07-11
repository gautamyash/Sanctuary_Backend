from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    return JsonResponse({"status": "ok", "service": "sanctuary-health-api"})


urlpatterns = [
    path("", health),
    path("admin/", admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/auth/", include("authorization.urls")),
    path("api/", include("doctors.urls")),
    path("api/", include("appointments.urls")),
    path("api/", include("queues.urls")),
    path("api/", include("attendance.urls")),
    path("api/", include("medical_records.urls")),
    path("api/", include("billing.urls")),
]

# Serve uploaded medical documents in development.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
