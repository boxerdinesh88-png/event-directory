import datetime
import hashlib
import logging
import secrets
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import random, string
import os


logger = logging.getLogger(__name__)


# ─── User Profile Model ──────────────────────────────────────────────
def user_profile_image_path(instance, filename):
    ext = filename.split('.')[-1]
    filename = f'profile_{instance.user.id}.{ext}'
    return os.path.join('profile_photos', filename)


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    photo = models.ImageField(upload_to=user_profile_image_path, blank=True, null=True)
    bio = models.TextField(blank=True, max_length=500)
    phone = models.CharField(max_length=20, blank=True)
    city = models.CharField(max_length=100, blank=True)
    company = models.CharField(max_length=200, blank=True)
    website = models.URLField(blank=True)
    linkedin = models.URLField(blank=True)
    is_2fa_enabled = models.BooleanField(default=True, help_text='Require OTP on every login')
    is_totp_enabled = models.BooleanField(default=False, help_text='Google Authenticator enabled')
    totp_secret = models.CharField(max_length=32, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.user.username} Profile'

    def delete_photo(self):
        if self.photo:
            if os.path.isfile(self.photo.path):
                os.remove(self.photo.path)
            self.photo = None
            self.save()


# ─── OTP Model ────────────────────────────────────────────────────
class OTPCode(models.Model):
    PURPOSE_CHOICES = [
        ('login', 'Login Verification'),
        ('register', 'Registration Verification'),
        ('reset', 'Password Reset'),
        ('test', 'Test'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    email = models.EmailField(db_index=True)
    code_hash = models.CharField(max_length=64, default='')
    code = models.CharField(max_length=6, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default='login')
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['email', '-created_at']),
            models.Index(fields=['is_used', 'email']),
        ]

    @staticmethod
    def generate_code():
        return ''.join(random.choices(string.digits, k=6))

    @staticmethod
    def hash_code(code):
        return hashlib.sha256(code.encode()).hexdigest()

    def set_code(self, code):
        self.code = code
        self.code_hash = self.hash_code(code)

    def check_code(self, code):
        return self.code_hash == self.hash_code(code) or self.code == code

    def is_valid(self):
        return not self.is_expired() and not self.is_used and self.attempts < self.max_attempts

    def is_expired(self):
        return self.created_at < timezone.now() - datetime.timedelta(minutes=5)

    def increment_attempts(self):
        self.attempts += 1
        self.save(update_fields=['attempts'])

    def __str__(self):
        return f"OTP:{self.email} [{self.purpose}] - {'valid' if self.is_valid() else 'invalid'}"


# ─── Email Verification Token ──────────────────────────────────────
class EmailVerificationToken(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='email_token')
    token = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=['token', 'is_used'])]

    @staticmethod
    def generate():
        return secrets.token_urlsafe(48)

    def is_expired(self):
        return self.created_at < timezone.now() - datetime.timedelta(minutes=15)

    def is_valid(self):
        return not self.is_used and not self.is_expired()

    def __str__(self):
        return f"Token:{self.user.email} - {'valid' if self.is_valid() else 'invalid'}"


# ─── Login Attempt Tracker ────────────────────────────────────────
class LoginAttempt(models.Model):
    ip_address = models.GenericIPAddressField(db_index=True)
    email = models.EmailField(blank=True, default='')
    attempted_at = models.DateTimeField(auto_now_add=True)
    was_successful = models.BooleanField(default=False)
    failure_reason = models.CharField(max_length=100, blank=True, default='')
    user_agent = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-attempted_at']
        indexes = [
            models.Index(fields=['ip_address', '-attempted_at']),
            models.Index(fields=['email', '-attempted_at']),
        ]

    @staticmethod
    def get_client_ip(request):
        x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded:
            return x_forwarded.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '0.0.0.0')

    def __str__(self):
        status = 'OK' if self.was_successful else 'FAIL'
        return f"{self.ip_address} - {self.email or 'unknown'} [{status}]"


