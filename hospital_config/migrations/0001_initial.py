import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="HospitalProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(default="Sanctuary Health", max_length=200),
                ),
                ("short_name", models.CharField(blank=True, default="", max_length=50)),
                (
                    "logo",
                    models.FileField(
                        blank=True, null=True, upload_to="hospital/logo/"
                    ),
                ),
                ("email", models.EmailField(blank=True, default="", max_length=254)),
                ("phone", models.CharField(blank=True, default="", max_length=30)),
                ("address", models.TextField(blank=True, default="")),
                ("website", models.URLField(blank=True, default="")),
                ("gst_number", models.CharField(blank=True, default="", max_length=30)),
                (
                    "registration_number",
                    models.CharField(blank=True, default="", max_length=60),
                ),
                ("timezone", models.CharField(default="UTC", max_length=64)),
                ("currency", models.CharField(default="USD", max_length=8)),
                ("primary_color", models.CharField(default="#0061A4", max_length=7)),
                ("secondary_color", models.CharField(default="#00497D", max_length=7)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Hospital Profile",
                "verbose_name_plural": "Hospital Profile",
            },
        ),
        migrations.CreateModel(
            name="FeatureFlag",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("key", models.CharField(max_length=100, unique=True)),
                ("label", models.CharField(blank=True, default="", max_length=150)),
                ("description", models.TextField(blank=True, default="")),
                ("category", models.CharField(blank=True, default="", max_length=80)),
                ("enabled", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["category", "key"],
            },
        ),
        migrations.AddIndex(
            model_name="featureflag",
            index=models.Index(fields=["key"], name="hospcfg_flag_key_idx"),
        ),
        migrations.AddIndex(
            model_name="featureflag",
            index=models.Index(fields=["category"], name="hospcfg_flag_cat_idx"),
        ),
    ]
