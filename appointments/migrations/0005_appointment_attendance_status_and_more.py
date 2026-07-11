# Generated for Feature 4 — additive attendance fields on Appointment.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('appointments', '0004_appointment_patient_checked_in_at_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='appointment',
            name='attendance_status',
            field=models.CharField(choices=[('unknown', 'Unknown'), ('confirmed', 'Confirmed'), ('checked_in', 'Checked In'), ('completed', 'Completed'), ('no_show', 'No Show')], default='unknown', help_text='Attendance lifecycle tracked by the no-show layer.', max_length=12),
        ),
        migrations.AddField(
            model_name='appointment',
            name='confirmed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='appointment',
            name='last_patient_response',
            field=models.CharField(blank=True, default='', help_text='Most recent patient reminder response.', max_length=20),
        ),
    ]
