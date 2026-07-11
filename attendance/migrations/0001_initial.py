# Generated for Feature 4 — AppointmentRiskPrediction + ReminderLog.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('appointments', '0005_appointment_attendance_status_and_more'),
        ('doctors', '0002_doctor_photo'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AppointmentRiskPrediction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('risk_score', models.FloatField(default=0)),
                ('risk_level', models.CharField(choices=[('LOW', 'Low'), ('MEDIUM', 'Medium'), ('HIGH', 'High'), ('CRITICAL', 'Critical')], default='LOW', max_length=8)),
                ('confidence', models.FloatField(default=0.8)),
                ('prediction_source', models.CharField(choices=[('rule_based', 'Rule Based'), ('ml', 'Ml'), ('manual', 'Manual')], default='rule_based', max_length=12)),
                ('reasons', models.JSONField(blank=True, default=list)),
                ('predicted_at', models.DateTimeField(auto_now=True)),
                ('confirmed', models.BooleanField(default=False)),
                ('actual_outcome', models.CharField(blank=True, default='', max_length=12)),
                ('was_correct', models.BooleanField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('appointment', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='risk_prediction', to='appointments.appointment')),
                ('doctor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='risk_predictions', to='doctors.doctor')),
                ('patient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='risk_predictions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-predicted_at'],
                'indexes': [models.Index(fields=['risk_level'], name='attendance__risk_le_0b27c2_idx'), models.Index(fields=['predicted_at'], name='attendance__predict_1a5228_idx'), models.Index(fields=['doctor'], name='attendance__doctor__e3e9c1_idx'), models.Index(fields=['patient'], name='attendance__patient_395053_idx')],
            },
        ),
        migrations.CreateModel(
            name='ReminderLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('type', models.CharField(choices=[('24_hour', '24 hour'), ('6_hour', '6 hour'), ('2_hour', '2 hour'), ('confirmation', 'Confirmation request'), ('followup', 'No-show follow-up')], max_length=16)),
                ('sent_at', models.DateTimeField(auto_now_add=True)),
                ('delivered', models.BooleanField(default=False)),
                ('opened', models.BooleanField(default=False)),
                ('responded', models.BooleanField(default=False)),
                ('response', models.CharField(blank=True, choices=[('confirmed', 'Confirmed'), ('reschedule', 'Reschedule'), ('cancel', 'Cancel'), ('ignored', 'Ignored')], default='', max_length=12)),
                ('appointment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reminder_logs', to='appointments.appointment')),
            ],
            options={
                'ordering': ['-sent_at'],
                'indexes': [models.Index(fields=['appointment', 'type'], name='attendance__appoint_10551a_idx'), models.Index(fields=['type', 'sent_at'], name='attendance__type_f65ad2_idx')],
            },
        ),
    ]
