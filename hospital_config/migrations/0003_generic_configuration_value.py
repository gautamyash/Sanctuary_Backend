"""
Generalize FeatureFlag into ConfigurationValue (Phase 2 refinement).

Renames the table in place (same data, same primary keys — no new model,
no duplicate storage) and replaces the boolean-only `enabled` column with
a generic typed `value`/`value_type` pair plus `display_order`, so a
configuration entry can hold a boolean, string, integer, or JSON value
instead of only on/off.

Backward compatibility for the 6 existing boolean flags (seeded by
0002_seed_feature_flags) is preserved by data-migrating every existing row
into value_type="boolean" with an equivalent text-encoded `value` — no
row is dropped, and FeatureFlagPublicView continues to expose exactly the
same {key: bool} shape for boolean-typed rows afterward.
"""

from django.db import migrations, models


def migrate_enabled_to_value(apps, schema_editor):
    ConfigurationValue = apps.get_model("hospital_config", "ConfigurationValue")
    for order, row in enumerate(
        ConfigurationValue.objects.order_by("category", "key")
    ):
        row.value = "true" if row.enabled else "false"
        row.value_type = "boolean"
        row.display_order = order
        row.save(update_fields=["value", "value_type", "display_order"])


def migrate_value_to_enabled(apps, schema_editor):
    """Reverse: best-effort restore of the boolean `enabled` column from
    `value`, for any row still typed as boolean."""
    ConfigurationValue = apps.get_model("hospital_config", "ConfigurationValue")
    for row in ConfigurationValue.objects.filter(value_type="boolean"):
        row.enabled = str(row.value).strip().lower() in ("1", "true", "yes", "on")
        row.save(update_fields=["enabled"])


class Migration(migrations.Migration):

    dependencies = [
        ("hospital_config", "0002_seed_feature_flags"),
    ]

    operations = [
        migrations.RenameModel(old_name="FeatureFlag", new_name="ConfigurationValue"),
        migrations.AlterModelOptions(
            name="configurationvalue",
            options={"ordering": ["category", "display_order", "key"]},
        ),
        migrations.RemoveIndex(
            model_name="configurationvalue", name="hospcfg_flag_key_idx"
        ),
        migrations.RemoveIndex(
            model_name="configurationvalue", name="hospcfg_flag_cat_idx"
        ),
        migrations.AddField(
            model_name="configurationvalue",
            name="value",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="configurationvalue",
            name="value_type",
            field=models.CharField(
                choices=[
                    ("boolean", "Boolean"),
                    ("string", "String"),
                    ("integer", "Integer"),
                    ("json", "JSON"),
                ],
                default="boolean",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="configurationvalue",
            name="display_order",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(migrate_enabled_to_value, migrate_value_to_enabled),
        migrations.RemoveField(model_name="configurationvalue", name="enabled"),
        migrations.AddIndex(
            model_name="configurationvalue",
            index=models.Index(fields=["key"], name="hospcfg_cfg_key_idx"),
        ),
        migrations.AddIndex(
            model_name="configurationvalue",
            index=models.Index(fields=["category"], name="hospcfg_cfg_cat_idx"),
        ),
    ]
