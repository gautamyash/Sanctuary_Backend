# Generated for Feature 5 — Electronic Medical Records (EMR).

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
            name='MedicalVisit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('chief_complaint', models.CharField(blank=True, default='', max_length=255)),
                ('diagnosis', models.CharField(blank=True, default='', max_length=255)),
                ('clinical_notes', models.TextField(blank=True, default='')),
                ('follow_up_date', models.DateField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('appointment', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='medical_visit', to='appointments.appointment')),
                ('doctor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='medical_visits', to='doctors.doctor')),
                ('patient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='medical_visits', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='LabReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=160)),
                ('file', models.FileField(upload_to='lab_reports/%Y/%m/')),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='uploaded_reports', to=settings.AUTH_USER_MODEL)),
                ('medical_visit', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reports', to='medical_records.medicalvisit')),
            ],
            options={
                'ordering': ['-uploaded_at'],
            },
        ),
        migrations.CreateModel(
            name='PatientRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('blood_group', models.CharField(blank=True, choices=[('A+', 'A Pos'), ('A-', 'A Neg'), ('B+', 'B Pos'), ('B-', 'B Neg'), ('AB+', 'Ab Pos'), ('AB-', 'Ab Neg'), ('O+', 'O Pos'), ('O-', 'O Neg')], default='', max_length=3)),
                ('height_cm', models.FloatField(blank=True, null=True)),
                ('weight_kg', models.FloatField(blank=True, null=True)),
                ('bmi', models.FloatField(blank=True, null=True)),
                ('smoking_status', models.CharField(blank=True, choices=[('never', 'Never'), ('former', 'Former'), ('current', 'Current')], default='', max_length=8)),
                ('alcohol', models.CharField(blank=True, choices=[('none', 'None'), ('occasional', 'Occasional'), ('regular', 'Regular')], default='', max_length=12)),
                ('pregnant', models.BooleanField(blank=True, null=True)),
                ('emergency_contact', models.CharField(blank=True, default='', max_length=120)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('patient', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='medical_record', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='Medication',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('dosage', models.CharField(blank=True, default='', max_length=80)),
                ('frequency', models.CharField(blank=True, default='', max_length=80)),
                ('start_date', models.DateField(blank=True, null=True)),
                ('end_date', models.DateField(blank=True, null=True)),
                ('active', models.BooleanField(default=True)),
                ('patient_record', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='medications', to='medical_records.patientrecord')),
            ],
            options={
                'ordering': ['-active', 'name'],
            },
        ),
        migrations.CreateModel(
            name='Allergy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('severity', models.CharField(choices=[('LOW', 'Low'), ('MEDIUM', 'Medium'), ('HIGH', 'High'), ('LIFE_THREATENING', 'Life Threatening')], default='LOW', max_length=16)),
                ('notes', models.CharField(blank=True, default='', max_length=255)),
                ('patient_record', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='allergies', to='medical_records.patientrecord')),
            ],
            options={
                'ordering': ['-severity', 'name'],
            },
        ),
        migrations.CreateModel(
            name='Prescription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('medicine', models.CharField(max_length=120)),
                ('dosage', models.CharField(blank=True, default='', max_length=80)),
                ('frequency', models.CharField(blank=True, default='', max_length=80)),
                ('duration', models.CharField(blank=True, default='', max_length=80)),
                ('instructions', models.CharField(blank=True, default='', max_length=255)),
                ('medical_visit', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='prescriptions', to='medical_records.medicalvisit')),
            ],
            options={
                'ordering': ['id'],
            },
        ),
        migrations.CreateModel(
            name='VitalSigns',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('temperature', models.FloatField(blank=True, help_text='°C', null=True)),
                ('pulse', models.PositiveIntegerField(blank=True, help_text='bpm', null=True)),
                ('blood_pressure', models.CharField(blank=True, default='', help_text='e.g. 120/80', max_length=12)),
                ('oxygen', models.FloatField(blank=True, help_text='SpO2 %', null=True)),
                ('respiration', models.PositiveIntegerField(blank=True, null=True)),
                ('blood_sugar', models.FloatField(blank=True, help_text='mg/dL', null=True)),
                ('weight', models.FloatField(blank=True, help_text='kg', null=True)),
                ('height', models.FloatField(blank=True, help_text='cm', null=True)),
                ('medical_visit', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='vitals', to='medical_records.medicalvisit')),
            ],
        ),
        migrations.AddIndex(
            model_name='medicalvisit',
            index=models.Index(fields=['patient', 'created_at'], name='medical_rec_patient_5f77be_idx'),
        ),
        migrations.AddIndex(
            model_name='medicalvisit',
            index=models.Index(fields=['doctor', 'created_at'], name='medical_rec_doctor__28ec59_idx'),
        ),
        migrations.AddIndex(
            model_name='medicalvisit',
            index=models.Index(fields=['diagnosis'], name='medical_rec_diagnos_723639_idx'),
        ),
    ]