# ─── Location / Venue / Apartment ─────────────────────────────────
class Location(models.Model):
    TYPE_CHOICES = [
        ('apartment', 'Apartment Complex'),
        ('venue', 'Event Venue'),
        ('club', 'Night Club / Lounge'),
        ('bar', 'Bar'),
        ('lounge', 'Lounge'),
        ('building', 'Building'),
        ('restaurant', 'Restaurant / Bar'),
        ('hotel', 'Hotel / Resort'),
        ('park', 'Park / Outdoor'),
        ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('pending', 'Pending Review'),
    ]

    name = models.CharField(max_length=200)
    # FIXED: Add db_index=True to Location.type, Location.status, Location.city
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='venue', db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active', db_index=True)

    # Address
    address = models.CharField(max_length=300)
    city = models.CharField(max_length=100, default='', db_index=True)
    state = models.CharField(max_length=50, default='Florida')
    zip_code = models.CharField(max_length=10, blank=True)
    county = models.CharField(max_length=100, blank=True)

    # Contact
    contact_name = models.CharField(max_length=150, blank=True)
    contact_title = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    facebook = models.URLField(blank=True)
    instagram = models.URLField(blank=True)
    twitter = models.URLField(blank=True)
    social_link = models.URLField(blank=True)

    # Details
    capacity = models.PositiveIntegerField(null=True, blank=True)
    amenities = models.TextField(blank=True)
    description = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    # Coordinates
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} – {self.city}"


# ─── Contact / Member ─────────────────────────────────────────────
class Contact(models.Model):
    GENDER_CHOICES = [('M', 'Male'), ('F', 'Female'), ('O', 'Other'), ('', 'Prefer not to say')]
    STATUS_CHOICES = [('active', 'Active'), ('inactive', 'Inactive'), ('subscribed', 'Subscribed')]

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True, null=True, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True)
    age = models.PositiveIntegerField(null=True, blank=True)
    # FIXED: Add db_index=True to Contact.city, Contact.status, Contact.is_subscribed
    city = models.CharField(max_length=100, blank=True, db_index=True)
    state = models.CharField(max_length=50, default='Florida')
    zip_code = models.CharField(max_length=10, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active', db_index=True)
    notes = models.TextField(blank=True)
    is_subscribed = models.BooleanField(default=True, db_index=True)
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['last_name', 'first_name']

    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    def __str__(self):
        return self.full_name()


# ─── Email Attachment ─────────────────────────────────────────────
def email_attachment_path(instance, filename):
    ext = filename.split('.')[-1] if '.' in filename else ''
    return f'email_attachments/{instance.blast.id}/{instance.blob_name}.{ext}' if instance.id else f'email_attachments/temp/{filename}'


class EmailAttachment(models.Model):
    blast = models.ForeignKey('EmailBlast', on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='email_attachments/%Y/%m/')
    blob_name = models.CharField(max_length=255)
    original_name = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.original_name

    def save(self, *args, **kwargs):
        if not self.blob_name:
            import uuid
            self.blob_name = str(uuid.uuid4())
        if self.file and not self.file_size:
            self.file_size = self.file.size
        if self.file and not self.content_type:
            self.content_type = self.file.content_type or 'application/octet-stream'
        if not self.original_name and self.file:
            self.original_name = self.file.name
        super().save(*args, **kwargs)


# ─── Email Blast ──────────────────────────────────────────────────
class EmailBlast(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('scheduled', 'Scheduled'),
        ('sending', 'Sending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    subject = models.CharField(max_length=300)
    body_html = models.TextField()
    body_text = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    recipient_type = models.CharField(max_length=30, default='all')
    target_locations = models.ManyToManyField(Location, blank=True)
    target_contacts = models.ManyToManyField(Contact, blank=True)
    total_sent = models.PositiveIntegerField(default=0)
    total_failed = models.PositiveIntegerField(default=0)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Blast: {self.subject} [{self.status}]"


# ─── Scheduled Email (Single Recipient) ──────────────────────────────
class ScheduledEmail(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]

    recipient_email = models.EmailField()
    subject = models.CharField(max_length=300)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'scheduled_at']),
        ]

    def __str__(self):
        return f"Email to {self.recipient_email}: {self.subject} [{self.status}]"

    def send(self):
        """Send the email immediately. Returns (success, error_message)."""
        from django.core.mail import send_mail
        from django.conf import settings

        try:
            send_mail(
                subject=self.subject,
                message=self.message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[self.recipient_email],
                fail_silently=False,
            )
            self.status = 'sent'
            self.sent_at = timezone.now()
            self.error_message = ''
            self.save(update_fields=['status', 'sent_at', 'error_message'])
            logger.info(f"Email #{self.id} sent successfully to {self.recipient_email}")
            return True, None
        except Exception as e:
            self.status = 'failed'
            self.error_message = str(e)
            self.save(update_fields=['status', 'error_message'])
            logger.error(f"Email #{self.id} failed to send: {e}")
            return False, str(e)


