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
            name='Notification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('notification_type', models.CharField(
                    choices=[
                        ('appointment', 'Appointment'),
                        ('lab_result', 'Lab Result'),
                        ('prescription', 'Prescription'),
                        ('message', 'Message'),
                        ('billing', 'Billing'),
                        ('system', 'System'),
                    ],
                    default='system',
                    max_length=16,
                )),
                ('title', models.CharField(max_length=200)),
                ('body', models.CharField(blank=True, default='', max_length=500)),
                ('is_read', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('recipient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notifications', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(fields=['recipient', 'is_read'], name='notificatio_recipie_1c1b8a_idx'),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(fields=['recipient', 'created_at'], name='notificatio_recipie_2c9f0e_idx'),
        ),
    ]
