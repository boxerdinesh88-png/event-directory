import csv
from django.http import HttpResponse
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html
from django.urls import reverse
from django.db.models import Count, Q
from django.utils import timezone
from datetime import timedelta

from .models import Location, Contact, EmailBlast, OTPCode, ImportLog

# --- 1. BRANDING & CUSTOMIZATION ---
admin.site.site_header = "🌴 Event Directory and Logistic Management"
admin.site.site_title = "Event Directory and Logistic Admin"
admin.site.index_title = "Welcome to Event Directory and Logistic Admin Dashboard"

# Custom admin site styling
admin.site.site_url = "/dashboard/"


# --- 2. UTILITY FUNCTIONS ---
def make_badge(text, color):
    """Create a styled badge for display in admin list views."""
    if not text:
        return "—"
    return format_html(
        '<span style="background-color: {}; color: white; padding: 4px 10px; border-radius: 14px; '
        'font-size: 0.85em; font-weight: 600; display: inline-block;">{}</span>',
        color, text
    )


# --- 3. CSV EXPORT MIXIN ---
class ExportCsvMixin:
    """Allow exporting selected records as CSV."""
    @admin.action(description="📥 Export selected as CSV")
    def export_as_csv(self, request, queryset):
        meta = self.model._meta
        field_names = [field.name for field in meta.fields]
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{meta.model_name}s_{timezone.now().strftime("%Y%m%d")}.csv"'
        
        writer = csv.writer(response)
        writer.writerow([field.verbose_name.title() for field in meta.fields])
        for obj in queryset:
            writer.writerow([getattr(obj, field) for field in field_names])
        return response


# --- 4. USER ADMIN ---
admin.site.unregister(User)

@admin.register(User)
class CustomUserAdmin(BaseUserAdmin):
    list_display = ['username', 'email', 'full_name', 'is_staff_badge', 'is_active_badge', 'last_login']
    list_filter = ['is_staff', 'is_active', 'date_joined']
    search_fields = ['username', 'email', 'first_name', 'last_name']
    ordering = ['-date_joined']
    
    fieldsets = (
        ('Account', {
            'fields': ('username', 'password')
        }),
        ('Personal Info', {
            'fields': ('first_name', 'last_name', 'email')
        }),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')
        }),
        ('Important Dates', {
            'classes': ('collapse',),
            'fields': ('last_login', 'date_joined')
        }),
    )
    
    @admin.display(description='Full Name')
    def full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip() or "—"
    
    @admin.display(description='Staff', boolean=True)
    def is_staff_badge(self, obj):
        return obj.is_staff
    
    @admin.display(description='Active', boolean=True)
    def is_active_badge(self, obj):
        return obj.is_active


