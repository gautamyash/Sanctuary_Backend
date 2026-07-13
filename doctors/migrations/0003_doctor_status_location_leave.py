import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("doctors", "0002_doctor_photo"),
    ]

    operations = [
        migrations.AddField(
            model_name="doctor",
            name="on_duty",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="doctor",
            name="on_leave",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="doctor",
            name="wing",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.AddField(
            model_name="doctor",
            name="floor",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="doctor",
            name="room",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.CreateModel(
            name="DoctorLeave",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("start_date", models.DateField()),
                ("end_date", models.DateField()),
                ("reason", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("doctor", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="leaves", to="doctors.doctor")),
            ],
            options={
                "ordering": ["-start_date"],
            },
        ),
    ]
