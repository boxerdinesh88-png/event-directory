"""
Free OTP Service — Event Directory and Logistic
Supports multiple free providers, zero paid subscriptions required.

Priority order:
  1. Console (development — prints to terminal, always works)
  2. Brevo (Sendinblue) — 300 free emails/day  → https://app.brevo.com
  3. Mailgun   — 100 free emails/day           → https://mailgun.com
  4. Abstract API — OTP-specific free tier      → https://abstractapi.com
  5. Resend    — 3,000 free emails/month        → https://resend.com
  6. Django SMTP fallback (any configured SMTP)
"""

import random
import string
import logging
import urllib.request
import urllib.parse
import json
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# OTP GENERATION
# ─────────────────────────────────────────────────────────────────

def generate_otp(length=6):
    """Generate a secure numeric OTP."""
    return ''.join(random.choices(string.digits, k=length))


# ─────────────────────────────────────────────────────────────────
# MASTER SEND FUNCTION  (call this from views)
# ─────────────────────────────────────────────────────────────────

def send_otp(email: str, otp_code: str, purpose: str = 'verify') -> dict:
    """
    Send OTP via best available free provider.
    Returns: {'success': True/False, 'provider': str, 'error': str}
    """
    provider = getattr(settings, 'OTP_PROVIDER', 'console').lower()

    providers = {
        'console':  _send_console,
        'brevo':    _send_brevo,
        'mailgun':  _send_mailgun,
        'abstract': _send_abstract_api,
        'resend':   _send_resend,
        'smtp':     _send_django_smtp,
    }

    fn = providers.get(provider, _send_console)
    fallback_to_console = getattr(settings, 'OTP_FALLBACK_TO_CONSOLE', False)
    try:
        result = fn(email, otp_code, purpose)
        if result.get('success'):
            logger.info(f"OTP sent via {provider} to {email}")
        else:
            logger.warning(f"OTP send failed via {provider}: {result.get('error')}")
            # Optional fallback for development only.
            if fallback_to_console and provider != 'console':
                logger.info("Falling back to console OTP")
                result = _send_console(email, otp_code, purpose)
        return result
    except Exception as e:
        logger.error(f"OTP error: {e}")
        if fallback_to_console and provider != 'console':
            return _send_console(email, otp_code, purpose)
        return {'success': False, 'provider': provider, 'error': str(e)}


# ─────────────────────────────────────────────────────────────────
# PROVIDER 1 — CONSOLE  (zero config, works in dev immediately)
# ─────────────────────────────────────────────────────────────────

def _send_console(email, otp, purpose):
    """Print OTP to terminal. Perfect for development."""
    purpose_label = {'login': 'Sign In', 'register': 'Registration', 'reset': 'Password Reset'}.get(purpose, 'Verification')
    print(f"""
{'='*60}
        🌴  EVENT DIRECTORY AND LOGISTIC - VERIFICATION CODE
{'='*60}
        
        Purpose: {purpose_label}
        To:      {email}
        
        ┌────────────────────────────────────┐
        │                                    │
        │          {otp}                      │
        │                                    │
        │      Your Verification Code       │
        │                                    │
        └────────────────────────────────────┘
        
        ⏱️  Expires in 10 minutes
        
        If this wasn't you, ignore this email.
        
{'='*60}
""")
    return {'success': True, 'provider': 'console'}


# ─────────────────────────────────────────────────────────────────
# PROVIDER 2 — BREVO (Sendinblue)
# Free tier: 300 emails/day, no credit card
# Sign up: https://app.brevo.com → API Keys → Create
# settings.py:  OTP_PROVIDER = 'brevo'
#               BREVO_API_KEY = 'xkeysib-...'
#               BREVO_SENDER_EMAIL = 'noreply@yourdomain.com'
#               BREVO_SENDER_NAME = 'Event Directory and Logistic'
# ─────────────────────────────────────────────────────────────────