# --- 5. LOCATION ADMIN ---
@admin.register(Location)
class LocationAdmin(admin.ModelAdmin, ExportCsvMixin):
    list_display = ['name', 'type_badge', 'status_badge', 'city', 'phone', 'email', 'capacity_display', 'created_at']
    list_filter = ['type', 'status', 'city', 'county', 'created_at']
    search_fields = ['name', 'city', 'email', 'phone', 'contact_name', 'address']
    actions = ['mark_active', 'mark_inactive', 'mark_pending', 'export_as_csv']
    date_hierarchy = 'created_at'
    list_per_page = 50
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'type', 'status', 'description')
        }),
        ('Address & Contact', {
            'fields': ('address', 'city', 'state', 'zip_code', 'county', 'contact_name', 'contact_title')
        }),
        ('Communication', {
            'fields': ('phone', 'email', 'website')
        }),
        ('Social Media', {
            'classes': ('collapse',),
            'fields': ('facebook', 'instagram', 'twitter', 'social_link')
        }),
        ('Facilities', {
            'fields': ('capacity', 'amenities')
        }),
        ('Location Coordinates', {
            'classes': ('collapse',),
            'fields': ('latitude', 'longitude')
        }),
        ('Admin Info', {
            'classes': ('collapse',),
            'fields': ('created_by', 'created_at', 'updated_at')
        }),
    )
    
    readonly_fields = ['created_by', 'created_at', 'updated_at']
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('created_by')
    
    def get_readonly_fields(self, request, obj=None):
        readonly = list(self.readonly_fields)
        if obj:  # Editing existing object
            readonly += ['name']  # Don't allow changing name after creation
        return readonly
    
    @admin.display(description='Type', ordering='type')
    def type_badge(self, obj):
        colors = {
            'apartment': '#17a2b8', 'venue': '#6f42c1', 'club': '#e83e8c',
            'bar': '#fd7e14', 'lounge': '#20c997', 'building': '#6c757d',
            'restaurant': '#d63384', 'hotel': '#007bff', 'park': '#28a745',
            'other': '#6c757d'
        }
        color = colors.get(obj.type, '#6c757d')
        return make_badge(obj.get_type_display(), color)
    
    @admin.display(description='Status', ordering='status')
    def status_badge(self, obj):
        colors = {
            'active': '#28a745', 'inactive': '#dc3545', 'pending': '#ffc107'
        }
        color = colors.get(obj.status, '#6c757d')
        return make_badge(obj.get_status_display(), color)
    
    @admin.display(description='Capacity', ordering='capacity')
    def capacity_display(self, obj):
        if obj.capacity:
            return f"👥 {obj.capacity:,}" if obj.capacity >= 1000 else f"👥 {obj.capacity}"
        return "—"
    
    @admin.action(description="✅ Mark selected as Active")
    def mark_active(self, request, queryset):
        count = queryset.update(status='active')
        self.message_user(request, f"{count} location(s) marked as active.")
    
    @admin.action(description="❌ Mark selected as Inactive")
    def mark_inactive(self, request, queryset):
        count = queryset.update(status='inactive')
        self.message_user(request, f"{count} location(s) marked as inactive.")
    
    @admin.action(description="⏳ Mark selected as Pending")
    def mark_pending(self, request, queryset):
        count = queryset.update(status='pending')
        self.message_user(request, f"{count} location(s) marked as pending.")
    
    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


# --- 6. CONTACT ADMIN ---
@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin, ExportCsvMixin):
    list_display = ['full_name_col', 'email_link', 'phone', 'city', 'gender_icon', 'status_badge', 'subscribed_icon', 'created_at']
    list_filter = ['status', 'is_subscribed', 'gender', 'city', 'created_at']
    search_fields = ['first_name', 'last_name', 'email', 'phone', 'city']
    actions = ['subscribe_contacts', 'unsubscribe_contacts', 'mark_active', 'mark_inactive', 'export_as_csv']
    date_hierarchy = 'created_at'
    list_per_page = 50
    
    fieldsets = (
        ('Personal Information', {
            'fields': ('first_name', 'last_name', 'email', 'phone')
        }),
        ('Demographics', {
            'fields': ('gender', 'age', 'city', 'state', 'zip_code')
        }),
        ('Status & Preferences', {
            'fields': ('status', 'is_subscribed', 'notes')
        }),
        ('Location Association', {
            'fields': ('location',)
        }),
        ('System Info', {
            'classes': ('collapse',),
            'fields': ('created_at', 'updated_at')
        }),
    )
    
    readonly_fields = ['created_at', 'updated_at']
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('location')
    
    @admin.display(description='Name', ordering='last_name')
    def full_name_col(self, obj):
        name = obj.full_name()
        return format_html('<strong>{}</strong>', name)
    
    @admin.display(description='Email', ordering='email')
    def email_link(self, obj):
        if obj.email:
            return format_html('<a href="mailto:{}">{}</a>', obj.email, obj.email)
        return "—"
    
    @admin.display(description='Gender')
    def gender_icon(self, obj):
        icons = {
            'M': '👨 Male', 'F': '👩 Female', 'O': '🌈 Other', '': '—'
        }
        return icons.get(obj.gender, '—')
    
    @admin.display(description='Status', ordering='status')
    def status_badge(self, obj):
        colors = {
            'active': '#28a745', 'inactive': '#dc3545', 'subscribed': '#007bff'
        }
        color = colors.get(obj.status, '#6c757d')
        return make_badge(obj.get_status_display(), color)
    
    @admin.display(description='Subscribed', boolean=True, ordering='is_subscribed')
    def subscribed_icon(self, obj):
        return obj.is_subscribed
    
    @admin.action(description="✅ Subscribe selected contacts")
    def subscribe_contacts(self, request, queryset):
        count = queryset.update(is_subscribed=True)
        self.message_user(request, f"{count} contact(s) subscribed.")
    
    @admin.action(description="❌ Unsubscribe selected contacts")
    def unsubscribe_contacts(self, request, queryset):
        count = queryset.update(is_subscribed=False)
        self.message_user(request, f"{count} contact(s) unsubscribed.")
    
    @admin.action(description="✅ Mark selected as Active")
    def mark_active(self, request, queryset):
        count = queryset.update(status='active')
        self.message_user(request, f"{count} contact(s) marked as active.")
    
    @admin.action(description="❌ Mark selected as Inactive")
    def mark_inactive(self, request, queryset):
        count = queryset.update(status='inactive')
        self.message_user(request, f"{count} contact(s) marked as inactive.")


