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
            name="Permission",
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
                ("code", models.CharField(max_length=100, unique=True)),
                ("name", models.CharField(max_length=150)),
                ("description", models.TextField(blank=True)),
                ("category", models.CharField(blank=True, max_length=80)),
            ],
            options={
                "ordering": ["category", "name"],
            },
        ),
        migrations.CreateModel(
            name="Role",
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
                ("name", models.CharField(max_length=100, unique=True)),
                ("description", models.TextField(blank=True)),
                (
                    "system_role",
                    models.BooleanField(
                        default=False,
                        help_text="System-defined roles (e.g. Admin) cannot be deleted by users.",
                    ),
                ),
                (
                    "priority",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Higher priority roles take precedence when a user holds more than one.",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-priority", "name"],
            },
        ),
        migrations.CreateModel(
            name="UserRole",
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
                ("assigned_at", models.DateTimeField(auto_now_add=True)),
                (
                    "assigned_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="role_assignments_made",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_roles",
                        to="authorization.role",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_roles",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="RolePermission",
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
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "permission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="role_permissions",
                        to="authorization.permission",
                    ),
                ),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="role_permissions",
                        to="authorization.role",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="permission",
            index=models.Index(
                fields=["code"], name="authz_permission_code_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="permission",
            index=models.Index(
                fields=["category"], name="authz_permission_cat_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="role",
            index=models.Index(fields=["name"], name="authz_role_name_idx"),
        ),
        migrations.AddIndex(
            model_name="role",
            index=models.Index(
                fields=["system_role"], name="authz_role_system_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="userrole",
            index=models.Index(
                fields=["user"], name="authz_userrole_user_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="userrole",
            index=models.Index(
                fields=["role"], name="authz_userrole_role_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="userrole",
            constraint=models.UniqueConstraint(
                fields=["user", "role"], name="unique_user_role"
            ),
        ),
        migrations.AddIndex(
            model_name="rolepermission",
            index=models.Index(
                fields=["role", "permission"], name="authz_roleperm_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="rolepermission",
            constraint=models.UniqueConstraint(
                fields=["role", "permission"], name="unique_role_permission"
            ),
        ),
    ]
