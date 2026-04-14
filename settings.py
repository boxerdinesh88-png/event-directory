"""
Event Directory and Logistic & Matchmaking Services - Django Settings
"""
import os
import sys
import logging
from pathlib import Path
try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = lambda *args, **kwargs: None

# Load .env from project root (explicit path so it always works)
_ENV_PATH = Path(__file__).resolve().parent / '.env'
try:
    load_dotenv(_ENV_PATH)
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent

DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'

SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    if DEBUG:
        import secrets
        SECRET_KEY = secrets.token_urlsafe(50)
        print(f"[DEV] Generated temporary SECRET_KEY", file=sys.stderr)
    else:
        raise ValueError("SECRET_KEY environment variable is required for production")

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '' )
if ALLOWED_HOSTS:
    ALLOWED_HOSTS = [h.strip() for h in ALLOWED_HOSTS.split(',') if h.strip()]
else:
    ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'testserver'] if DEBUG else []

# Auto-detect ngrok domain from MEDIA_PUBLIC_BASE and add to ALLOWED_HOSTS
_MEDIA_PUBLIC_BASE_RAW = os.environ.get('MEDIA_PUBLIC_BASE', '').strip().rstrip('/')
if _MEDIA_PUBLIC_BASE_RAW:
    from urllib.parse import urlparse as _urlparse
    _parsed_ngrok = _urlparse(_MEDIA_PUBLIC_BASE_RAW)
    _ngrok_host = _parsed_ngrok.hostname
    if _ngrok_host and _ngrok_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_ngrok_host)
        print(f"[DEBUG] Auto-added ngrok host to ALLOWED_HOSTS: {_ngrok_host}", file=sys.stderr)

# Allow all *.ngrok-free.dev subdomains in development (ngrok URLs change)
if DEBUG:
    ALLOWED_HOSTS.append('.ngrok-free.dev')
    ALLOWED_HOSTS.append('.ngrok.io')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'django.contrib.humanize',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'core',
]

# Facebook API Configuration (for Webinar System)
FACEBOOK_APP_ID = os.environ.get('FACEBOOK_APP_ID', '')
FACEBOOK_APP_SECRET = os.environ.get('FACEBOOK_APP_SECRET', '')
FACEBOOK_REDIRECT_URI = os.environ.get('FACEBOOK_REDIRECT_URI', 'http://127.0.0.1:8000/webinar/facebook/callback/')

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'core.middleware.AuthErrorCatchMiddleware',
]

ROOT_URLCONF = 'urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.user_profile_context',
            ],
        },
    },
]

# ─── MySQL Database ───────────────────────────────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ.get('DB_NAME', ''),
        'USER': os.environ.get('DB_USER', ''),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', ''),
        'PORT': os.environ.get('DB_PORT', ''),
        'CONN_MAX_AGE': 60,
        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
}

# FIXED: Add django.core.cache.backends.locmem.LocMemCache as CACHES default
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'florida-events-cache',
    }
}

# ─── OTP Provider Config (ALL FREE options) ──────────────────────
OTP_PROVIDER = os.environ.get('OTP_PROVIDER', 'smtp')

BREVO_API_KEY      = os.environ.get('BREVO_API_KEY', '')
BREVO_SENDER_EMAIL = os.environ.get('BREVO_SENDER_EMAIL', 'noreply@yourdomain.com')
BREVO_SENDER_NAME  = os.environ.get('BREVO_SENDER_NAME', 'Event Directory and Logistic')

RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
RESEND_FROM    = os.environ.get('RESEND_FROM', 'Event Directory and Logistic<onboarding@resend.dev>')

MAILGUN_API_KEY = os.environ.get('MAILGUN_API_KEY', '')
MAILGUN_DOMAIN  = os.environ.get('MAILGUN_DOMAIN', '')
MAILGUN_SENDER  = os.environ.get('MAILGUN_SENDER', '')