def _send_brevo(email, otp, purpose):
    api_key = getattr(settings, 'BREVO_API_KEY', '')
    if not api_key:
        return {'success': False, 'error': 'BREVO_API_KEY not set'}

    sender_email = getattr(settings, 'BREVO_SENDER_EMAIL', 'noreply@floridaevents.com')
    sender_name  = getattr(settings, 'BREVO_SENDER_NAME', 'Event Directory and Logistic')

    subject, body_html, body_text = _build_email_content(email, otp, purpose)

    payload = json.dumps({
        "sender": {"name": sender_name, "email": sender_email},
        "to": [{"email": email}],
        "subject": subject,
        "htmlContent": body_html,
        "textContent": body_text,
    }).encode()

    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=payload,
        headers={
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 201):
                return {'success': True, 'provider': 'brevo'}
            return {'success': False, 'error': f'HTTP {resp.status}'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ─────────────────────────────────────────────────────────────────
# PROVIDER 3 — MAILGUN
# Free tier: 100 emails/day (Flex plan)
# Sign up: https://mailgun.com → API Keys
# settings.py:  OTP_PROVIDER = 'mailgun'
#               MAILGUN_API_KEY = 'key-...'
#               MAILGUN_DOMAIN = 'mg.yourdomain.com'
#               MAILGUN_SENDER = 'Event Directory and Logistic <noreply@mg.yourdomain.com>'
# ─────────────────────────────────────────────────────────────────

def _send_mailgun(email, otp, purpose):
    api_key = getattr(settings, 'MAILGUN_API_KEY', '')
    domain  = getattr(settings, 'MAILGUN_DOMAIN', '')
    if not api_key or not domain:
        return {'success': False, 'error': 'MAILGUN_API_KEY or MAILGUN_DOMAIN not set'}

    sender  = getattr(settings, 'MAILGUN_SENDER', f'Event Directory and Logistic <noreply@{domain}>')
    subject, _, body_text = _build_email_content(email, otp, purpose)

    data = urllib.parse.urlencode({
        'from': sender,
        'to': email,
        'subject': subject,
        'text': body_text,
    }).encode()

    import base64
    creds = base64.b64encode(f'api:{api_key}'.encode()).decode()
    req = urllib.request.Request(
        f'https://api.mailgun.net/v3/{domain}/messages',
        data=data,
        headers={'Authorization': f'Basic {creds}'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {'success': resp.status == 200, 'provider': 'mailgun'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ─────────────────────────────────────────────────────────────────
# PROVIDER 4 — ABSTRACT API  (OTP-specific)
# Free tier: 500 OTPs/month
# Sign up: https://www.abstractapi.com/api/phone-validation  →  OTP section
# settings.py:  OTP_PROVIDER = 'abstract'
#               ABSTRACT_OTP_API_KEY = 'your-key'
# Note: Abstract sends the OTP itself — you don't build the email.
# ─────────────────────────────────────────────────────────────────

def _send_abstract_api(email, otp, purpose):
    api_key = getattr(settings, 'ABSTRACT_OTP_API_KEY', '')
    if not api_key:
        return {'success': False, 'error': 'ABSTRACT_OTP_API_KEY not set'}

    # Abstract OTP API sends the OTP on your behalf
    params = urllib.parse.urlencode({
        'api_key': api_key,
        'email': email,
        'otp_length': 6,
    })
    req = urllib.request.Request(
        f'https://emailvalidation.abstractapi.com/v1/otp/send?{params}',
        method='GET',
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
            if body.get('success'):
                return {'success': True, 'provider': 'abstract', 'abstract_otp': body.get('otp')}
            return {'success': False, 'error': body.get('error', 'Unknown')}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ─────────────────────────────────────────────────────────────────
# PROVIDER 5 — RESEND
# Free tier: 3,000 emails/month, 100/day
# Sign up: https://resend.com → API Keys
# settings.py:  OTP_PROVIDER = 'resend'
#               RESEND_API_KEY = 're_...'
#               RESEND_FROM = 'Event Directory and Logistic <onboarding@resend.dev>'
# ─────────────────────────────────────────────────────────────────

def _send_resend(email, otp, purpose):
    api_key = getattr(settings, 'RESEND_API_KEY', '')
    if not api_key:
        return {'success': False, 'error': 'RESEND_API_KEY not set'}

    from_addr = getattr(settings, 'RESEND_FROM', 'Event Directory and Logistic <onboarding@resend.dev>')
    subject, body_html, body_text = _build_email_content(email, otp, purpose)

    payload = json.dumps({
        "from": from_addr,
        "to": [email],
        "subject": subject,
        "html": body_html,
        "text": body_text,
    }).encode()

    req = urllib.request.Request(
        'https://api.resend.com/emails',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {'success': resp.status in (200, 201), 'provider': 'resend'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ─────────────────────────────────────────────────────────────────
# PROVIDER 6 — Django SMTP fallback
# Uses whatever EMAIL_* settings are configured in settings.py
# ─────────────────────────────────────────────────────────────────

def _send_django_smtp(email, otp, purpose):
    from django.core.mail import send_mail
    subject, _, body_text = _build_email_content(email, otp, purpose)
    try:
        send_mail(subject, body_text, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
        return {'success': True, 'provider': 'smtp'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ─────────────────────────────────────────────────────────────────
# EMAIL CONTENT BUILDER
# ─────────────────────────────────────────────────────────────────

def _build_email_content(email, otp, purpose):
    purpose_label = {'login': 'Sign In', 'register': 'Account Verification', 'reset': 'Password Reset'}.get(purpose, 'Verification')
    
    purpose_heading = {
        'login': 'Sign In Verification',
        'register': 'Verify Your Account', 
        'reset': 'Password Reset Request'
    }.get(purpose, 'Account Verification')
    
    purpose_description = {
        'login': 'sign in to your account',
        'register': 'complete your registration',
        'reset': 'reset your password'
    }.get(purpose, 'verify your account')

    subject = f"Event Directory and Logistic - Your Verification Code"

    body_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background-color:#f4f4f4;font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color:#f4f4f4;">
        <tr>
            <td align="center" style="padding:50px 20px;">
                <table role="presentation" width="560" cellspacing="0" cellpadding="0" style="background-color:#ffffff;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,0.1);overflow:hidden;">
                    <!-- Header -->
                    <tr>
                        <td style="background:linear-gradient(135deg,#f97316 0%,#ea580c 100%);padding:48px 40px;text-align:center;">
                            <table role="presentation" cellspacing="0" cellpadding="0" style="margin:0 auto;">
                                <tr>
                                    <td style="padding-right:16px;">
                                        <div style="width:48px;height:48px;background:rgba(255,255,255,0.2);border-radius:12px;display:inline-block;line-height:48px;font-size:24px;">🌴</div>
                                    </td>
                                    <td style="text-align:left;">
                                        <h1 style="margin:0;color:#ffffff;font-size:28px;font-weight:700;letter-spacing:-0.5px;">Event Directory and Logistic</h1>
                                        <p style="margin:4px 0 0 0;color:rgba(255,255,255,0.9);font-size:14px;">& Matchmaking Services</p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Content -->
                    <tr>
                        <td style="padding:48px 40px;">
                            <h2 style="margin:0 0 24px 0;color:#111827;font-size:24px;font-weight:600;">{purpose_heading}</h2>
                            
                            <p style="margin:0 0 16px 0;color:#374151;font-size:16px;line-height:1.6;">
                                Hello,
                            </p>
                            
                            <p style="margin:0 0 24px 0;color:#374151;font-size:16px;line-height:1.6;">
                                We received a request to {purpose_description}. Use the verification code below to continue:
                            </p>
                            
                            <!-- OTP Code Box -->
                            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:32px 0;">
                                <tr>
                                    <td style="background:linear-gradient(135deg,#fef3c7 0%,#fde68a 100%);border-radius:12px;padding:32px;text-align:center;border:1px solid #fcd34d;">
                                        <div style="letter-spacing:16px;font-size:40px;font-weight:800;color:#92400e;font-family:'Courier New',Courier,monospace;">{otp}</div>
                                        <div style="margin-top:12px;color:#b45309;font-size:13px;font-weight:500;">Your Verification Code</div>
                                    </td>
                                </tr>
                            </table>
                            
                            <!-- Info Box -->
                            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:24px 0;">
                                <tr>
                                    <td style="background:#f0fdf4;border-radius:8px;padding:16px 20px;border-left:4px solid #22c55e;">
                                        <p style="margin:0;color:#166534;font-size:14px;line-height:1.5;">
                                            <strong>⏱️ Expires in 10 minutes</strong><br>
                                            This code will expire soon for your security.
                                        </p>
                                    </td>
                                </tr>
                            </table>
                            
                            <!-- Security Warning -->
                            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:24px 0;">
                                <tr>
                                    <td style="background:#fef2f2;border-radius:8px;padding:16px 20px;border-left:4px solid #ef4444;">
                                        <p style="margin:0;color:#991b1b;font-size:14px;line-height:1.5;">
                                            <strong>🔒 Keep this code secure</strong><br>
                                            Never share your verification code with anyone. Our team will never ask for it.
                                        </p>
                                    </td>
                                </tr>
                            </table>
                            
                            <p style="margin:32px 0 0 0;color:#6b7280;font-size:14px;line-height:1.6;">
                                If you didn't request this code, you can safely ignore this email. Your account security is protected.
                            </p>
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="background:#f9fafb;padding:32px 40px;border-top:1px solid #e5e7eb;">
                            <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                                <tr>
                                    <td style="text-align:center;">
                                        <p style="margin:0 0 8px 0;color:#374151;font-size:14px;font-weight:600;">Event Directory and Logistic & Matchmaking Services</p>
                                        <p style="margin:0 0 16px 0;color:#6b7280;font-size:12px;">Serving all of Florida</p>
                                        <p style="margin:0;color:#9ca3af;font-size:11px;">
                                            Miami · Orlando · Tampa · Jacksonville · Fort Lauderdale
                                        </p>
                                    </td>
                                </tr>
                                <tr>
                                    <td style="text-align:center;padding-top:24px;">
                                        <p style="margin:0;color:#d1d5db;font-size:11px;">
                                            © 2025 Event Directory and Logistic. All rights reserved.
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

    body_text = f"""EVENT DIRECTORY AND LOGISTIC - {purpose_label.upper()}
{'='*50}

Hello,

We received a request to {purpose_description}. Use the verification code below:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    VERIFICATION CODE: {otp}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

This code expires in 10 minutes.

If you didn't request this code, you can safely ignore this email.

Stay Secure,
Event Directory and Logistic Team

---
Event Directory and Logistic & Matchmaking Services
Serving all of Florida: Miami · Orlando · Tampa · Jacksonville
"""

    return subject, body_html, body_text
