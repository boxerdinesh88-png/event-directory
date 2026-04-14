from django.urls import path
from . import views

urlpatterns = [
    # Root → redirect to login or dashboard
    path('', views.root_redirect, name='root'),
    path('sw.js', views.service_worker, name='service_worker'),
    path('404/', views.error_404_preview, name='error_404_preview'),

    # ── Auth ─────────────────────────────────────────────────────
    path('auth/login/', views.login_view, name='login'),
    path('auth/register/', views.register_view, name='register'),
    path('auth/verify-email/', views.verify_email_view, name='verify_email'),
    path('auth/send-otp/', views.send_otp, name='send_otp'),
    path('auth/verify-otp/', views.verify_otp, name='verify_otp'),
    path('auth/verify-totp/', views.verify_totp, name='verify_totp'),
    path('auth/logout/', views.logout_view, name='logout'),
    path('auth/password-reset/', views.password_reset_request, name='password_reset'),
    path('auth/reset-password/', views.password_reset_confirm, name='password_reset_confirm'),
    path('auth/totp-setup/', views.totp_setup, name='totp_setup'),

    # ── Dashboard ────────────────────────────────────────────────
    path('dashboard/', views.dashboard, name='dashboard'),
    path('dashboard/help/', views.help, name='help'),

    # ── Locations ────────────────────────────────────────────────
    path('dashboard/locations/', views.location_list, name='location_list'),
    path('dashboard/locations/add/', views.location_add, name='location_add'),
    path('dashboard/locations/<int:pk>/edit/', views.location_edit, name='location_edit'),
    path('dashboard/locations/<int:pk>/delete/', views.location_delete, name='location_delete'),
    path('dashboard/locations/<int:pk>/detail/', views.location_detail, name='location_detail'),

    # ── Contacts ─────────────────────────────────────────────────
    path('dashboard/contacts/', views.contact_list, name='contact_list'),
    path('dashboard/contacts/add/', views.contact_add, name='contact_add'),
    path('dashboard/contacts/<int:pk>/detail/', views.contact_detail, name='contact_detail'),
    path('dashboard/contacts/<int:pk>/edit/', views.contact_edit, name='contact_edit'),
    path('dashboard/contacts/<int:pk>/delete/', views.contact_delete, name='contact_delete'),

    # ── Email Blasts ─────────────────────────────────────────────
    path('dashboard/email/', views.email_list, name='email_list'),
    path('dashboard/email/compose/', views.email_compose, name='email_compose'),
    path('dashboard/email/<int:pk>/detail/', views.email_detail, name='email_detail'),
    path('dashboard/email/<int:pk>/send/', views.email_send, name='email_send'),
    path('dashboard/email/<int:pk>/cancel/', views.email_cancel, name='email_cancel'),
    path('dashboard/email/<int:pk>/delete/', views.email_delete, name='email_delete'),
    path('dashboard/email/attachment/upload/', views.email_attachment_upload, name='email_attachment_upload'),
    path('dashboard/email/attachment/delete/', views.email_attachment_delete, name='email_attachment_delete'),

    # ── Scheduled Emails (Single Recipient) ──────────────────────
    path('dashboard/scheduled-email/', views.scheduled_email_list, name='scheduled_email_list'),
    path('dashboard/scheduled-email/create/', views.scheduled_email_create, name='scheduled_email_create'),
    path('dashboard/scheduled-email/<int:pk>/send/', views.scheduled_email_send_now, name='scheduled_email_send_now'),
    path('dashboard/scheduled-email/<int:pk>/delete/', views.scheduled_email_delete, name='scheduled_email_delete'),

    # ── Import ───────────────────────────────────────────────────
    path('dashboard/import/', views.import_data, name='import_data'),

    path('dashboard/otp-setup/', views.otp_setup, name='otp_setup'),

    # ── Promotion Hub ────────────────────────────────────────────
    path('dashboard/promotion/',           views.promotion_hub,               name='promotion_hub'),

    # ── Social Media ─────────────────────────────────────────────
    path('dashboard/social/',              views.social_media,                name='social_media'),
    path('dashboard/social/edit-account/', views.social_edit_account,         name='social_edit_account'),
    path('dashboard/social/post/<int:pk>/edit/', views.social_post_edit,      name='social_post_edit'),
    path('dashboard/social/post/<int:pk>/delete/', views.social_post_delete,  name='social_post_delete'),
    path('dashboard/social/post-now/<int:pk>/', views.social_post_now,       name='social_post_now'),
    path('dashboard/social/upload-image/', views.upload_image,                name='upload_image'),

    # ── Email Templates ──────────────────────────────────────────
    path('dashboard/email-templates/',         views.email_templates,         name='email_templates'),
    path('dashboard/email-templates/<int:pk>/',views.email_template_detail,   name='email_template_detail'),
    path('dashboard/email-templates/<int:pk>/delete/', views.email_template_delete, name='email_template_delete'),
    path('dashboard/email-templates/<int:pk>/use/',    views.email_template_use,    name='email_template_use'),

    # ── SMS Blast ─────────────────────────────────────────────────
    path('dashboard/sms/',                     views.sms_blast,               name='sms_blast'),
    path('dashboard/sms/<int:pk>/delete/',     views.sms_blast_delete,        name='sms_blast_delete'),

    # ── Reports ───────────────────────────────────────────────────
    path('dashboard/reports/', views.reports, name='reports'),

    # ── User Profile & Settings ─────────────────────────────────────
    path('dashboard/profile/', views.user_profile, name='user_profile'),
    path('dashboard/profile/update/', views.user_profile_update, name='user_profile_update'),
    path('dashboard/profile/upload-photo/', views.user_upload_photo, name='user_upload_photo'),
    path('dashboard/profile/remove-photo/', views.user_remove_photo, name='user_remove_photo'),
    path('dashboard/settings/', views.user_settings, name='user_settings'),
    path('dashboard/settings/update-password/', views.user_update_password, name='user_update_password'),

    # ── API / AJAX ────────────────────────────────────────────────
    path('api/stats/', views.api_stats, name='api_stats'),
    path('api/locations/', views.api_locations, name='api_locations'),
    path('dashboard/api/locations-by-type/', views.api_locations_by_type, name='api_locations_by_type'),
    path('api/locations/by-type/', views.api_locations_by_type, name='api_locations_by_type_legacy'),
    path('dashboard/api/dedupe/', views.api_dedupe, name='api_dedupe'),

    # ── LinkedIn OAuth ─────────────────────────────────────────────
    path('linkedin/connect/', views.linkedin_connect, name='linkedin_connect'),
    path('linkedin/callback/', views.linkedin_callback, name='linkedin_callback'),
    path('linkedin/disconnect/', views.linkedin_disconnect, name='linkedin_disconnect'),
    path('linkedin/test-post/', views.linkedin_test_post, name='linkedin_test_post'),

    # ── Webinar Dashboard ──────────────────────────────────────────
    path('webinar/', views.webinar_dashboard, name='webinar_dashboard'),
    path('webinar/groups/', views.webinar_groups, name='webinar_groups'),
    path('webinar/groups/import/', views.webinar_import_groups, name='webinar_import_groups'),
    path('webinar/groups/<int:pk>/delete/', views.webinar_delete_group, name='webinar_delete_group'),
    path('webinar/accounts/', views.webinar_accounts, name='webinar_accounts'),
    path('webinar/accounts/import/', views.webinar_import_accounts, name='webinar_import_accounts'),
    path('webinar/accounts/<int:pk>/delete/', views.webinar_delete_account, name='webinar_delete_account'),
    path('webinar/events/', views.webinar_events, name='webinar_events'),
    path('webinar/events/create/', views.webinar_event_create, name='webinar_event_create'),
    path('webinar/events/<int:pk>/delete/', views.webinar_delete_event, name='webinar_delete_event'),
    path('webinar/create-post/', views.webinar_create_post, name='webinar_create_post'),
    path('webinar/scheduled/', views.webinar_scheduled, name='webinar_scheduled'),
    path('webinar/post/<int:pk>/delete/', views.webinar_delete_post, name='webinar_delete_post'),
    path('webinar/post/<int:pk>/send/', views.webinar_send_post, name='webinar_send_post'),
    path('webinar/facebook/settings/', views.webinar_facebook_settings, name='webinar_facebook_settings'),
    path('webinar/facebook/save/', views.webinar_save_facebook_settings, name='webinar_save_facebook_settings'),
    path('webinar/facebook/login/', views.webinar_facebook_login, name='webinar_facebook_login'),
    path('webinar/facebook/callback/', views.webinar_facebook_callback, name='webinar_facebook_callback'),
    path('webinar/facebook/logout/', views.webinar_facebook_logout, name='webinar_facebook_logout'),
    path('webinar/post/<int:pk>/post-now/', views.webinar_facebook_post_now, name='webinar_facebook_post_now'),
    path('webinar/analytics/', views.webinar_analytics, name='webinar_analytics'),
    path('webinar/export/', views.webinar_export_report, name='webinar_export_report'),
]

