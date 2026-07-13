from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0001_initial'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='notification',
            old_name='notificatio_recipie_1c1b8a_idx',
            new_name='notificatio_recipie_4e3567_idx',
        ),
        migrations.RenameIndex(
            model_name='notification',
            old_name='notificatio_recipie_2c9f0e_idx',
            new_name='notificatio_recipie_f39341_idx',
        ),
    ]