# ─── Email Template ───────────────────────────────────────────────
class EmailTemplate(models.Model):
    CATEGORY_CHOICES = [
        ('outreach', 'Outreach'),
        ('announcement', 'Announcement'),
        ('reminder', 'Reminder'),
        ('followup', 'Follow-up'),
        ('promotion', 'Promotion'),
    ]
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='outreach')
    subject = models.CharField(max_length=300)
    body = models.TextField()
    from_name = models.CharField(max_length=100, default='Event Directory and Logistic Team')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} [{self.category}]"

    def render(self, context: dict) -> tuple:
        """Return (subject, body) with variables substituted."""
        subject = self.subject
        body = self.body
        for key, val in context.items():
            placeholder = '{' + key + '}'
            subject = subject.replace(placeholder, str(val))
            body = body.replace(placeholder, str(val))
        return subject, body


# ─── Social Platform Config ────────────────────────────────────────
class SocialPlatformConfig(models.Model):
    PLATFORM_CHOICES = [
        ('facebook', 'Facebook'),
        ('instagram', 'Instagram'),
        ('twitter', 'X (Twitter)'),
        ('linkedin', 'LinkedIn'),
        ('threads', 'Threads'),
        ('pinterest', 'Pinterest'),
    ]
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    account_name = models.CharField(max_length=100, blank=True, help_text='Friendly name for this account (e.g., "Main Page", "Events Page")')
    access_token = models.TextField(blank=True)
    app_id = models.CharField(max_length=200, blank=True)
    app_secret = models.CharField(max_length=200, blank=True)
    extra_field = models.CharField(max_length=200, blank=True, help_text='Page ID / Account ID / URN')
    is_connected = models.BooleanField(default=False)
    is_primary = models.BooleanField(default=False, help_text='Use this account as the default for posting')
    last_tested = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-is_primary', 'platform', 'account_name']
        verbose_name_plural = 'Social Platform Configs'

    def __str__(self):
        name = self.account_name or self.get_platform_display()
        status = 'connected' if self.is_connected else 'disconnected'
        primary = ' [Primary]' if self.is_primary else ''
        return f"{name} ({status}){primary}"


# ─── Social Post ────────────────────────────────────────────────────
class SocialPost(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('scheduled', 'Scheduled'),
        ('posted', 'Posted'),
        ('failed', 'Failed'),
    ]
    MEDIA_CHOICES = [
        ('text', 'Text'),
        ('image', 'Image'),
        ('video', 'Video'),
        ('document', 'Document'),
    ]
    platform = models.CharField(max_length=20)
    account_name = models.CharField(max_length=100, blank=True, help_text='Which account was used')
    media_type = models.CharField(max_length=20, choices=MEDIA_CHOICES, default='text')
    media_url = models.URLField(blank=True)
    link_url = models.URLField(blank=True)
    caption = models.TextField()
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    scheduled_at = models.DateTimeField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    post_url = models.URLField(blank=True)
    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        account = f" ({self.account_name})" if self.account_name else ""
        return f"{self.platform}{account} post [{self.status}]"


# ─── SMS Config ────────────────────────────────────────────────────
class SMSConfig(models.Model):
    PROVIDER_CHOICES = [
        ('twilio', 'Twilio'),
        ('textbelt', 'Textbelt'),
        ('vonage', 'Vonage'),
        ('plivo', 'Plivo'),
    ]
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, unique=True)
    api_key = models.CharField(max_length=300, blank=True)
    api_secret = models.CharField(max_length=300, blank=True)
    from_number = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['provider']

    def __str__(self):
        return f"{self.get_provider_display()} ({'active' if self.is_active else 'inactive'})"


