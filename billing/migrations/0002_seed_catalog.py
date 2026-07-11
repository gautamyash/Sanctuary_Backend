"""Seed service categories and a starter service catalog (additive)."""

from decimal import Decimal

from django.db import migrations

CATEGORIES = [
    ("Consultation", "#003d9b"),
    ("Laboratory", "#0f766e"),
    ("Radiology", "#6d28d9"),
    ("Procedure", "#b45309"),
    ("Vaccination", "#0369a1"),
    ("Pharmacy", "#be185d"),
    ("Miscellaneous", "#475569"),
]

# code, name, category, price, tax%
SERVICES = [
    ("GEN-CONSULT", "General Consultation", "Consultation", "500.00", "0.00", 20),
    ("CARD-CONSULT", "Cardiology Consultation", "Consultation", "800.00", "0.00", 30),
    ("FOLLOWUP", "Follow-up", "Consultation", "300.00", "0.00", 15),
    ("ECG", "ECG", "Procedure", "400.00", "5.00", 15),
    ("BLD-SUGAR", "Blood Sugar", "Laboratory", "150.00", "5.00", 5),
    ("CBC", "CBC", "Laboratory", "300.00", "5.00", 5),
    ("XRAY", "X-Ray", "Radiology", "600.00", "12.00", 10),
    ("MRI", "MRI", "Radiology", "5000.00", "12.00", 30),
    ("CT", "CT Scan", "Radiology", "3500.00", "12.00", 25),
    ("VACCINE", "Vaccination", "Vaccination", "700.00", "0.00", 10),
    ("HOME-VISIT", "Home Visit", "Miscellaneous", "1200.00", "0.00", 45),
]


def seed(apps, schema_editor):
    ServiceCategory = apps.get_model("billing", "ServiceCategory")
    MedicalService = apps.get_model("billing", "MedicalService")
    cats = {}
    for name, color in CATEGORIES:
        cats[name], _ = ServiceCategory.objects.get_or_create(
            name=name, defaults={"color": color}
        )
    for code, name, cat, price, tax, duration in SERVICES:
        MedicalService.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "category": cats[cat],
                "price": Decimal(price),
                "tax_percentage": Decimal(tax),
                "duration": duration,
            },
        )


def unseed(apps, schema_editor):
    apps.get_model("billing", "MedicalService").objects.filter(
        code__in=[s[0] for s in SERVICES]
    ).delete()
    apps.get_model("billing", "ServiceCategory").objects.filter(
        name__in=[c[0] for c in CATEGORIES]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
