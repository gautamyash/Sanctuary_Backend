from django.urls import path

from .views import (
    AddItemView,
    ApplyCouponView,
    BillingAnalyticsView,
    InvoiceDetailView,
    InvoicePdfView,
    MyInvoicesView,
    RecordPaymentView,
    RefundView,
    ServiceCatalogView,
)

urlpatterns = [
    path("billing/my-invoices/", MyInvoicesView.as_view(), name="my-invoices"),
    path("billing/services/", ServiceCatalogView.as_view(), name="billing-services"),
    path("billing/analytics/", BillingAnalyticsView.as_view(), name="billing-analytics"),
    path("billing/invoices/<int:pk>/", InvoiceDetailView.as_view(), name="invoice-detail"),
    path("billing/invoices/<int:pk>/items/", AddItemView.as_view(), name="invoice-items"),
    path(
        "billing/invoices/<int:pk>/payments/",
        RecordPaymentView.as_view(),
        name="invoice-payments",
    ),
    path(
        "billing/invoices/<int:pk>/coupon/",
        ApplyCouponView.as_view(),
        name="invoice-coupon",
    ),
    path("billing/invoices/<int:pk>/refund/", RefundView.as_view(), name="invoice-refund"),
    path("billing/invoices/<int:pk>/pdf/", InvoicePdfView.as_view(), name="invoice-pdf"),
]