# --- 7. EMAIL BLAST ADMIN ---
class TargetContactsInline(admin.TabularInline):
    model = EmailBlast.target_contacts.through
    extra = 0
    verbose_name = "Target Contact"
    verbose_name_plural = "Target Contacts"


class TargetLocationsInline(admin.TabularInline):
    model = EmailBlast.target_locations.through
    extra = 0
    verbose_name = "Target Location"
    verbose_name_plural = "Target Locations"


@admin.register(EmailBlast)
class EmailBlastAdmin(admin.ModelAdmin, ExportCsvMixin):
    list_display = ['subject_short', 'status_badge', 'recipient_count', 'total_sent', 'total_failed', 'scheduled_at', 'sent_at']
    list_filter = ['status', 'recipient_type', 'created_at', 'scheduled_at', 'sent_at']
    search_fields = ['subject', 'body_text']
    actions = ['export_as_csv']
    date_hierarchy = 'created_at'
    exclude = ['target_contacts', 'target_locations']
    inlines = [TargetLocationsInline, TargetContactsInline]
    list_per_page = 50
    
    fieldsets = (
        ('Message Content', {
            'fields': ('subject', 'body_html', 'body_text')
        }),
        ('Recipients', {
            'fields': ('recipient_type',)
        }),
        ('Scheduling', {
            'fields': ('scheduled_at', 'status')
        }),
        ('Statistics', {
            'classes': ('collapse',),
            'fields': ('total_sent', 'total_failed', 'sent_at', 'created_by', 'created_at')
        }),
    )
    
    readonly_fields = ['total_sent', 'total_failed', 'sent_at', 'created_by', 'created_at']
    
    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == 'sent':
            return list(self.readonly_fields) + ['subject', 'body_html', 'body_text', 'recipient_type', 'scheduled_at', 'status']
        return self.readonly_fields
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('created_by')
    
    @admin.display(description='Subject', ordering='subject')
    def subject_short(self, obj):
        return obj.subject[:50] + "..." if len(obj.subject) > 50 else obj.subject
    
    @admin.display(description='Status', ordering='status')
    def status_badge(self, obj):
        colors = {
            'draft': '#6c757d', 'scheduled': '#17a2b8', 'sending': '#fd7e14',
            'sent': '#28a745', 'failed': '#dc3545'
        }
        color = colors.get(obj.status, '#6c757d')
        return make_badge(obj.get_status_display(), color)
    
    @admin.display(description='Recipients')
    def recipient_count(self, obj):
        count = obj.target_contacts.count() + obj.target_locations.count()
        return f"👥 {count}" if count > 0 else "—"
    
    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


# --- 8. IMPORT LOG ADMIN ---
@admin.register(ImportLog)
class ImportLogAdmin(admin.ModelAdmin):
    list_display = ['filename', 'rows_imported', 'rows_failed', 'success_rate_badge', 'created_at', 'imported_by_link']
    list_filter = ['created_at', ('rows_failed', admin.EmptyFieldListFilter)]
    search_fields = ['filename', 'errors']
    readonly_fields = ['filename', 'rows_imported', 'rows_failed', 'errors', 'imported_by', 'created_at', 'success_rate_badge']
    date_hierarchy = 'created_at'
    list_per_page = 50
    
    fieldsets = (
        ('Import Details', {
            'fields': ('filename', 'imported_by', 'created_at')
        }),
        ('Statistics', {
            'fields': ('rows_imported', 'rows_failed', 'success_rate_badge')
        }),
        ('Error Log', {
            'classes': ('collapse',),
            'fields': ('errors',)
        }),
    )
    
    def has_add_permission(self, request):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    @admin.display(description='Success Rate')
    def success_rate_badge(self, obj):
        total = obj.rows_imported + obj.rows_failed
        if total == 0:
            return make_badge("0%", "#6c757d")
        
        rate = (obj.rows_imported / total) * 100
        text = f"{rate:.1f}%"
        
        if rate >= 90:
            color = "#28a745"  # Green
        elif rate >= 60:
            color = "#ffc107"  # Yellow
        else:
            color = "#dc3545"  # Red
        
        return make_badge(text, color)
    
    @admin.display(description='Imported By')
    def imported_by_link(self, obj):
        if obj.imported_by:
            url = reverse('admin:auth_user_change', args=[obj.imported_by.id])
            return format_html('<a href="{}">{}</a>', url, obj.imported_by.username)
        return "—"


