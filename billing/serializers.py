from rest_framework import serializers

from doctors.serializers import DoctorSerializer

from .models import (
    Coupon,
    Invoice,
    InvoiceItem,
    MedicalService,
    Payment,
    Refund,
    ServiceCategory,
)


class ServiceCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceCategory
        fields = ("id", "name", "description", "color")


class MedicalServiceSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = MedicalService
        fields = (
            "id",
            "code",
            "name",
            "description",
            "price",
            "duration",
            "tax_percentage",
            "category",
            "category_name",
        )


class InvoiceItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceItem
        fields = (
            "id",
            "service",
            "description",
            "quantity",
            "unit_price",
            "discount",
            "tax",
            "total",
        )


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = (
            "id",
            "method",
            "reference",
            "amount",
            "status",
            "paid_at",
            "notes",
        )


class RefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refund
        fields = ("id", "amount", "reason", "processed_at")


class CouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = (
            "id",
            "code",
            "name",
            "discount_type",
            "value",
            "minimum_amount",
            "expiry",
            "active",
        )


class InvoiceSerializer(serializers.ModelSerializer):
    doctor_detail = DoctorSerializer(source="doctor", read_only=True)
    patient_name = serializers.SerializerMethodField()
    items = InvoiceItemSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    refunds = RefundSerializer(many=True, read_only=True)
    coupon_code = serializers.CharField(
        source="coupon.code", read_only=True, default=None
    )

    class Meta:
        model = Invoice
        fields = (
            "id",
            "invoice_number",
            "appointment",
            "patient",
            "patient_name",
            "doctor",
            "doctor_detail",
            "status",
            "subtotal",
            "discount",
            "tax",
            "total",
            "amount_paid",
            "balance",
            "payment_status",
            "coupon_code",
            "issued_at",
            "paid_at",
            "notes",
            "items",
            "payments",
            "refunds",
        )
        read_only_fields = fields

    def get_patient_name(self, obj):
        return getattr(obj.patient, "name", None) or obj.patient.email
