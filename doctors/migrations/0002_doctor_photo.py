from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("doctors", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="doctor",
            name="photo",
            field=models.URLField(blank=True, default=""),
        ),
    ]