# ─── SMS Blast ──────────────────────────────────────────────────────
class SMSBlast(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('scheduled', 'Scheduled'),
        ('sending', 'Sending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]
    message = models.CharField(max_length=160)
    recipient_type = models.CharField(max_length=30, default='all')
    target_city = models.CharField(max_length=100, blank=True)
    custom_numbers = models.TextField(blank=True, help_text='Comma-separated phone numbers')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    total_sent = models.PositiveIntegerField(default=0)
    total_failed = models.PositiveIntegerField(default=0)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"SMS Blast [{self.status}] – {self.message[:40]}"

    def get_recipients(self):
        """Return queryset of contacts to receive this SMS."""
        qs = Contact.objects.filter(created_by=self.created_by, is_subscribed=True).exclude(phone='')
        if self.recipient_type == 'city' and self.target_city:
            qs = qs.filter(city__iexact=self.target_city)
        return qs


# ─── Data Import Log ──────────────────────────────────────────────
class ImportLog(models.Model):
    filename = models.CharField(max_length=255)
    rows_imported = models.PositiveIntegerField(default=0)
    rows_failed = models.PositiveIntegerField(default=0)
    errors = models.TextField(blank=True)
    imported_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at'] # FIXED: Add Meta.ordering

    def __str__(self):
        return f"Import: {self.filename} ({self.rows_imported} rows)"


# ═══════════════════════════════════════════════════════════════════════════════
# WEBINAR DASHBOARD MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class WebinarGroup(models.Model):
    SIZE_CATEGORY_CHOICES = [
        ('small', 'Below 10,000'),
        ('medium', '10,000 - 100,000'),
        ('large', 'Above 100,000'),
    ]
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('restricted', 'Restricted'),
        ('error', 'Error'),
        ('inactive', 'Inactive'),
    ]
    ENGAGEMENT_CHOICES = [
        ('high', 'High'),
        ('medium', 'Medium'),
        ('low', 'Low'),
    ]

    name = models.CharField(max_length=300)
    url = models.URLField(max_length=500, blank=True)
    member_count = models.PositiveIntegerField(default=0)
    size_category = models.CharField(max_length=10, choices=SIZE_CATEGORY_CHOICES, db_index=True)
    engagement = models.CharField(max_length=10, choices=ENGAGEMENT_CHOICES, default='medium')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active', db_index=True)
    niche = models.CharField(max_length=100, blank=True)
    tags = models.CharField(max_length=500, blank=True, help_text='Comma-separated tags')
    last_posted = models.DateTimeField(null=True, blank=True)
    posts_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-member_count']
        indexes = [
            models.Index(fields=['size_category', 'status']),
            models.Index(fields=['niche']),
        ]

    def save(self, *args, **kwargs):
        if self.member_count < 10000:
            self.size_category = 'small'
        elif self.member_count < 100000:
            self.size_category = 'medium'
        else:
            self.size_category = 'large'
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.get_size_category_display()})"


class WebinarAccount(models.Model):
    ACCOUNT_TYPE_CHOICES = [
        ('page', 'Facebook Page'),
        ('group', 'Facebook Group'),
        ('profile', 'Personal Profile'),
    ]
    TAG_CHOICES = [
        ('personal', 'Personal'),
        ('business', 'Business'),
        ('niche', 'Niche-Specific'),
        ('main', 'Main'),
        ('backup', 'Backup'),
    ]
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('restricted', 'Restricted'),
        ('error', 'Error'),
        ('inactive', 'Inactive'),
    ]

    name = models.CharField(max_length=300)
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES)
    url = models.URLField(max_length=500, blank=True)
    page_id = models.CharField(max_length=100, blank=True, help_text='Facebook Page/Group ID')
    access_token = models.TextField(blank=True, help_text='Securely stored access token')
    token_expires = models.DateTimeField(null=True, blank=True)
    tags = models.CharField(max_length=200, blank=True, help_text='Comma-separated: personal, business, niche')
    is_active = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    followers_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_account_type_display()})"


class WebinarEvent(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('scheduled', 'Scheduled'),
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    registration_link = models.URLField(max_length=500, blank=True)
    event_date = models.DateTimeField(null=True, blank=True)
    event_end_date = models.DateTimeField(null=True, blank=True)
    cover_image = models.ImageField(upload_to='webinar_covers/', blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-event_date']

    def __str__(self):
        return self.title


class WebinarPost(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('scheduled', 'Scheduled'),
        ('posting', 'Posting'),
        ('posted', 'Posted'),
        ('partial', 'Partially Posted'),
        ('failed', 'Failed'),
    ]

    event = models.ForeignKey(WebinarEvent, on_delete=models.SET_NULL, null=True, blank=True)
    title = models.CharField(max_length=300)
    content = models.TextField()
    image = models.ImageField(upload_to='webinar_posts/', blank=True, null=True)
    link_url = models.URLField(max_length=500, blank=True)
    spin_variations = models.TextField(blank=True, help_text='JSON array of spin variations')
    status = models.Status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    scheduled_at = models.DateTimeField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    target_groups = models.ManyToManyField(WebinarGroup, blank=True, related_name='targeted_posts')
    target_accounts = models.ManyToManyField(WebinarAccount, blank=True, related_name='targeted_posts')
    delay_minutes = models.PositiveIntegerField(default=5, help_text='Delay between posts in minutes')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} [{self.status}]"


class WebinarPostLog(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
    ]

    post = models.ForeignKey(WebinarPost, on_delete=models.CASCADE)
    group = models.ForeignKey(WebinarGroup, on_delete=models.SET_NULL, null=True, blank=True)
    account = models.ForeignKey(WebinarAccount, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    post_url = models.URLField(max_length=500, blank=True)
    error_message = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    likes_count = models.PositiveIntegerField(default=0)
    comments_count = models.PositiveIntegerField(default=0)
    shares_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        dest = self.group.name if self.group else self.account.name if self.account else 'Unknown'
        return f"Post to {dest} [{self.status}]"
