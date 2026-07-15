from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("doctors", "0004_remove_doctor_floor_remove_doctor_on_duty_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="doctor",
            name="license_number",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
    ]
