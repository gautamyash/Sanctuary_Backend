from django.contrib import admin

from .models import ConfigurationValue, HospitalProfile


@admin.register(HospitalProfile)
class HospitalProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "short_name", "timezone", "currency", "updated_at")

    def has_add_permission(self, request):
        # Singleton: never allow a second row via /admin/.
        return not HospitalProfile.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ConfigurationValue)
class ConfigurationValueAdmin(admin.ModelAdmin):
    list_display = (
        "key",
        "label",
        "category",
        "value_type",
        "value",
        "display_order",
        "updated_at",
    )
    list_filter = ("category", "value_type")
    search_fields = ("key", "label")
    ordering = ("category", "display_order", "key")
