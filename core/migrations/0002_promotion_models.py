from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EmailTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('category', models.CharField(choices=[('outreach','Outreach'),('announcement','Announcement'),('reminder','Reminder'),('followup','Follow-up'),('promotion','Promotion')], default='outreach', max_length=20)),
                ('subject', models.CharField(max_length=300)),
                ('body', models.TextField()),
                ('from_name', models.CharField(default='Event Directory and Logistic Team', max_length=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='SocialPlatformConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('platform', models.CharField(choices=[('facebook','Facebook'),('instagram','Instagram'),('twitter','X (Twitter)'),('linkedin','LinkedIn'),('threads','Threads'),('tiktok','TikTok')], max_length=20, unique=True)),
                ('access_token', models.TextField(blank=True)),
                ('app_id', models.CharField(blank=True, max_length=200)),
                ('app_secret', models.CharField(blank=True, max_length=200)),
                ('extra_field', models.CharField(blank=True, max_length=200)),
                ('is_connected', models.BooleanField(default=False)),
                ('last_tested', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['platform']},
        ),
        migrations.CreateModel(
            name='SocialPost',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('platform', models.CharField(max_length=20)),
                ('caption', models.TextField()),
                ('status', models.CharField(choices=[('draft','Draft'),('scheduled','Scheduled'),('posted','Posted'),('failed','Failed')], default='draft', max_length=20)),
                ('scheduled_at', models.DateTimeField(blank=True, null=True)),
                ('posted_at', models.DateTimeField(blank=True, null=True)),
                ('post_url', models.URLField(blank=True)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('location', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='core.location')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='SMSConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(choices=[('twilio','Twilio'),('textbelt','Textbelt'),('vonage','Vonage'),('plivo','Plivo')], max_length=20, unique=True)),
                ('api_key', models.CharField(blank=True, max_length=300)),
                ('api_secret', models.CharField(blank=True, max_length=300)),
                ('from_number', models.CharField(blank=True, max_length=30)),
                ('is_active', models.BooleanField(default=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['provider']},
        ),
        migrations.CreateModel(
            name='SMSBlast',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('message', models.CharField(max_length=160)),
                ('recipient_type', models.CharField(default='all', max_length=30)),
                ('target_city', models.CharField(blank=True, max_length=100)),
                ('custom_numbers', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('draft','Draft'),('scheduled','Scheduled'),('sending','Sending'),('sent','Sent'),('failed','Failed')], default='draft', max_length=20)),
                ('total_sent', models.PositiveIntegerField(default=0)),
                ('total_failed', models.PositiveIntegerField(default=0)),
                ('scheduled_at', models.DateTimeField(blank=True, null=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='Directory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('url', models.URLField()),
                ('submission_url', models.URLField(blank=True)),
                ('category', models.CharField(choices=[('national','National'),('florida','Florida'),('community','Community'),('tourism','Tourism'),('chamber','Chamber of Commerce'),('university','University'),('other','Other')], default='national', max_length=20)),
                ('submission_method', models.CharField(choices=[('form','Web Form'),('email','Email Submission'),('api','API'),('manual','Manual')], default='form', max_length=10)),
                ('is_free', models.BooleanField(default=True)),
                ('notes', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('city', models.CharField(blank=True, max_length=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'ordering': ['name'], 'verbose_name_plural': 'Directories'},
        ),
        migrations.CreateModel(
            name='DirectorySubmission',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_title', models.CharField(max_length=200)),
                ('event_date', models.DateField(blank=True, null=True)),
                ('event_link', models.URLField(blank=True)),
                ('description', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('pending','Pending'),('submitted','Submitted'),('confirmed','Confirmed'),('rejected','Rejected')], default='pending', max_length=20)),
                ('submitted_at', models.DateTimeField(blank=True, null=True)),
                ('notes', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('directory', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='submissions', to='core.directory')),
                ('location', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='core.location')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