# --- 9. OTP CODE ADMIN ---
@admin.register(OTPCode)
class OTPCodeAdmin(admin.ModelAdmin):
    list_display = ['email', 'code', 'purpose_badge', 'is_used_badge', 'is_valid_badge', 'created_at', 'age']
    list_filter = ['purpose', 'is_used', ('created_at', admin.RelatedOnlyFieldListFilter)]
    search_fields = ['email', 'code']
    readonly_fields = ['user', 'email', 'code', 'purpose', 'is_used', 'created_at', 'is_valid_badge', 'is_expired_badge']
    date_hierarchy = 'created_at'
    list_per_page = 100
    
    fieldsets = (
        ('OTP Information', {
            'fields': ('email', 'code', 'purpose')
        }),
        ('Status', {
            'fields': ('is_used', 'is_valid_badge', 'is_expired_badge')
        }),
        ('User', {
            'classes': ('collapse',),
            'fields': ('user',)
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('created_at',)
        }),
    )
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    @admin.display(description='Purpose')
    def purpose_badge(self, obj):
        colors = {
            'login': '#007bff', 'register': '#28a745', 'reset': '#fd7e14'
        }
        color = colors.get(obj.purpose, '#6c757d')
        return make_badge(obj.get_purpose_display() if hasattr(obj, 'get_purpose_display') else obj.purpose, color)
    
    @admin.display(description='Used', boolean=True)
    def is_used_badge(self, obj):
        return obj.is_used
    
    @admin.display(description='Valid', boolean=True)
    def is_valid_badge(self, obj):
        return obj.is_valid()
    
    @admin.display(description='Expired', boolean=True)
    def is_expired_badge(self, obj):
        return obj.is_expired()
    
    @admin.display(description='Age (minutes)', ordering='-created_at')
    def age(self, obj):
        delta = timezone.now() - obj.created_at
        minutes = delta.total_seconds() / 60
        if minutes > 60:
            hours = minutes / 60
            return f"{hours:.1f}h"
        return f"{int(minutes)}m"


# ─── Register New Promotion Models ─────────────────────────────────
from .models import (EmailTemplate, SocialPlatformConfig, SocialPost,
                     SMSConfig, SMSBlast)


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display  = ('name', 'category', 'subject', 'from_name', 'created_at')
    list_filter   = ('category',)
    search_fields = ('name', 'subject', 'body')
    ordering      = ('-created_at',)


@admin.register(SocialPlatformConfig)
class SocialPlatformConfigAdmin(admin.ModelAdmin):
    list_display = ('platform', 'is_connected', 'updated_at')
    list_filter  = ('is_connected',)


@admin.register(SocialPost)
class SocialPostAdmin(admin.ModelAdmin):
    list_display  = ('platform', 'status', 'location', 'created_at')
    list_filter   = ('platform', 'status')
    search_fields = ('caption',)
    ordering      = ('-created_at',)


@admin.register(SMSConfig)
class SMSConfigAdmin(admin.ModelAdmin):
    list_display = ('provider', 'from_number', 'is_active', 'updated_at')
    list_filter  = ('is_active',)


@admin.register(SMSBlast)
class SMSBlastAdmin(admin.ModelAdmin):
    list_display  = ('message', 'status', 'recipient_type', 'total_sent', 'total_failed', 'created_at')
    list_filter   = ('status', 'recipient_type')
    ordering      = ('-created_at',)
