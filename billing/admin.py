from django.contrib import admin

from .models import (
    Coupon,
    InsuranceClaim,
    InsuranceProvider,
    Invoice,
    InvoiceItem,
    MedicalService,
    Payment,
    Refund,
    ServiceCategory,
)


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "active")


@admin.register(MedicalService)
class MedicalServiceAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "price", "tax_percentage", "active")
    list_filter = ("category", "active")
    search_fields = ("code", "name")


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "invoice_number",
        "patient",
        "doctor",
        "status",
        "payment_status",
        "total",
        "balance",
        "issued_at",
    )
    list_filter = ("status", "payment_status", "issued_at")
    search_fields = ("invoice_number", "patient__email", "patient__name")
    date_hierarchy = "issued_at"
    inlines = [InvoiceItemInline, PaymentInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("invoice", "method", "amount", "status", "paid_at")
    list_filter = ("method", "status")


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ("invoice", "amount", "processed_at")


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ("code", "discount_type", "value", "minimum_amount", "active", "expiry")


admin.site.register(InsuranceProvider)
admin.site.register(InsuranceClaim)
