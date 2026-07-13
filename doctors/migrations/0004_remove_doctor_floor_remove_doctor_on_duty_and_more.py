import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('doctors', '0003_doctor_status_location_leave'),
    ]

    operations = [
        migrations.AddField(
            model_name='doctor',
            name='user',
            field=models.OneToOneField(
                blank=True,
                help_text="Login account for this doctor's self-service mobile app, if any.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='doctor_profile',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='doctor',
            name='bio',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='doctor',
            name='profile_photo',
            field=models.FileField(blank=True, null=True, upload_to='doctor_profiles/%Y/%m/'),
        ),
        migrations.AddField(
            model_name='doctor',
            name='consultation_duration',
            field=models.PositiveIntegerField(
                default=30,
                help_text='Preferred default consultation length in minutes',
            ),
        ),
        migrations.AddField(
            model_name='doctorleave',
            name='leave_type',
            field=models.CharField(
                choices=[
                    ('medical', 'Medical'),
                    ('personal', 'Personal'),
                    ('study', 'Study'),
                    ('annual', 'Annual'),
                ],
                default='annual',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='doctorleave',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('approved', 'Approved'),
                    ('rejected', 'Rejected'),
                ],
                default='pending',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='doctorleave',
            name='notes',
            field=models.CharField(
                blank=True,
                default='',
                help_text="Reviewer notes (distinct from the doctor's own `reason`).",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name='doctorleave',
            name='approved_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='leave_reviews',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='doctorleave',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='Certification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('issuing_body', models.CharField(blank=True, default='', max_length=200)),
                ('year', models.PositiveIntegerField(blank=True, null=True)),
                ('doctor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='certifications', to='doctors.doctor')),
            ],
            options={
                'ordering': ['-year', 'name'],
            },
        ),
        migrations.CreateModel(
            name='Language',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80)),
                ('proficiency', models.CharField(
                    choices=[
                        ('basic', 'Basic'),
                        ('conversational', 'Conversational'),
                        ('fluent', 'Fluent'),
                        ('native', 'Native'),
                    ],
                    default='conversational',
                    max_length=16,
                )),
                ('doctor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='languages', to='doctors.doctor')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='Education',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('institution', models.CharField(max_length=200)),
                ('degree', models.CharField(blank=True, default='', max_length=200)),
                ('year', models.PositiveIntegerField(blank=True, null=True)),
                ('doctor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='education', to='doctors.doctor')),
            ],
            options={
                'ordering': ['-year', 'institution'],
            },
        ),
    ]