ABSTRACT_OTP_API_KEY = os.environ.get('ABSTRACT_OTP_API_KEY', '')
# Email Configuration
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True').lower() == 'true'
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'Event Directory and Logistic<noreply@example.com>')
# ─── Static & Media ──────────────────────────────────────────────
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static'] if (BASE_DIR / 'static').exists() else []
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ─── Public Media URL (for Instagram / external APIs) ────────────
# Set this to your ngrok URL or production domain so Instagram can fetch images.
# Example: MEDIA_PUBLIC_BASE = 'https://abc123.ngrok-free.app'
# In production, set to your real domain: 'https://yourdomain.com'
MEDIA_PUBLIC_BASE = os.environ.get('MEDIA_PUBLIC_BASE', '').strip().rstrip('/')

# Validate and print debug
if MEDIA_PUBLIC_BASE:
    if not MEDIA_PUBLIC_BASE.startswith('https://'):
        print(f"[WARNING] MEDIA_PUBLIC_BASE should start with https://, got: {MEDIA_PUBLIC_BASE}", file=sys.stderr)
    print(f"[DEBUG] MEDIA_PUBLIC_BASE = {MEDIA_PUBLIC_BASE}", file=sys.stderr)
else:
    print("[WARNING] MEDIA_PUBLIC_BASE is NOT SET in .env! Instagram image posts will fail.", file=sys.stderr)
    print("[WARNING] Add this to .env: MEDIA_PUBLIC_BASE=https://your-ngrok-url.ngrok-free.app", file=sys.stderr)

# ─── Cloudinary (for Instagram image uploads) ────────────────────
CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME', '')
CLOUDINARY_API_KEY    = os.environ.get('CLOUDINARY_API_KEY', '')
CLOUDINARY_API_SECRET = os.environ.get('CLOUDINARY_API_SECRET', '')

# ─── LinkedIn OAuth 2.0 ─────────────────────────────────────────
LINKEDIN_CLIENT_ID     = os.environ.get('LINKEDIN_CLIENT_ID', '')
LINKEDIN_CLIENT_SECRET = os.environ.get('LINKEDIN_CLIENT_SECRET', '')
LINKEDIN_REDIRECT_URI  = os.environ.get('LINKEDIN_REDIRECT_URI', 'http://127.0.0.1:8000/linkedin/callback')


# ─── Auth ─────────────────────────────────────────────────────────
AUTH_USER_MODEL = 'auth.User'
LOGIN_URL = '/auth/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

# django-allauth Configuration
SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_EMAIL_VERIFICATION = 'none'
ACCOUNT_DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

SOCIALACCOUNT_ADAPTER = 'core.adapters.CustomSocialAccountAdapter'
SOCIALACCOUNT_QUERY_EMAIL = True
SOCIALACCOUNT_STORE_TOKENS = False
SOCIALACCOUNT_EMAIL_VERIFICATION = 'none'

LOGIN_ERROR_URL = '/auth/login/'

# Override default allauth login redirect
ACCOUNT_LOGIN_REDIRECT_URL = '/dashboard/'
SOCIALACCOUNT_LOGIN_REDIRECT_URL = '/dashboard/'

# ─── Session ──────────────────────────────────────────────────────
SESSION_COOKIE_AGE = 3600  # 1 hour
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
else:
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False

# ─── Security (Production) ─────────────────────────────────────────
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CSP_CONNECT_SRC = ("'self'", "https://*.ngrok-free.dev", "https://*.ngrok.io")
else:
    SECURE_SSL_REDIRECT = False
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False

# ─── Proxy Headers (for ngrok) ──────────────────────────────────
# ngrok terminates HTTPS and forwards to Django as HTTP.
# These settings let Django see the original HTTPS scheme and host.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'America/New_York'
USE_I18N = True
USE_TZ = True

DATA_UPLOAD_MAX_NUMBER_FIELDS = 20000

# ─── LOGGING ──────────────────────────────────────────────────────
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'filters': {
        'require_debug_false': {
            '()': 'django.utils.log.RequireDebugFalse',
        },
        'require_debug_true': {
            '()': 'django.utils.log.RequireDebugTrue',
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': BASE_DIR / 'logs' / 'florida_events.log',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'core': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': True,
        },
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': True,
        },
    },
}

# Ensure logs directory exists
os.makedirs(BASE_DIR / 'logs', exist_ok=True)
