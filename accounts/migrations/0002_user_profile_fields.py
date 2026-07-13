from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='phone',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='user',
            name='gender',
            field=models.CharField(
                blank=True,
                choices=[('female', 'Female'), ('male', 'Male'), ('other', 'Other')],
                default='',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='date_of_birth',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='profile_photo',
            field=models.FileField(blank=True, null=True, upload_to='user_profiles/%Y/%m/'),
        ),
    ]
