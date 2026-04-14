import json, csv, io
import logging
import hashlib
import secrets
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
import re
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.core.cache import cache
from .otp_service import send_otp as otp_send, generate_otp
from django.conf import settings
from django.utils import timezone
from django.db.models import Q, Count, Sum
from django.db import transaction
from .models import (Location, Contact, EmailBlast, EmailAttachment, OTPCode, ImportLog,
                     ScheduledEmail, EmailVerificationToken, LoginAttempt, UserProfile)
from .forms import (LocationForm, ContactForm, EmailBlastForm,
                     RegisterForm, LoginForm, OTPVerifyForm,
                      PasswordResetRequestForm, PasswordResetConfirmForm, ImportForm)
logger = logging.getLogger(__name__)

pd = None

def _ensure_pandas():
    global pd
    if pd is None:
        try:
            import pandas as _pd
            pd = _pd
        except ImportError:
            pd = None
    return pd

def _get_pandas():
    return _ensure_pandas()


# ══════════════════════════════════════════════════════════════════
# ROOT REDIRECT
# ══════════════════════════════════════════════════════════════════

def root_redirect(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return redirect('login')


def custom_404(request, exception=None):
    return render(request, '404.html', status=404)


def error_404_preview(request):
    # Helpful for testing custom 404 while DEBUG=True.
    return render(request, '404.html', status=404)


def help(request):
    return render(request, 'admin_dash/help.html')


def service_worker(request):
    """
    Serve a root-scoped service worker that returns the cached /404/ page
    when navigation requests fail due to network loss.
    """
    js = """
const CACHE_NAME = 'Event-Directory-and-Logistic-offline-v1';
const OFFLINE_URL = '/404/';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.map((key) => caches.delete(key))
    )).then(() => {
      self.registration.unregister();
    })
  );
});

self.addEventListener('fetch', (event) => {
  // Pass through all requests, no longer caching.
});
""".strip()
    response = HttpResponse(js, content_type='application/javascript')
    response['Cache-Control'] = 'no-cache'
    return response


# ══════════════════════════════════════════════════════════════════
# AUTHENTICATION — Production-Ready
# ══════════════════════════════════════════════════════════════════

def _get_client_ip(request):
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


def _is_rate_limited(ip_address, email='', window_minutes=15, max_attempts=5):
    ip_key = f'rate_limit:ip:{ip_address}'
    email_key = f'rate_limit:email:{hashlib.md5(email.encode()).hexdigest()}' if email else None
    ip_count = cache.get(ip_key, 0)
    if ip_count >= max_attempts:
        return True, f'Too many attempts. Try again in {window_minutes} minutes.'
    if email_key:
        email_count = cache.get(email_key, 0)
        if email_count >= max_attempts:
            return True, f'Too many attempts for this email. Try again in {window_minutes} minutes.'
    return False, ''


def _record_failed_attempt(ip_address, email='', window_minutes=15):
    ip_key = f'rate_limit:ip:{ip_address}'
    email_key = f'rate_limit:email:{hashlib.md5(email.encode()).hexdigest()}' if email else None
    timeout = window_minutes * 60
    try:
        cache.incr(ip_key)
    except (ValueError, AttributeError):
        cache.set(ip_key, 1, timeout)
    if email_key:
        try:
            cache.incr(email_key)
        except (ValueError, AttributeError):
            cache.set(email_key, 1, timeout)


def _clear_rate_limit(ip_address, email=''):
    ip_key = f'rate_limit:ip:{ip_address}'
    email_key = f'rate_limit:email:{hashlib.md5(email.encode()).hexdigest()}' if email else None
    cache.delete(ip_key)
    if email_key:
        cache.delete(email_key)


def _send_verification_email(user, request):
    token_str = EmailVerificationToken.generate()
    EmailVerificationToken.objects.filter(user=user).delete()
    EmailVerificationToken.objects.create(user=user, token=token_str)
    protocol = 'https' if not settings.DEBUG else 'http'
    host = request.get_host()
    verify_url = f'{protocol}://{host}/auth/verify-email/?token={token_str}'
    subject = 'Event Directory and Logistic — Verify Your Email'
    body_html = f'<div style="font-family:Inter,sans-serif;max-width:560px;margin:0 auto;padding:40px 20px;"><div style="background:linear-gradient(135deg,#f97316,#ea580c);padding:32px;border-radius:16px 16px 0 0;text-align:center;"><h1 style="color:#fff;margin:0;font-size:24px;">Event Directory and Logistic</h1><p style="color:rgba(255,255,255,.85);margin:8px 0 0;font-size:14px;">Verify Your Email</p></div><div style="background:#fff;padding:32px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 16px;"><p style="color:#374151;font-size:16px;">Hello {user.first_name},</p><p style="color:#374151;font-size:16px;">Click below to verify your email:</p><div style="text-align:center;margin:32px 0;"><a href="{verify_url}" style="background:linear-gradient(135deg,#f5a623,#c8851b);color:#000;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:700;">Verify Email</a></div><p style="color:#6b7280;font-size:13px;">This link expires in 15 minutes.</p></div></div>'
    body_text = f'Event Directory and Logistic — Verify your email:\n{verify_url}'
    try:
        msg = EmailMultiAlternatives(subject, body_text, settings.DEFAULT_FROM_EMAIL, [user.email])
        msg.attach_alternative(body_html, 'text/html')
        msg.send(fail_silently=False)
        return True
    except Exception as e:
        logger.error(f"Verification email failed: {e}")
        return False


def _send_password_reset_email(user, request):
    token_str = secrets.token_urlsafe(48)
    cache.set(f'pwd_reset:{token_str}', user.id, 1800)
    protocol = 'https' if not settings.DEBUG else 'http'
    host = request.get_host()
    reset_url = f'{protocol}://{host}/auth/reset-password/?token={token_str}'
    subject = 'Event Directory and Logistic — Password Reset'
    body_html = f'<div style="font-family:Inter,sans-serif;max-width:560px;margin:0 auto;padding:40px 20px;"><div style="background:linear-gradient(135deg,#ef4444,#dc2626);padding:32px;border-radius:16px 16px 0 0;text-align:center;"><h1 style="color:#fff;margin:0;font-size:24px;">Password Reset</h1></div><div style="background:#fff;padding:32px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 16px;"><p style="color:#374151;font-size:16px;">Hello {user.first_name},</p><p style="color:#374151;font-size:16px;">Click below to reset your password:</p><div style="text-align:center;margin:32px 0;"><a href="{reset_url}" style="background:linear-gradient(135deg,#ef4444,#dc2626);color:#fff;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:700;">Reset Password</a></div><p style="color:#6b7280;font-size:13px;">This link expires in 30 minutes.</p></div></div>'
    body_text = f'Event Directory and Logistic — Reset your password:\n{reset_url}'
    try:
        msg = EmailMultiAlternatives(subject, body_text, settings.DEFAULT_FROM_EMAIL, [user.email])
        msg.attach_alternative(body_html, 'text/html')
        msg.send(fail_silently=False)
        return True
    except Exception as e:
        logger.error(f"Password reset email failed: {e}")
        return False


def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = RegisterForm()
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            email = d['email']
            ip = _get_client_ip(request)
            limited, msg = _is_rate_limited(ip, email, max_attempts=10)
            if limited:
                messages.error(request, msg)
                return render(request, 'auth/register.html', {'form': form})
            user = User.objects.create_user(
                username=email, email=email,
                first_name=d['first_name'], last_name=d['last_name'],
                password=d['password'], is_active=False,
            )
            UserProfile.objects.get_or_create(user=user, defaults={'is_2fa_enabled': True})
            if _send_verification_email(user, request):
                messages.success(request, 'Account created! Check your email to verify.')
            else:
                messages.warning(request, 'Account created but verification email failed. Try logging in to resend.')
            return redirect('login')
    return render(request, 'auth/register.html', {'form': form})


def verify_email_view(request):
    token_str = request.GET.get('token', '').strip()
    if not token_str:
        messages.error(request, 'Invalid verification link.')
        return redirect('login')
    try:
        token = EmailVerificationToken.objects.select_related('user').get(token=token_str)
    except EmailVerificationToken.DoesNotExist:
        messages.error(request, 'Invalid or expired verification link.')
        return redirect('login')
    if not token.is_valid():
        messages.error(request, 'This verification link has expired.')
        return redirect('login')
    user = token.user
    user.is_active = True
    user.save(update_fields=['is_active'])
    token.is_used = True
    token.save(update_fields=['is_used'])
    messages.success(request, 'Email verified! You can now log in.')
    return redirect('login')


def login_view(request):
    try:
        if request.user.is_authenticated:
            return redirect('dashboard')
        form = LoginForm()
        if request.method == 'POST':
            form = LoginForm(request.POST)
            try:
                if form.is_valid():
                    email = form.cleaned_data['email']
                    password = form.cleaned_data['password']
                    ip = _get_client_ip(request)
                    limited, msg = _is_rate_limited(ip, email, window_minutes=15, max_attempts=5)
                    if limited:
                        messages.error(request, msg)
                        return render(request, 'auth/login.html', {'form': form})
                    try:
                        user_obj = User.objects.get(email__iexact=email)
                    except User.DoesNotExist:
                        _record_failed_attempt(ip, email)
                        messages.error(request, 'Invalid email or password.')
                        return render(request, 'auth/login.html', {'form': form})
                    user = authenticate(request, username=user_obj.username, password=password)
                    if not user:
                        _record_failed_attempt(ip, email)
                        messages.error(request, 'Invalid email or password.')
                        return render(request, 'auth/login.html', {'form': form})
                    if not user.is_active:
                        _send_verification_email(user, request)
                        messages.error(request, 'Account not verified. A new verification email has been sent.')
                        return render(request, 'auth/login.html', {'form': form})
                    profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'is_2fa_enabled': True})
                    if profile.is_2fa_enabled or profile.is_totp_enabled:
                        request.session['pending_2fa_user_id'] = user.id
                        request.session['pending_2fa_email'] = user.email
                        if profile.is_totp_enabled:
                            return redirect('verify_totp')
                        result = _send_otp_email(user.email, purpose='login', user=user, request=request)
                        if result.get('success'):
                            return redirect(f'/auth/verify-otp/?purpose=login&email={user.email}')
                        else:
                            messages.error(request, 'Failed to send verification code.')
                            return render(request, 'auth/login.html', {'form': form})
                    _clear_rate_limit(ip, email)
                    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                    return redirect('dashboard')
            except Exception as e:
                logger.error(f"login_view POST error: {e}")
                messages.error(request, 'An error occurred. Please try again.')
        return render(request, 'auth/login.html', {'form': form})
    except Exception as e:
        logger.error(f"login_view error: {e}")
        messages.error(request, 'An error occurred. Please try again.')
        return render(request, 'auth/login.html', {'form': LoginForm()})


def send_otp(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST only'})
    try:
        email = request.POST.get('email', '').strip().lower()
        purpose = request.POST.get('purpose', 'login')
        if not email:
            return JsonResponse({'success': False, 'error': 'Email required'})
        ip = _get_client_ip(request)
        limited, msg = _is_rate_limited(ip, email, window_minutes=5, max_attempts=3)
        if limited:
            return JsonResponse({'success': False, 'error': msg}, status=429)
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            if purpose == 'login':
                return JsonResponse({'success': False, 'error': 'No account found with this email address.'})
            user = None
        result = _send_otp_email(email, purpose, user=user, request=request)
        _record_failed_attempt(ip, email, window_minutes=5)
        if result.get('success'):
            if user and purpose == 'login':
                request.session['pending_2fa_user_id'] = user.id
                request.session['pending_2fa_email'] = email
            return JsonResponse({'success': True, 'message': f'OTP sent to {email}'})
        return JsonResponse({'success': False, 'error': result.get('error', 'Failed')}, status=400)
    except Exception as e:
        logger.error(f"send_otp error: {e}")
        return JsonResponse({'success': False, 'error': 'Unable to send verification code. Please try again.'}, status=500)


def verify_otp(request):
    try:
        purpose = request.GET.get('purpose', 'login')
        email = request.GET.get('email', '').strip().lower()
        if request.method == 'POST':
            try:
                code = request.POST.get('otp_code', '').strip()
                email = request.POST.get('email', '').strip().lower()
                purpose = request.POST.get('purpose', 'login')
                ip = _get_client_ip(request)
                limited, msg = _is_rate_limited(ip, email, window_minutes=5, max_attempts=10)
                if limited:
                    messages.error(request, msg)
                    return render(request, 'auth/verify_otp.html', {'purpose': purpose, 'email': email})
                otp = OTPCode.objects.filter(email=email, purpose=purpose, is_used=False).order_by('-created_at').first()
                if not otp:
                    messages.error(request, 'No verification code found. Request a new one.')
                    return render(request, 'auth/verify_otp.html', {'purpose': purpose, 'email': email})
                if otp.attempts >= otp.max_attempts:
                    messages.error(request, 'Too many incorrect attempts. Request a new code.')
                    return render(request, 'auth/verify_otp.html', {'purpose': purpose, 'email': email})
                if otp.is_expired():
                    messages.error(request, 'Code has expired. Request a new one.')
                    return render(request, 'auth/verify_otp.html', {'purpose': purpose, 'email': email})
                if not otp.check_code(code):
                    otp.increment_attempts()
                    remaining = otp.max_attempts - otp.attempts
                    _record_failed_attempt(ip, email, window_minutes=5)
                    messages.error(request, f'Incorrect code. {remaining} attempts remaining.')
                    return render(request, 'auth/verify_otp.html', {'purpose': purpose, 'email': email})
                otp.is_used = True
                otp.save(update_fields=['is_used'])
                if purpose == 'register':
                    pending = request.session.get('pending_register')
                    if pending and pending.get('email', '').strip().lower() == email:
                        user = User.objects.create_user(
                            username=email, email=email,
                            first_name=pending['first_name'], last_name=pending['last_name'],
                            password=pending['password'], is_active=True,
                        )
                        UserProfile.objects.get_or_create(user=user, defaults={'is_2fa_enabled': True})
                        del request.session['pending_register']
                        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                        messages.success(request, f"Welcome, {user.first_name}! Account created.")
                        return redirect('dashboard')
                elif purpose == 'login':
                    user_id = request.session.get('pending_2fa_user_id')
                    if user_id:
                        try:
                            user = User.objects.get(id=user_id)
                            _clear_rate_limit(ip, email)
                            request.session.pop('pending_2fa_user_id', None)
                            request.session.pop('pending_2fa_email', None)
                            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                            return redirect('dashboard')
                        except User.DoesNotExist:
                            messages.error(request, 'Account not found. Please register first.')
                            return redirect('register')
                    messages.error(request, 'Session expired. Please request a new code.')
                elif purpose == 'reset':
                    return redirect(f'/auth/reset-password/?email={email}&verified=1')
            except Exception as e:
                logger.error(f"verify_otp POST error: {e}")
                messages.error(request, 'Verification failed. Please try again.')
        return render(request, 'auth/verify_otp.html', {'purpose': purpose, 'email': email})
    except Exception as e:
        logger.error(f"verify_otp error: {e}")
        messages.error(request, 'Verification failed. Please try again.')
        return redirect('login')


def verify_totp(request):
    user_id = request.session.get('pending_2fa_user_id')
    if not user_id:
        return redirect('login')
    if request.method == 'POST':
        code = request.POST.get('totp_code', '').strip()
        ip = _get_client_ip(request)
        limited, msg = _is_rate_limited(ip, '', window_minutes=5, max_attempts=5)
        if limited:
            messages.error(request, msg)
            return render(request, 'auth/verify_totp.html')
        try:
            import pyotp
            user = User.objects.get(id=user_id)
            profile = user.profile
            totp = pyotp.TOTP(profile.totp_secret)
            if totp.verify(code, valid_window=1):
                del request.session['pending_2fa_user_id']
                request.session.pop('pending_2fa_email', '')
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                return redirect('dashboard')
            else:
                _record_failed_attempt(ip, '', window_minutes=5)
                messages.error(request, 'Invalid code. Try again.')
        except Exception as e:
            logger.error(f"TOTP verify error: {e}")
            messages.error(request, 'Verification failed.')
    return render(request, 'auth/verify_totp.html')


def password_reset_request(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = PasswordResetRequestForm()
    if request.method == 'POST':
        form = PasswordResetRequestForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            ip = _get_client_ip(request)
            limited, msg = _is_rate_limited(ip, email, window_minutes=15, max_attempts=3)
            if limited:
                messages.error(request, msg)
                return render(request, 'auth/password_reset_request.html', {'form': form})
            _record_failed_attempt(ip, email, window_minutes=15)
            try:
                user = User.objects.get(email__iexact=email)
                if _send_password_reset_email(user, request):
                    messages.success(request, 'Password reset link sent to your email.')
                else:
                    messages.error(request, 'Failed to send reset email.')
            except User.DoesNotExist:
                messages.success(request, 'If an account exists with this email, a reset link has been sent.')
            return redirect('login')
    return render(request, 'auth/password_reset_request.html', {'form': form})


def password_reset_confirm(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    token_str = request.GET.get('token', '').strip()
    email = request.GET.get('email', '').strip().lower()
    verified = request.GET.get('verified', '')
    if email and verified == '1':
        if request.method == 'POST':
            form = PasswordResetConfirmForm(request.POST)
            if form.is_valid():
                try:
                    user = User.objects.get(email__iexact=email)
                    user.set_password(form.cleaned_data['new_password'])
                    user.save()
                    messages.success(request, 'Password reset successful! You can now log in.')
                    return redirect('login')
                except User.DoesNotExist:
                    messages.error(request, 'User not found.')
        else:
            form = PasswordResetConfirmForm()
        return render(request, 'auth/password_reset_confirm.html', {'form': form, 'email': email})
    if not token_str:
        messages.error(request, 'Invalid reset link.')
        return redirect('login')
    user_id = cache.get(f'pwd_reset:{token_str}')
    if not user_id:
        messages.error(request, 'Reset link has expired or is invalid.')
        return redirect('login')
    if request.method == 'POST':
        form = PasswordResetConfirmForm(request.POST)
        if form.is_valid():
            try:
                user = User.objects.get(id=user_id)
                user.set_password(form.cleaned_data['new_password'])
                user.save()
                cache.delete(f'pwd_reset:{token_str}')
                messages.success(request, 'Password reset successful! You can now log in.')
                return redirect('login')
            except User.DoesNotExist:
                messages.error(request, 'User not found.')
    else:
        form = PasswordResetConfirmForm()
    return render(request, 'auth/password_reset_confirm.html', {'form': form})


@login_required
def totp_setup(request):
    import pyotp
    profile, _ = UserProfile.objects.get_or_create(user=request.user, defaults={'is_2fa_enabled': True})
    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'enable':
            code = request.POST.get('totp_code', '').strip()
            secret = request.session.get('pending_totp_secret', '')
            if secret:
                totp = pyotp.TOTP(secret)
                if totp.verify(code, valid_window=1):
                    profile.totp_secret = secret
                    profile.is_totp_enabled = True
                    profile.save(update_fields=['totp_secret', 'is_totp_enabled'])
                    del request.session['pending_totp_secret']
                    messages.success(request, 'Google Authenticator enabled!')
                else:
                    messages.error(request, 'Invalid code. Try again.')
            return redirect('totp_setup')
        elif action == 'disable':
            profile.is_totp_enabled = False
            profile.totp_secret = ''
            profile.save(update_fields=['is_totp_enabled', 'totp_secret'])
            messages.success(request, 'Google Authenticator disabled.')
            return redirect('totp_setup')
    secret = request.session.get('pending_totp_secret')
    if not secret:
        secret = pyotp.random_base32()
        request.session['pending_totp_secret'] = secret
    totp = pyotp.TOTP(secret)
    issuer = getattr(settings, 'TOTP_ISSUER_NAME', 'Event Directory and Logistic')
    provisioning_uri = totp.provisioning_uri(name=request.user.email, issuer_name=issuer)
    return render(request, 'auth/totp_setup.html', {
        'profile': profile, 'secret': secret, 'qr_uri': provisioning_uri,
    })


def logout_view(request):
    logout(request)
    request.session.flush()
    return redirect('login')


def _send_otp_email(email, purpose='login', user=None, request=None):
    email = email.strip().lower()
    code = generate_otp()
    OTPCode.objects.filter(email=email, purpose=purpose, is_used=False).update(is_used=True)
    ip = _get_client_ip(request) if request else None
    otp = OTPCode(email=email, purpose=purpose, user=user, ip_address=ip)
    otp.set_code(code)
    otp.save()
    result = otp_send(email, code, purpose)
    return result


# ══════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════

@login_required
def dashboard(request):
    from .models import EmailTemplate, SMSBlast, SocialPost
    user = request.user
    def get_stats():
        return {
            'locations': Location.objects.filter(Q(created_by=user) | Q(created_by__isnull=True)).count(),
            'active_locations': Location.objects.filter(Q(created_by=user) | Q(created_by__isnull=True), status='active').count(),
            'contacts': Contact.objects.filter(Q(created_by=user) | Q(created_by__isnull=True)).count(),
            'subscribed': Contact.objects.filter(Q(created_by=user) | Q(created_by__isnull=True), is_subscribed=True).count(),
            'email_blasts': EmailBlast.objects.filter(created_by=user).count(),
            'sent_blasts': EmailBlast.objects.filter(created_by=user, status='sent').count(),
            'template_count': EmailTemplate.objects.filter(created_by=user).count(),
            'sms_blasts': SMSBlast.objects.filter(created_by=user, status='sent').count(),
            'social_posts': SocialPost.objects.filter(created_by=user, status='posted').count(),
        }

    stats = get_stats()

    recent_locations = Location.objects.filter(Q(created_by=user) | Q(created_by__isnull=True)).order_by('-created_at')[:5]
    recent_contacts  = Contact.objects.filter(Q(created_by=user) | Q(created_by__isnull=True)).order_by('-created_at')[:5]
    by_city = Location.objects.filter(Q(created_by=user) | Q(created_by__isnull=True)).values('city').annotate(count=Count('id')).order_by('-count')[:8]
    by_type = Location.objects.filter(Q(created_by=user) | Q(created_by__isnull=True)).values('type').annotate(count=Count('id'))

    recent_blasts = EmailBlast.objects.filter(created_by=user)[:3]

    return render(request, 'admin_dash/dashboard.html', {
        'stats': stats,
        'recent_locations': recent_locations,
        'recent_contacts':  recent_contacts,
        'recent_blasts':    recent_blasts,
        'by_city': list(by_city),
        'by_type': list(by_type),
    })


# ══════════════════════════════════════════════════════════════════
# LOCATIONS CRUD
# ══════════════════════════════════════════════════════════════════

@login_required
def location_list(request):
    qs = Location.objects.filter(created_by=request.user)
    q = request.GET.get('q', '')
    ftype = request.GET.get('type', '')
    fstatus = request.GET.get('status', '')
    fcity = request.GET.get('city', '')

    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(city__icontains=q) | Q(contact_name__icontains=q) | Q(email__icontains=q))
    if ftype:
        qs = qs.filter(type=ftype)
    if fstatus:
        qs = qs.filter(status=fstatus)
    if fcity:
        qs = qs.filter(city__icontains=fcity)

    paginator = Paginator(qs, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    cities = Location.objects.filter(created_by=request.user).values_list('city', flat=True).distinct().order_by('city')
    return render(request, 'admin_dash/locations.html', {
        'locations': page_obj,
        'cities': cities,
        'filters': {'q': q, 'type': ftype, 'status': fstatus, 'city': fcity},
        'type_choices': Location.TYPE_CHOICES,
    })


def _split_person_name(full_name):
    name = _strip_contact_suffix(full_name)
    if not name:
        return 'Location', ''
    parts = name.split()
    if len(parts) == 1:
        return parts[0], ''
    return parts[0], ' '.join(parts[1:])


def _strip_contact_suffix(value):
    raw = ' '.join(str(value or '').strip().split())
    if not raw:
        return ''
    return re.sub(r'\bcontact\b$', '', raw, flags=re.IGNORECASE).strip()


def _normalize_phone(value):
    digits = re.sub(r'\D', '', str(value or ''))
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits


def _canonical_contact_name(first_name, last_name):
    full = f"{(first_name or '').strip()} {(last_name or '').strip()}".strip()
    full = _strip_contact_suffix(full)
    return re.sub(r'[^a-z0-9]+', ' ', full.lower()).strip()


def _contact_rank(contact):
    has_email = 1 if (contact.email and '@' in contact.email) else 0
    canonical = _canonical_contact_name(contact.first_name, contact.last_name)
    non_contact_label = 1 if not str(f"{contact.first_name} {contact.last_name}").strip().lower().endswith('contact') else 0
    return has_email, non_contact_label, len(canonical), -contact.id


def _merge_contact_records(primary, duplicate):
    """Merge duplicate into primary. Returns True if duplicate was deleted."""
    primary_email = (primary.email or '').strip().lower()
    dup_email = (duplicate.email or '').strip().lower()
    if primary_email and dup_email and primary_email != dup_email:
        return False

    changed = False
    if not primary.email and dup_email:
        primary.email = dup_email
        changed = True

    fill_fields = ('phone', 'city', 'state', 'zip_code', 'age', 'gender', 'location')
    for field in fill_fields:
        current_val = getattr(primary, field)
        dup_val = getattr(duplicate, field)
        if not current_val and dup_val:
            setattr(primary, field, dup_val)
            changed = True

    if not primary.first_name and duplicate.first_name:
        primary.first_name = _strip_contact_suffix(duplicate.first_name) or duplicate.first_name
        changed = True

    if (not primary.last_name or primary.last_name.lower() == 'contact') and duplicate.last_name:
        primary.last_name = _strip_contact_suffix(duplicate.last_name) or duplicate.last_name
        changed = True

    if duplicate.notes:
        if not primary.notes:
            primary.notes = duplicate.notes
            changed = True
        elif duplicate.notes not in primary.notes:
            primary.notes = f"{primary.notes}\n{duplicate.notes}".strip()
            changed = True

    if duplicate.is_subscribed and not primary.is_subscribed:
        primary.is_subscribed = True
        changed = True

    if changed:
        primary.save()

    duplicate.delete()
    return True


def _dedupe_contacts_for_location(location, user=None):
    """Merge likely duplicates inside a single location."""
    if not location:
        return 0

    merged = 0
    contacts = list(Contact.objects.filter(location=location, created_by=user).order_by('id')) if user else list(Contact.objects.filter(location=location).order_by('id'))
    if len(contacts) < 2:
        return 0

    by_phone = {}
    for c in contacts:
        phone_key = _normalize_phone(c.phone)
        if not phone_key:
            continue
        by_phone.setdefault(phone_key, []).append(c)

    for group in by_phone.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=_contact_rank, reverse=True)
        primary = Contact.objects.get(pk=ordered[0].pk)
        for dup in ordered[1:]:
            if not Contact.objects.filter(pk=dup.pk).exists():
                continue
            duplicate = Contact.objects.get(pk=dup.pk)
            primary = Contact.objects.get(pk=primary.pk)
            if _merge_contact_records(primary, duplicate):
                merged += 1

    contacts = list(Contact.objects.filter(location=location).order_by('id'))
    by_name = {}
    for c in contacts:
        canonical = _canonical_contact_name(c.first_name, c.last_name)
        if canonical:
            by_name.setdefault(canonical, []).append(c)

    for group in by_name.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=_contact_rank, reverse=True)
        primary = Contact.objects.get(pk=ordered[0].pk)
        for dup in ordered[1:]:
            if not Contact.objects.filter(pk=dup.pk).exists():
                continue
            duplicate = Contact.objects.get(pk=dup.pk)
            primary = Contact.objects.get(pk=primary.pk)
            if _normalize_phone(primary.phone) and _normalize_phone(duplicate.phone):
                if _normalize_phone(primary.phone) != _normalize_phone(duplicate.phone):
                    continue
            if _merge_contact_records(primary, duplicate):
                merged += 1

    return merged


def _find_existing_contact(email='', first_name='', last_name='', phone='', location=None, exclude_pk=None, user=None):
    norm_email = (email or '').strip().lower()
    norm_phone = _normalize_phone(phone)
    canonical_name = _canonical_contact_name(first_name, last_name)

    qs = Contact.objects.filter(created_by=user) if user else Contact.objects.all()
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)

    if norm_email and '@' in norm_email:
        existing = qs.filter(email__iexact=norm_email).first()
        if existing:
            return existing

    if norm_phone:
        existing = qs.filter(phone__icontains=norm_phone[-10:]).first()
        if existing:
            return existing

    if canonical_name:
        for contact in qs:
            if _canonical_contact_name(contact.first_name, contact.last_name) == canonical_name:
                return contact

    return None


def _sync_location_contact(location):
    """
    Keep a Contact linked to this location when location contact fields exist.
    This is used for manual add/edit and location-import flows.
    """
    contact_name = (location.contact_name or '').strip()
    email = (location.email or '').strip().lower()
    phone = (location.phone or '').strip()
    if not any([contact_name, email, phone]):
        return None

    first_name, last_name = _split_person_name(contact_name or location.name)
    defaults = {
        'first_name': first_name,
        'last_name': last_name,
        'phone': phone,
        'city': location.city or '',
        'state': location.state or 'Florida',
        'zip_code': location.zip_code or '',
        'status': 'active',
        'is_subscribed': True,
        'location': location,
    }

    # 1) Search for existing contact at THIS location first
    contact = None
    if email and '@' in email:
        contact = Contact.objects.filter(location=location, email__iexact=email).first()
    if not contact and phone:
        norm_phone = _normalize_phone(phone)
        if norm_phone:
            contact = Contact.objects.filter(location=location, phone__icontains=norm_phone[-10:]).first()
    if not contact and first_name:
        canonical = _canonical_contact_name(first_name, last_name)
        if canonical:
            for c in Contact.objects.filter(location=location):
                if _canonical_contact_name(c.first_name, c.last_name) == canonical:
                    contact = c
                    break

    # 2) If not found at this location, check global existence for dedup only
    if not contact:
        global_contact = _find_existing_contact(
            email=email,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            location=location,
            user=location.created_by if location else None,
        )
        if global_contact:
            if not global_contact.location_id or global_contact.location_id == location.id:
                # Contact is unlinked or already at this location — reuse it
                contact = global_contact
            else:
                # Contact belongs to a different location — create a new contact
                # for this location. Skip email if it would violate unique constraint.
                create_email = None
                if email and '@' in email:
                    if not Contact.objects.filter(created_by=location.created_by, email__iexact=email).exclude(location=location).exists():
                        create_email = email
                contact = Contact.objects.create(email=create_email, created_by=location.created_by, **defaults)
                _dedupe_contacts_for_location(location, location.created_by)
                return contact

    # 3) No match anywhere — create new contact
    if not contact:
        contact = Contact.objects.create(email=(email or None), created_by=location.created_by, **defaults)
        _dedupe_contacts_for_location(location, location.created_by)
        return contact

    # 4) Update the matched contact with any missing info
    changed = False
    if contact.location_id != location.id:
        contact.location = location
        changed = True
    if not contact.first_name and first_name:
        contact.first_name = first_name
        changed = True
    if not contact.last_name and last_name:
        contact.last_name = last_name
        changed = True
    if not contact.phone and phone:
        contact.phone = phone
        changed = True
    if not contact.city and location.city:
        contact.city = location.city
        changed = True
    if not contact.state and location.state:
        contact.state = location.state
        changed = True
    if not contact.zip_code and location.zip_code:
        contact.zip_code = location.zip_code
        changed = True

    if changed:
        contact.save()

    _dedupe_contacts_for_location(location, location.created_by if location else None)
    return contact


@login_required
def location_add(request):
    form = LocationForm()
    if request.method == 'POST':
        form = LocationForm(request.POST)
        if form.is_valid():
            try:
                loc = form.save(commit=False)
                loc.created_by = request.user
                loc.save()
                _sync_location_contact(loc)
                messages.success(request, f'Location "{loc.name}" added successfully.')
                return redirect('location_list')
            except Exception as e:
                messages.error(request, f'Error saving location: {e}')
        else:
            messages.error(request, 'Location was not saved. Please fix the highlighted errors.')
    return render(request, 'admin_dash/location_form.html', {'form': form, 'title': 'Add Location'})


@login_required
def location_edit(request, pk):
    loc = get_object_or_404(Location.objects.filter(created_by=request.user), pk=pk)
    form = LocationForm(instance=loc)
    if request.method == 'POST':
        form = LocationForm(request.POST, instance=loc)
        if form.is_valid():
            try:
                loc = form.save()
                _sync_location_contact(loc)
                messages.success(request, f'Location "{loc.name}" updated.')
                return redirect('location_list')
            except Exception as e:
                messages.error(request, f'Error updating location: {e}')
        else:
            messages.error(request, 'Location update failed. Please fix the highlighted errors.')
    return render(request, 'admin_dash/location_form.html', {'form': form, 'title': 'Edit Location', 'obj': loc})


@login_required
def location_delete(request, pk):
    loc = get_object_or_404(Location.objects.filter(created_by=request.user), pk=pk)
    if request.method == 'POST':
        try:
            name = loc.name
            loc.delete()
            messages.success(request, f'Location "{name}" deleted.')
            return redirect('location_list')
        except Exception as e:
            messages.error(request, f'Error deleting location: {e}')
    return render(request, 'admin_dash/confirm_delete.html', {'obj': loc, 'type': 'Location'})


@login_required
def location_detail(request, pk):
    loc = get_object_or_404(Location.objects.prefetch_related('contact_set').filter(created_by=request.user), pk=pk)
    contacts = loc.contact_set.all().order_by('last_name', 'first_name')
    return render(
        request,
        'admin_dash/location_detail.html',
        {'loc': loc, 'contacts': contacts},
    )


# ══════════════════════════════════════════════════════════════════
# CONTACTS CRUD
# ══════════════════════════════════════════════════════════════════

@login_required
def contact_list(request):
    qs = Contact.objects.select_related('location').filter(created_by=request.user)
    q = request.GET.get('q', '')
    fstatus = request.GET.get('status', '')
    fcity = request.GET.get('city', '')
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q) |
            Q(last_name__icontains=q) |
            Q(email__icontains=q) |
            Q(phone__icontains=q) |
            Q(location__name__icontains=q)
        )
    if fstatus:
        qs = qs.filter(status=fstatus)
    if fcity:
        qs = qs.filter(city__icontains=fcity)

    paginator = Paginator(qs, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    cities = Contact.objects.filter(created_by=request.user).values_list('city', flat=True).distinct().order_by('city')
    return render(request, 'admin_dash/contacts.html', {
        'contacts': page_obj,
        'cities': cities,
        'filters': {'q': q, 'status': fstatus, 'city': fcity},
    })


def _ensure_contact_location(contact, location_type_hint='', user=None):
    """Auto-link a contact to a generated location when no location is selected."""
    if contact.location_id:
        return

    city = (contact.city or '').strip()
    if not city:
        return

    state = (contact.state or 'Event Directory and Logistic').strip() or 'Event Directory and Logistic'
    loc_type = _normalize_choice(location_type_hint, Location.TYPE_CHOICES, 'venue')
    type_labels = dict(Location.TYPE_CHOICES)
    type_label = type_labels.get(loc_type, loc_type.title())
    generated_name = f"{type_label} - {city}"

    loc, created = Location.objects.get_or_create(
        name=generated_name,
        city=city,
        defaults={
            'type': loc_type,
            'status': 'active',
            'address': 'N/A',
            'state': state,
            'created_by': user,
        },
    )
    if not created and not loc.created_by:
        loc.created_by = user
        loc.save(update_fields=['created_by'])
    contact.location = loc


@login_required
def contact_add(request):
    selected_location = None
    selected_location_type = ''

    if request.method == 'GET':
        location_id = request.GET.get('location', '').strip()
        if location_id:
            selected_location = Location.objects.filter(created_by=request.user, pk=location_id).first()
            if selected_location:
                selected_location_type = selected_location.type
                form = ContactForm(initial={
                    'location': selected_location,
                    'city': selected_location.city,
                    'state': selected_location.state,
                })
            else:
                form = ContactForm()
        else:
            form = ContactForm()

    if request.method == 'POST':
        form = ContactForm(request.POST)
        posted_location_id = request.POST.get('location', '').strip()
        if posted_location_id:
            selected_location = Location.objects.filter(created_by=request.user, pk=posted_location_id).only('type').first()
            if selected_location:
                selected_location_type = selected_location.type
        if form.is_valid():
            try:
                contact = form.save(commit=False)
                _ensure_contact_location(contact, request.POST.get('location_type', ''), request.user)
                existing = _find_existing_contact(
                    email=contact.email,
                    first_name=contact.first_name,
                    last_name=contact.last_name,
                    phone=contact.phone,
                    location=contact.location,
                    user=request.user,
                )
                if existing:
                    for field in ('first_name', 'last_name', 'phone', 'city', 'state', 'zip_code', 'age', 'gender', 'notes'):
                        cur = getattr(existing, field)
                        new = getattr(contact, field)
                        if (cur in (None, '', 0) and new not in (None, '')) or (field == 'notes' and new and new not in (cur or '')):
                            setattr(existing, field, new if field != 'notes' or not cur else f"{cur}\n{new}".strip())
                    if contact.email and not existing.email:
                        existing.email = contact.email
                    if contact.location and existing.location_id != contact.location_id:
                        existing.location = contact.location
                    existing.created_by = existing.created_by or request.user
                    existing.save()
                    if existing.location:
                        _dedupe_contacts_for_location(existing.location, request.user)
                    messages.success(request, 'Contact merged with existing entry.')
                else:
                    contact.created_by = request.user
                    contact.save()
                    if contact.location:
                        _dedupe_contacts_for_location(contact.location, request.user)
                    messages.success(request, 'Contact added successfully.')
                return redirect('contact_list')
            except Exception as e:
                messages.error(request, f'Error saving contact: {e}')
        else:
            messages.error(request, 'Contact was not saved. Please fix the highlighted errors.')
    return render(request, 'admin_dash/contact_form.html', {
        'form': form,
        'title': 'Add Contact',
        'prefill_location_type': selected_location_type,
    })


@login_required
def contact_edit(request, pk):
    contact = get_object_or_404(Contact.objects.filter(created_by=request.user), pk=pk)
    form = ContactForm(instance=contact)
    prefill_location_type = contact.location.type if contact.location else ''
    if request.method == 'POST':
        form = ContactForm(request.POST, instance=contact)
        posted_location_id = request.POST.get('location', '').strip()
        if posted_location_id:
            selected_location = Location.objects.filter(created_by=request.user, pk=posted_location_id).only('type').first()
            if selected_location:
                prefill_location_type = selected_location.type
        if form.is_valid():
            try:
                contact = form.save(commit=False)
                _ensure_contact_location(contact, request.POST.get('location_type', ''), request.user)
                existing = _find_existing_contact(
                    email=contact.email,
                    first_name=contact.first_name,
                    last_name=contact.last_name,
                    phone=contact.phone,
                    location=contact.location,
                    exclude_pk=contact.pk,
                    user=request.user,
                )
                if existing:
                    for field in ('first_name', 'last_name', 'phone', 'city', 'state', 'zip_code', 'age', 'gender', 'notes'):
                        cur = getattr(existing, field)
                        new = getattr(contact, field)
                        if (cur in (None, '', 0) and new not in (None, '')) or (field == 'notes' and new and new not in (cur or '')):
                            setattr(existing, field, new if field != 'notes' or not cur else f"{cur}\n{new}".strip())
                    if contact.email and not existing.email:
                        existing.email = contact.email
                    if contact.location and existing.location_id != contact.location_id:
                        existing.location = contact.location
                    existing.created_by = existing.created_by or request.user
                    existing.save()
                    if existing.location:
                        _dedupe_contacts_for_location(existing.location, request.user)
                    contact.delete()
                    messages.success(request, 'Duplicate contact merged into existing record.')
                else:
                    contact.created_by = request.user
                    contact.save()
                    if contact.location:
                        _dedupe_contacts_for_location(contact.location, request.user)
                    messages.success(request, 'Contact updated.')
                return redirect('contact_list')
            except Exception as e:
                messages.error(request, f'Error updating contact: {e}')
        else:
            messages.error(request, 'Contact update failed. Please fix the highlighted errors.')
    return render(request, 'admin_dash/contact_form.html', {
        'form': form,
        'title': 'Edit Contact',
        'obj': contact,
        'prefill_location_type': prefill_location_type,
    })


@login_required
def contact_detail(request, pk):
    contact = get_object_or_404(Contact.objects.select_related('location').filter(created_by=request.user), pk=pk)
    return render(request, 'admin_dash/contact_detail.html', {'contact': contact})


@login_required
def contact_delete(request, pk):
    contact = get_object_or_404(Contact.objects.filter(created_by=request.user), pk=pk)
    if request.method == 'POST':
        try:
            name = contact.full_name()
            contact.delete()
            messages.success(request, f'Contact "{name}" deleted.')
            return redirect('contact_list')
        except Exception as e:
            messages.error(request, f'Error deleting contact: {e}')
    return render(request, 'admin_dash/confirm_delete.html', {'obj': contact, 'type': 'Contact'})


# ══════════════════════════════════════════════════════════════════
# EMAIL BLASTS
# ══════════════════════════════════════════════════════════════════

@login_required
def email_list(request):
    _check_and_send_scheduled_blasts()
    blasts = EmailBlast.objects.filter(created_by=request.user).prefetch_related('target_locations', 'target_contacts').order_by('-created_at')
    paginator = Paginator(blasts, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    return render(request, 'admin_dash/emails.html', {'blasts': page_obj})


def _check_and_send_scheduled_blasts():
    """Check for scheduled blasts whose time has passed and send them."""
    from django.utils import timezone
    now = timezone.now()
    blasts = list(EmailBlast.objects.filter(
        scheduled_at__isnull=False,
        scheduled_at__lte=now,
        status='scheduled',
    ))
    for blast in blasts:
        updated = EmailBlast.objects.filter(id=blast.id, status='scheduled').update(status='sending')
        if not updated:
            continue
        blast.refresh_from_db()
        try:
            sent, failed, first_error = _send_blast_from_blast_config(blast)
        except Exception:
            sent, failed, first_error = 0, 0, "Unexpected error"
        blast.status = 'sent' if sent > 0 else 'failed'
        blast.sent_at = now
        blast.total_sent = sent
        blast.total_failed = failed
        blast.save(update_fields=['status', 'sent_at', 'total_sent', 'total_failed'])


@login_required
def email_detail(request, pk):
    blast = get_object_or_404(EmailBlast.objects.filter(created_by=request.user), pk=pk)
    if blast.status == 'scheduled':
        from django.utils import timezone
        if blast.scheduled_at and blast.scheduled_at <= timezone.now():
            _send_single_scheduled_blast(pk)
            blast.refresh_from_db()

    context = {
        'blast': blast,
        'target_locations': list(blast.target_locations.all()),
        'target_contacts': list(blast.target_contacts.all()),
    }
    return render(request, 'admin_dash/email_detail.html', context)


def _send_single_scheduled_blast(blast_id):
    """Send a single scheduled blast by ID."""
    from django.utils import timezone
    now = timezone.now()
    try:
        blast = EmailBlast.objects.get(id=blast_id, status='scheduled')
    except EmailBlast.DoesNotExist:
        return
    blast.status = 'sending'
    blast.save(update_fields=['status'])
    try:
        sent, failed, first_error = _send_blast_from_blast_config(blast)
    except Exception:
        sent, failed, first_error = 0, 0, "Unexpected error"
    blast.status = 'sent' if sent > 0 else 'failed'
    blast.sent_at = now
    blast.total_sent = sent
    blast.total_failed = failed
    blast.save(update_fields=['status', 'sent_at', 'total_sent', 'total_failed'])


@login_required
def email_compose(request):
    from .models import EmailTemplate as ET
    
    # Build type-pill data for template
    location_types = (
        Location.objects.filter(Q(created_by=request.user) | Q(created_by__isnull=True), status='active')
        .values('type')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    location_types = [
        {'type': row['type'], 'count': row['count']}
        for row in location_types
        if row['type']
    ]

    # Support pre-filling from an email template
    tpl_subject = ''
    tpl_body = ''
    tpl_name = ''
    tpl_id = request.GET.get('template_id')
    if tpl_id:
        try:
            tpl = ET.objects.filter(created_by=request.user, pk=tpl_id).first()
            if tpl:
                tpl_subject = tpl.subject
                tpl_body = tpl.body
                tpl_name = tpl.name
        except Exception:
            pass

    if request.method == 'GET':
        return render(request, 'admin_dash/email_compose.html', {
            'form': EmailBlastForm(initial={
                'subject': tpl_subject,
                'body_text': tpl_body,
            }),
            'location_types': location_types,
            'tpl_subject': tpl_subject,
            'tpl_body': tpl_body,
            'tpl_name': tpl_name,
        })

    # POST
    subject = request.POST.get('subject', '').strip()
    body_html = request.POST.get('body_html', '').strip()
    body_text = request.POST.get('body_text', '').strip()
    send_to_mode = request.POST.get('send_to_mode', 'all').strip()
    recipient_type = request.POST.get('recipient_type', 'all').strip()
    action = request.POST.get('action', 'draft').strip()
    scheduled_at_raw = request.POST.get('scheduled_at', '').strip()
    scheduled_at = _parse_scheduled_at(scheduled_at_raw) if scheduled_at_raw else None
    attachment_ids = request.POST.getlist('attachment_ids')
    temp_blast_id = request.POST.get('temp_blast_id', '').strip()

    # Accept either HTML body or plain-text body from UI
    if not subject or not (body_html or body_text):
        messages.error(request, 'Subject and message body are required.')
        form = EmailBlastForm(request.POST)
        return render(request, 'admin_dash/email_compose.html', {
            'form': form,
            'location_types': location_types,
        })

    if not body_text and body_html:
        body_text = _html_to_text(body_html)
    if not body_html and body_text:
        # Convert plain text to professional HTML email
        body_html = _format_email_html(subject, body_text, request.user)

    location_ids = request.POST.getlist('target_locations')
    target_locations = []
    if send_to_mode == 'locations_only':
        # Only use selected location IDs
        target_locations = list(Location.objects.filter(Q(created_by=request.user) | Q(created_by__isnull=True), id__in=location_ids, status='active'))
    elif send_to_mode == 'all':
        # Use all active locations
        target_locations = list(Location.objects.filter(Q(created_by=request.user) | Q(created_by__isnull=True), status='active'))
    # else: contacts_only - don't use any locations

    target_contacts = []
    if send_to_mode in ('all', 'contacts_only'):
        target_contacts = list(Contact.objects.filter(Q(created_by=request.user) | Q(created_by__isnull=True), is_subscribed=True))

    # Normalize recipient type for stored blast/send view compatibility.
    if send_to_mode == 'locations_only':
        recipient_type = 'location'
    elif send_to_mode == 'contacts_only':
        recipient_type = 'custom'
    else:
        recipient_type = 'all'

    blast_status = 'scheduled' if scheduled_at else 'draft'

    try:
        if temp_blast_id:
            blast = EmailBlast.objects.filter(created_by=request.user, id=temp_blast_id, status='draft', subject='Draft').first()
            if blast:
                blast.subject = subject
                blast.body_html = body_html
                blast.body_text = body_text
                blast.status = blast_status
                blast.recipient_type = recipient_type
                blast.total_sent = 0
                blast.total_failed = 0
                blast.scheduled_at = scheduled_at
                blast.save()
        else:
            blast = EmailBlast.objects.create(
                subject=subject,
                body_html=body_html,
                body_text=body_text,
                status=blast_status,
                recipient_type=recipient_type,
                total_sent=0,
                total_failed=0,
                scheduled_at=scheduled_at,
                created_by=request.user,
            )
        blast.target_locations.set(target_locations)
        blast.target_contacts.set(target_contacts)
        
        if attachment_ids:
            EmailAttachment.objects.filter(id__in=attachment_ids, blast__created_by=request.user).update(blast=blast)
        
        blast.save()

        if action == 'send':
            try:
                sent, failed = _send_blast(blast, target_locations, target_contacts)
                blast.status = 'sent' if sent > 0 else 'failed'
                blast.total_sent = sent
                blast.total_failed = failed
                blast.sent_at = timezone.now()
                blast.save()
                messages.success(request, f'Email blast sent! {sent} delivered, {failed} failed.')
            except Exception as e:
                messages.error(request, f'Failed to send blast: {e}')
        else:
            messages.success(
                request,
                f'Draft saved. {len(target_locations)} location(s) and {len(target_contacts)} contact(s) queued.'
            )
    except Exception as e:
        messages.error(request, f'Error creating email blast: {e}')

    return redirect('email_list')


@login_required
def email_attachment_upload(request):
    if request.method != 'POST' or not request.FILES:
        return JsonResponse({'success': False, 'error': 'No file provided'}, status=400)
    
    file = request.FILES['file']
    blast_id = request.POST.get('blast_id')
    
    max_size = 25 * 1024 * 1024
    if file.size > max_size:
        return JsonResponse({'success': False, 'error': 'File too large. Max size is 25MB.'}, status=400)
    
    allowed_types = [
        'application/pdf', 'application/msword',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.ms-excel',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'image/jpeg', 'image/png', 'image/gif', 'image/webp',
        'text/plain', 'text/csv'
    ]
    if file.content_type not in allowed_types:
        return JsonResponse({'success': False, 'error': 'File type not allowed. Allowed: PDF, DOC, DOCX, XLS, XLSX, images, TXT, CSV'}, status=400)
    
    try:
        if blast_id:
            blast = EmailBlast.objects.filter(created_by=request.user, id=blast_id).first()
            if not blast:
                return JsonResponse({'success': False, 'error': 'Blast not found'}, status=404)
        else:
            blast = EmailBlast.objects.create(
                subject='Draft',
                body_html='',
                body_text='',
                status='draft',
                created_by=request.user,
            )
        
        attachment = EmailAttachment.objects.create(
            blast=blast,
            file=file,
            blob_name='',
            original_name=file.name,
            file_size=file.size,
            content_type=file.content_type,
        )
        
        return JsonResponse({
            'success': True,
            'attachment_id': attachment.id,
            'blast_id': blast.id,
            'name': attachment.original_name,
            'size': attachment.file_size,
            'size_formatted': _format_file_size(attachment.file_size),
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def email_attachment_delete(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=400)
    
    attachment_id = request.POST.get('attachment_id')
    if not attachment_id:
        return JsonResponse({'success': False, 'error': 'Attachment ID required'}, status=400)
    
    try:
        attachment = EmailAttachment.objects.filter(
            id=attachment_id,
            blast__created_by=request.user
        ).first()
        if not attachment:
            return JsonResponse({'success': False, 'error': 'Attachment not found'}, status=404)
        
        blast_id = attachment.blast.id
        attachment.file.delete()
        attachment.delete()
        return JsonResponse({'success': True, 'blast_id': blast_id})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def _format_file_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


@login_required
def email_send(request, pk):
    blast = get_object_or_404(EmailBlast.objects.filter(created_by=request.user), pk=pk)
    if blast.status in ('sent', 'sending', 'cancelled'):
        messages.warning(request, f'This blast cannot be sent. Status: {blast.status}')
        return redirect('email_list')

    updated = EmailBlast.objects.filter(id=pk, status__in=['draft', 'scheduled']).update(status='sending')
    if not updated:
        messages.warning(request, 'This blast could not be sent. Status may have changed.')
        return redirect('email_list')

    blast.refresh_from_db()
    try:
        sent, failed, first_error = _send_blast_from_blast_config(blast)
        
        blast.refresh_from_db()
        if blast.status == 'cancelled':
            messages.warning(request, f'Blast was cancelled. {sent} emails were sent before cancellation.')
            return redirect('email_list')

        blast.status = 'sent' if sent > 0 else 'failed'
        blast.sent_at = timezone.now()
        blast.total_sent = sent
        blast.total_failed = failed
        blast.save()

        if sent > 0:
            if failed:
                messages.warning(request, f'Blast partially sent: {sent} delivered, {failed} failed.')
            else:
                messages.success(request, f'Blast sent! {sent} delivered, {failed} failed.')
        else:
            auth_hint = ''
            if first_error and (('5.7.8' in first_error) or ('SMTPAuthenticationError' in (first_error or ''))):
                auth_hint = ' Gmail SMTP authentication failed. Regenerate App Password and update EMAIL_HOST_PASSWORD.'
            messages.error(request, f'Blast failed: {failed} failed.{auth_hint}')
    except Exception as e:
        blast.status = 'failed'
        blast.save(update_fields=['status'])
        messages.error(request, f'System error during blast: {e}')

    return redirect('email_detail', pk=pk)


@login_required
def email_cancel(request, pk):
    """Cancel an email blast that is scheduled or sending."""
    blast = get_object_or_404(EmailBlast.objects.filter(created_by=request.user), pk=pk)
    
    if blast.status in ('sent', 'failed', 'cancelled'):
        messages.warning(request, f'Cannot cancel blast with status: {blast.status}')
        return redirect('email_list')
    
    blast.status = 'cancelled'
    blast.save(update_fields=['status'])
    messages.success(request, f'Email blast "{blast.subject}" has been cancelled.')
    return redirect('email_list')


# ─── Scheduled Emails (Single Recipient) ─────────────────────────────

@login_required
def scheduled_email_list(request):
    """List all scheduled emails."""
    _process_scheduled_emails()
    emails = ScheduledEmail.objects.filter(created_by=request.user).order_by('-created_at')
    paginator = Paginator(emails, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    return render(request, 'admin_dash/scheduled_emails.html', {'emails': page_obj})


@login_required
def scheduled_email_create(request):
    """Create a new scheduled email."""
    if request.method == 'POST':
        recipient_email = request.POST.get('recipient_email', '').strip()
        subject = request.POST.get('subject', '').strip()
        message = request.POST.get('message', '').strip()
        scheduled_at = request.POST.get('scheduled_at', '').strip()

        if not recipient_email or not subject or not message:
            messages.error(request, 'Recipient email, subject, and message are required.')
            return redirect('scheduled_email_create')

        scheduled_at_dt = None
        if scheduled_at:
            try:
                scheduled_at_dt = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
            except ValueError:
                messages.error(request, 'Invalid date/time format.')
                return redirect('scheduled_email_create')

        email = ScheduledEmail.objects.create(
            recipient_email=recipient_email,
            subject=subject,
            message=message,
            scheduled_at=scheduled_at_dt,
            status='pending',
            created_by=request.user if request.user.is_authenticated else None,
        )

        if not scheduled_at_dt:
            success, error = email.send()
            if success:
                messages.success(request, f'Email sent successfully to {recipient_email}')
            else:
                messages.error(request, f'Failed to send email: {error}')
        else:
            messages.success(request, f'Email scheduled for {scheduled_at_dt}')

        return redirect('scheduled_email_list')

    return render(request, 'admin_dash/scheduled_email_form.html', {'email': None})


@login_required
def scheduled_email_send_now(request, pk):
    """Send a scheduled email immediately."""
    email = get_object_or_404(ScheduledEmail.objects.filter(created_by=request.user), pk=pk)
    
    if email.status == 'sent':
        messages.warning(request, 'This email has already been sent.')
        return redirect('scheduled_email_list')
    
    success, error = email.send()
    
    if success:
        messages.success(request, f'Email sent successfully to {email.recipient_email}')
    else:
        messages.error(request, f'Failed to send email: {error}')
    
    return redirect('scheduled_email_list')


@login_required
def scheduled_email_delete(request, pk):
    """Delete a scheduled email."""
    email = get_object_or_404(ScheduledEmail, pk=pk)
    if request.method == 'POST':
        email.delete()
        messages.success(request, 'Email deleted successfully.')
        return redirect('scheduled_email_list')
    return render(request, 'admin_dash/confirm_delete.html', {'obj': email, 'type': 'Scheduled Email'})


def _process_scheduled_emails():
    """Process and send pending scheduled emails that are due."""
    from django.utils import timezone
    now = timezone.now()
    pending_emails = list(ScheduledEmail.objects.filter(
        status='pending',
        scheduled_at__isnull=False,
        scheduled_at__lte=now,
    ))
    
    for email in pending_emails:
        updated = ScheduledEmail.objects.filter(
            id=email.id, 
            status='pending'
        ).update(status='sending')
        
        if not updated:
            continue
        
        email.refresh_from_db()
        success, error = email.send()
        
        if not success:
            logger.error(f"Failed to send scheduled email #{email.id}: {error}")


def _send_blast_from_blast_config(blast):
    """
    Send an EmailBlast using its saved recipient configuration.
    Returns (sent_count, failed_count, first_error_message_or_None).
    """
    blast.refresh_from_db()
    if blast.status == 'cancelled':
        return 0, 0, 'Blast was cancelled.'
    
    user = blast.created_by
    
    if blast.recipient_type == 'all':
        recipients = list(Contact.objects.filter(created_by=user, is_subscribed=True).values_list('email', flat=True))
        loc_emails = list(
            Location.objects.filter(created_by=user, status='active', email__isnull=False).exclude(email='').values_list('email', flat=True)
        )
    elif blast.recipient_type == 'location':
        loc_ids = list(blast.target_locations.values_list('id', flat=True))
        recipients = list(
            Contact.objects.filter(created_by=user, location__in=loc_ids, is_subscribed=True).values_list('email', flat=True)
        )
        loc_emails = list(
            Location.objects.filter(created_by=user, id__in=loc_ids, email__isnull=False).exclude(email='').values_list('email', flat=True)
        )
    else:
        recipients = list(blast.target_contacts.filter(created_by=user).values_list('email', flat=True))
        loc_emails = []

    all_emails = sorted({e.strip().lower() for e in (recipients + loc_emails) if e and '@' in e})

    if not all_emails:
        return 0, 0, 'No valid recipient emails found.'

    attachments = list(blast.attachments.all())
    
    sent, failed = 0, 0
    first_error = None
    for email_addr in all_emails:
        blast.refresh_from_db()
        if blast.status == 'cancelled':
            break
        
        try:
            msg = EmailMultiAlternatives(
                subject=blast.subject,
                body=blast.body_text or 'Please view this email in HTML format.',
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[email_addr],
            )
            if blast.body_html:
                msg.attach_alternative(blast.body_html, "text/html")
            
            for att in attachments:
                if att.file:
                    msg.attach_file(att.file.path)
            
            msg.send()
            sent += 1
        except Exception as exc:
            if first_error is None:
                first_error = str(exc)
            failed += 1

    return sent, failed, first_error


@login_required
def email_delete(request, pk):
    blast = get_object_or_404(EmailBlast.objects.filter(created_by=request.user), pk=pk)
    if request.method == 'POST':
        try:
            blast.delete()
            messages.success(request, 'Email blast deleted.')
            return redirect('email_list')
        except Exception as e:
            messages.error(request, f'Error deleting blast: {e}')
    return render(request, 'admin_dash/confirm_delete.html', {'obj': blast, 'type': 'Email Blast'})


def _send_blast(blast, locations, contacts):
    sent = 0
    failed = 0
    all_emails = set()

    for loc in locations:
        if loc.email and '@' in loc.email:
            all_emails.add((loc.email.strip().lower(), loc.name))

    for contact in contacts:
        if contact.email and '@' in contact.email:
            name = f'{contact.first_name} {contact.last_name}'.strip() or contact.email
            all_emails.add((contact.email.strip().lower(), name))

    for email_addr, name in all_emails:
        try:
            msg = EmailMultiAlternatives(
                subject=blast.subject,
                body=blast.body_text or '',
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[f'{name} <{email_addr}>'],
            )
            if blast.body_html:
                msg.attach_alternative(blast.body_html, 'text/html')
            msg.send()
            sent += 1
            logger.info('Blast #%s sent to %s', blast.id, email_addr)
        except Exception as exc:
            failed += 1
            logger.error('Blast #%s failed for %s: %s', blast.id, email_addr, exc)

    return sent, failed


def _html_to_text(html):
    text = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()


def _format_email_html(subject, body_text, user):
    """Convert plain text to professional HTML email with proper formatting."""
    # Convert newlines to <br> and paragraphs
    paragraphs = body_text.strip().split('\n\n')
    body_html = ''
    for para in paragraphs:
        para = para.strip()
        if para:
            # Convert single newlines within paragraph to <br>
            para = para.replace('\n', '<br>')
            body_html += f'<p style="margin: 0 0 16px 0; line-height: 1.6; color: #333333;">{para}</p>\n'
    
    # Professional email template
    html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{subject}</title>
</head>
<body style="margin: 0; padding: 0; font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #f4f4f4;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f4f4f4;">
        <tr>
            <td align="center" style="padding: 40px 10px;">
                <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                    <!-- Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #f97316 0%, #ea580c 100%); padding: 30px 40px; border-radius: 8px 8px 0 0;">
                            <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">Event Directory and Logistic</h1>
                            <p style="margin: 8px 0 0 0; color: rgba(255,255,255,0.9); font-size: 14px;">{subject}</p>
                        </td>
                    </tr>
                    <!-- Body -->
                    <tr>
                        <td style="padding: 40px;">
                            {body_html}
                        </td>
                    </tr>
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f9fafb; padding: 24px 40px; border-top: 1px solid #e5e7eb; border-radius: 0 0 8px 8px;">
                            <p style="margin: 0 0 8px 0; color: #6b7280; font-size: 12px; text-align: center;">
                                You're receiving this email because you're subscribed to Event Directory and Logistic updates.
                            </p>
                            <p style="margin: 0; color: #9ca3af; font-size: 11px; text-align: center;">
                                © 2025 Event Directory and Logistic. All rights reserved.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>'''
    return html


def _parse_scheduled_at(value):
    try:
        dt = datetime.fromisoformat(value)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
# DATA IMPORT
# ══════════════════════════════════════════════════════════════════

@login_required
def import_data(request):
    logs = ImportLog.objects.filter(imported_by=request.user).order_by('-created_at')[:20]
    last_sheet_results = request.session.pop('last_import_sheet_results', [])
    form = ImportForm()

    if request.method == 'POST':
        form = ImportForm(request.POST, request.FILES)
        if form.is_valid():
            f = request.FILES['file']
            import_type = form.cleaned_data['import_type'].strip().lower()
            rows_ok, rows_fail, rows_skip, errors = 0, 0, 0, []
            sheet_results = []

            try:
                with transaction.atomic():
                    pd = _get_pandas()
                    if pd is not None:
                        file_name = f.name.lower()
                        if file_name.endswith('.csv'):
                            raw_df = pd.read_csv(f, header=None, dtype=str, keep_default_na=False)
                            resolved_type = _resolve_sheet_import_type(import_type, raw_df)
                            if not resolved_type:
                                raise ValueError('Could not auto-detect import type from CSV headers.')

                            ok, fail, skip, sheet_errors, row_count = _import_dataframe_sheet(
                                raw_df, resolved_type, request.user
                            )
                            rows_ok += ok
                            rows_fail += fail
                            rows_skip += skip
                            errors.extend([f'CSV: {err}' for err in sheet_errors])
                            sheet_results.append({
                                'sheet': 'CSV',
                                'type': resolved_type,
                                'rows': row_count,
                                'imported': ok,
                                'failed': fail,
                                'skip': skip,
                                'status': 'ok' if (ok or fail or skip) else 'empty',
                            })
                        elif file_name.endswith(('.xlsx', '.xls')):
                            excel_engine = 'xlrd' if file_name.endswith('.xls') else 'openpyxl'
                            try:
                                f.seek(0)
                                raw_sheets = pd.read_excel(
                                    f, engine=excel_engine, sheet_name=None, header=None, dtype=str
                                )
                                if not raw_sheets:
                                    raise ValueError('Excel file has no sheets.')
                            except ValueError:
                                raise
                            except Exception as exc:
                                raise ValueError(
                                    f'Unable to read Excel file ({excel_engine}). '
                                    f'Install required dependency or upload CSV. Details: {exc}'
                                )
                            processed_sheets = 0
                            for sheet_name, raw_df in raw_sheets.items():
                                resolved_type = _resolve_sheet_import_type(import_type, raw_df)
                                if not resolved_type:
                                    sheet_results.append({
                                        'sheet': str(sheet_name),
                                        'type': 'skipped',
                                        'rows': 0,
                                        'imported': 0,
                                        'failed': 0,
                                        'status': 'skipped',
                                    })
                                    continue

                                ok, fail, skip, sheet_errors, row_count = _import_dataframe_sheet(
                                    raw_df, resolved_type, request.user
                                )
                                processed_sheets += 1
                                rows_ok += ok
                                rows_fail += fail
                                rows_skip += skip
                                errors.extend([f'{sheet_name}: {err}' for err in sheet_errors])
                                sheet_results.append({
                                    'sheet': str(sheet_name),
                                    'type': resolved_type,
                                    'rows': row_count,
                                    'imported': ok,
                                    'failed': fail,
                                    'skip': skip,
                                    'status': 'ok' if (ok or fail or skip) else 'empty',
                                })

                            if processed_sheets == 0:
                                raise ValueError('No usable sheets found in this Excel file.')
                        else:
                            raise ValueError('Unsupported file format. Use .csv, .xlsx or .xls')
                    else:
                        # CSV-only fallback (no pandas)
                        if not f.name.lower().endswith('.csv'):
                            raise ValueError('Excel import requires pandas/openpyxl. Install dependencies or upload CSV.')
                        content = f.read().decode('utf-8', errors='ignore')
                        resolved_type = import_type
                        if import_type == 'auto':
                            resolved_type = _auto_detect_csv_import_type(content)
                            if not resolved_type:
                                raise ValueError('Could not auto-detect import type from CSV headers.')

                        rows = _prepare_csv_rows_for_import(content, resolved_type)
                        ok, fail, skip, sheet_errors = _bulk_import_rows(rows, resolved_type, request.user)
                        rows_ok += ok
                        rows_fail += fail
                        rows_skip += skip
                        errors.extend([f'CSV: {err}' for err in sheet_errors])
                        sheet_results.append({
                            'sheet': 'CSV',
                            'type': resolved_type,
                            'rows': len(rows),
                            'imported': ok,
                            'failed': fail,
                            'status': 'ok' if (ok or fail) else 'empty',
                        })

                    # Save history INSIDE the transaction so it's never lost
                    summary_lines = [
                        (
                            f"{item['sheet']} [{item['type']}]: "
                            f"rows={item['rows']}, imported={item['imported']}, "
                            f"failed={item['failed']}, status={item['status']}"
                        )
                        for item in sheet_results
                    ]
                    ImportLog.objects.create(
                        filename=f.name,
                        rows_imported=rows_ok,
                        rows_failed=rows_fail,
                        errors=json.dumps({"summary": summary_lines, "errors": errors}),
                        imported_by=request.user,
                    )

                cache.delete('dashboard_stats')
                request.session['last_import_sheet_results'] = sheet_results[:120]
                messages.success(
                    request,
                    f'Import complete: {rows_ok} rows imported, {rows_fail} failed, {rows_skip} duplicates skipped across {len(sheet_results)} sheet(s).'
                )
                if rows_fail and errors:
                    messages.warning(request, f'First error: {errors[0]}')
            except Exception as e:
                # Always save a history entry even on failure
                try:
                    ImportLog.objects.create(
                        filename=f.name,
                        rows_imported=rows_ok,
                        rows_failed=rows_fail or 1,
                        errors=json.dumps({"error": str(e), "errors": errors[:50]}),
                        imported_by=request.user,
                    )
                except Exception:
                    pass
                messages.error(request, f'Import error: {e}')

            return redirect('import_data')

    return render(
        request,
        'admin_dash/import.html',
        {'form': form, 'logs': logs, 'last_sheet_results': last_sheet_results},
    )


def _resolve_sheet_import_type(requested_type, raw_df):
    explicit_types = {'location', 'contact', 'both'}
    if requested_type in explicit_types:
        return requested_type
    if requested_type != 'auto':
        raise ValueError(f"Unsupported import type: {requested_type}")
    return _auto_detect_import_type(raw_df)


def _auto_detect_import_type(raw_df):
    best_type = None
    best_metric = -1
    best_score = -1

    for candidate in ('location', 'contact', 'both'):
        try:
            df, meta = _prepare_dataframe_for_import(raw_df, candidate, return_meta=True)
            metric = (meta['header_score'] * 100000) + len(df)
            if metric > best_metric:
                best_metric = metric
                best_score = meta['header_score']
                best_type = candidate
        except Exception:
            continue

    if best_type is None:
        return 'location'
    return best_type


def _auto_detect_csv_import_type(content):
    rows = list(csv.reader(io.StringIO(content)))
    if not rows:
        return None

    best_type = None
    best_metric = -1
    best_score = -1
    for candidate in ('location', 'contact', 'both'):
        header_idx = _find_header_index(rows, candidate)
        header_row = rows[header_idx] if header_idx < len(rows) else []
        score, _ = _score_header_row(header_row, candidate)
        data_rows = sum(
            1 for raw in rows[header_idx + 1:]
            if any(str(v).strip() for v in raw)
        )
        metric = (score * 100000) + data_rows
        if metric > best_metric:
            best_metric = metric
            best_score = score
            best_type = candidate

    if best_type is None or (best_score < 2 and len(rows) < 3):
        return None
    if best_type is None:
        best_type = 'location'
    return best_type


def _get_import_handler(import_type, user):
    handlers = {
        'location': lambda row: _import_location_row(row, user),
        'contact': _import_contact_row,
        'both': lambda row: _import_both_row(row, user),
    }
    handler = handlers.get(import_type)
    if handler is None:
        raise ValueError(f"Unsupported import type: {import_type}")
    return handler


def _import_row_collection(rows, import_type, user):
    handler = _get_import_handler(import_type, user)
    rows_ok, rows_fail, errors = 0, 0, []
    for row in rows:
        try:
            handler(row)
            rows_ok += 1
        except Exception as exc:
            rows_fail += 1
            if len(errors) < 200:
                errors.append(str(exc))
    return rows_ok, rows_fail, errors


def _bulk_import_rows(rows, import_type, user):
    """Fast bulk import with pre-loaded caches and batched DB writes."""
    rows_ok, rows_fail, errors = 0, 0, []
    rows_skip = 0

    if import_type == 'location':
        rows_ok, rows_fail, rows_skip, errors = _bulk_import_locations(rows, user)
    elif import_type == 'contact':
        rows_ok, rows_fail, rows_skip, errors = _bulk_import_contacts(rows, user)
    elif import_type == 'both':
        loc_ok, loc_fail, loc_skip, loc_err = _bulk_import_locations(rows, user, for_both=True)
        con_ok, con_fail, con_err = _bulk_import_contacts(rows, user)
        rows_ok = loc_ok + con_ok
        rows_fail = loc_fail + con_fail
        rows_skip = loc_skip
        errors = loc_err + con_err

    return rows_ok, rows_fail, rows_skip, errors


def _bulk_import_locations(rows, user, for_both=False):
    """Bulk location import — pre-loads existing, skip duplicates, batch-creates new."""
    existing_locs = {
        (loc.name.strip().lower(), (loc.city or '').strip().lower()): loc
        for loc in Location.objects.filter(created_by=user)
    }

    to_create = []
    rows_ok, rows_fail, rows_skip, errors = 0, 0, 0, []

    for row in rows:
        try:
            g = lambda *keys, **kw: _get_col(row, *keys, **kw)
            name = (g('name') or g('location_name', 'locationname') or
                    g('venue_name', 'venue', 'location_venue_name') or
                    g('organization_name', 'organisation_name', 'company_name') or
                    g('business_name', 'businessname') or
                    g('apartment_name', 'complex_name') or g('property_name')).strip()
            if not name:
                raise ValueError('Location name is required')

            city = g('city')
            state = g('state') or 'Florida'
            category = g('category', 'type', 'location_type')
            location_type = _map_category_to_type(category)
            notes = g('notes', 'note', 'comments', 'remarks')
            if category and category.lower() not in notes.lower():
                notes = f"[Category] {category}\n{notes}".strip()

            key = (name.strip().lower(), (city or '').strip().lower())

            if key in existing_locs:
                rows_skip += 1
                continue
            else:
                new_loc = Location(
                    name=_clip(name, 200),
                    type=location_type,
                    status='active',
                    address=_clip(g('address', 'street', 'street_address'), 300) or 'N/A',
                    city=_clip(city, 100),
                    state=_clip(state, 50),
                    zip_code=_clip(g('zip', 'zip_code', 'zipcode', 'postal_code'), 10),
                    county=_clip(g('county'), 100),
                    phone=_clip(g('phone', 'telephone', 'phone_number'), 30),
                    email=_clip(g('email', 'email_address'), 254),
                    website=_clip(g('website_url', 'website', 'web', 'url'), 200),
                    facebook=_clip(g('facebook', 'facebook_url', 'facebook_page'), 200),
                    instagram=_clip(g('instagram', 'instagram_url', 'instagram_profile'), 200),
                    twitter=_clip(g('twitter', 'twitter_url', 'x', 'x_url'), 200),
                    social_link=_clip(g('social_link', 'social', 'social_url'), 200),
                    contact_name=_clip(g('contact', 'contact_name', 'contact_person', 'manager'), 150),
                    contact_title=_clip(g('title', 'contact_title', 'position'), 100),
                    notes=notes,
                    amenities=g('amenities', 'features'),
                    description=g('description', 'desc'),
                    created_by=user,
                )
                to_create.append(new_loc)
                rows_ok += 1
        except Exception as exc:
            rows_fail += 1
            if len(errors) < 200:
                errors.append(str(exc))

    if to_create:
        Location.objects.bulk_create(to_create, batch_size=500)

    return rows_ok, rows_fail, rows_skip, errors


def _bulk_import_contacts(rows, user=None):
    """Bulk contact import — pre-loads existing, batch-creates new."""
    # Get existing contacts - both user's and ones without created_by (to handle legacy data)
    existing_by_email = {}
    for c in Contact.objects.filter(Q(created_by=user) | Q(created_by__isnull=True)).exclude(email='').exclude(email__isnull=True):
        existing_by_email[c.email.strip().lower()] = c

    to_create, to_update = [], []
    rows_ok, rows_fail, errors = 0, 0, []

    for row in rows:
        try:
            g = lambda *keys, **kw: _get_col(row, *keys, **kw)
            email = g('contact_email', 'email', 'email_address', 'e_mail')
            first = g('first_name', 'firstname', 'first', 'contact_first_name')
            last = g('last_name', 'lastname', 'last', 'surname', 'contact_last_name')

            if not first and not last:
                full = g('contact_name', 'name', 'full_name', 'fullname',
                         'link_to_location', 'location_name', 'venue_name')
                if full and ' ' in full:
                    parts = full.split(' ', 1)
                    first, last = parts[0], parts[1]
                elif full:
                    first = full

            location = _resolve_or_create_location_from_row(row, user)
            job_title = g('job_title', 'title', 'designation')
            notes = g('notes', 'note', 'comments')
            if job_title and job_title.lower() not in notes.lower():
                notes = f"[Job Title] {job_title}\n{notes}".strip()

            phone = _clip(g('contact_phone', 'phone', 'telephone', 'phone_number', 'mobile'), 30)
            city = _clip(g('contact_city', 'city'), 100)
            state = _clip(g('contact_state', 'state') or 'Florida', 50)
            first = _strip_contact_suffix(first or 'Unknown')
            last = _strip_contact_suffix(last or '')

            norm_email = (email or '').strip().lower() if email and '@' in email else ''

            if norm_email and norm_email in existing_by_email:
                existing = existing_by_email[norm_email]
                dirty = False
                for fld, val in [('first_name', first), ('last_name', last),
                                  ('phone', phone), ('city', city), ('state', state),
                                  ('zip_code', g('zip', 'zip_code', 'zipcode')),
                                  ('age', _safe_int(g('age'))),
                                  ('gender', g('gender', 'sex')[:1].upper() if g('gender', 'sex') else ''),
                                  ('notes', notes)]:
                    cur = getattr(existing, fld)
                    if cur in (None, '', 0) and val not in (None, ''):
                        setattr(existing, fld, val)
                        dirty = True
                if location and existing.location_id != location.pk:
                    existing.location = location
                    dirty = True
                if not existing.created_by:
                    existing.created_by = user
                    dirty = True
                if dirty:
                    to_update.append(existing)
            else:
                if first == 'Unknown' and not last and not phone and not location:
                    raise ValueError('Contact row has no email and no usable identity fields.')
                new_c = Contact(
                    email=(norm_email or None),
                    first_name=first, last_name=last, phone=phone,
                    city=city, state=state,
                    zip_code=g('zip', 'zip_code', 'zipcode'),
                    age=_safe_int(g('age')),
                    gender=g('gender', 'sex')[:1].upper() if g('gender', 'sex') else '',
                    notes=notes, location=location,
                    created_by=user,
                )
                to_create.append(new_c)
                if norm_email:
                    existing_by_email[norm_email] = new_c

            rows_ok += 1
        except Exception as exc:
            rows_fail += 1
            if len(errors) < 200:
                errors.append(str(exc))

    if to_create:
        Contact.objects.bulk_create(to_create, batch_size=500, ignore_conflicts=True)
    if to_update:
        to_update_with_pk = [c for c in to_update if c.pk]
        if to_update_with_pk:
            Contact.objects.bulk_update(
                to_update_with_pk,
                ['first_name', 'last_name', 'phone', 'city', 'state', 'zip_code',
                 'age', 'gender', 'notes', 'location', 'created_by'],
                batch_size=500,
            )

    # Dedup affected locations at the end
    affected = {c.location_id for c in to_create + to_update if c.location_id}
    for loc_id in affected:
        try:
            loc = Location.objects.get(pk=loc_id)
            _dedupe_contacts_for_location(loc, user)
        except Location.DoesNotExist:
            pass

    return rows_ok, rows_fail, errors


def _import_dataframe_sheet(raw_df, import_type, user):
    df = _prepare_dataframe_for_import(raw_df, import_type)
    rows = [row for _, row in df.iterrows()]
    rows_ok, rows_fail, rows_skip, errors = _bulk_import_rows(rows, import_type, user)
    return rows_ok, rows_fail, rows_skip, errors, len(df)


def _get_col(row, *keys, default=''):
    """
    Safely extract a value from a pandas Series or plain dict.
    Tries multiple key variants (snake_case, space, title, upper).
    Ignores NaN / None / empty strings.
    """
    variants = []
    for key in keys:
        variants += [
            key,
            key.replace('_', ' '),
            key.replace(' ', '_'),
            key.title(),
            key.replace('_', ' ').title(),
            key.upper(),
        ]
    # De-duplicate while preserving order
    seen = set()
    uniq = [v for v in variants if not (v in seen or seen.add(v))]

    for k in uniq:
        try:
            if isinstance(row, dict):
                v = row.get(k, row.get(k.lower(), None))
            else:
                # pandas Series — use index lookup
                v = row[k] if k in row.index else None
            if v is not None and str(v).strip() not in ('', 'nan', 'None', 'none', 'NULL', 'null'):
                return str(v).strip()
        except (KeyError, IndexError, TypeError):
            continue
    return default


def _normalize_header_name(value):
    s = str(value or '').strip().lower()
    s = s.replace('&', ' and ')
    s = re.sub(r'[^a-z0-9]+', '_', s)
    return s.strip('_')


def _expected_headers(import_type):
    if import_type == 'location':
        return {
            'name', 'location_name', 'address', 'city', 'state', 'zip', 'zip_code', 'zipcode',
            'county', 'phone', 'email', 'website_url', 'website', 'facebook', 'instagram',
            'twitter', 'social_link', 'contact', 'contact_name', 'notes', 'venue_name',
            'venue', 'location_venue_name', 'organization_name', 'organisation_name',
            'company_name', 'business_name', 'apartment_name', 'complex_name'
        }
    if import_type == 'contact':
        return {
            'first_name', 'last_name', 'name', 'full_name', 'email', 'phone', 'city',
            'state', 'zip', 'zip_code', 'location_id', 'location_name', 'contact_email',
            'contact_phone', 'contact_city', 'contact_state', 'link_to_location', 'job_title'
        }
    if import_type == 'both':
        return _expected_headers('location') | _expected_headers('contact')
    return set()


def _required_headers(import_type):
    if import_type == 'location':
        return {
            'name', 'location_name', 'venue_name', 'venue', 'location_venue_name',
            'organization_name', 'organisation_name', 'company_name', 'business_name'
        }
    if import_type == 'contact':
        return {'email', 'contact_email'}
    if import_type == 'both':
        return _required_headers('location') | _required_headers('contact')
    return set()


def _score_header_row(row_values, import_type):
    headers = {_normalize_header_name(v) for v in row_values if str(v or '').strip()}
    expected = _expected_headers(import_type)
    required = _required_headers(import_type)
    expected_hits = len(headers & expected)
    required_hits = len(headers & required)
    return expected_hits + (required_hits * 5), headers


def _find_header_index(rows, import_type, max_scan=25):
    best_idx = 0
    best_score = -1
    scan_rows = rows[:max_scan]
    for idx, row in enumerate(scan_rows):
        score, _ = _score_header_row(row, import_type)
        if score > best_score:
            best_score = score
            best_idx = idx
    if best_score < 2:
        return 0
    return best_idx


def _make_unique_headers(headers):
    seen = {}
    result = []
    for h in headers:
        key = h or 'col'
        if key not in seen:
            seen[key] = 1
            result.append(key)
        else:
            seen[key] += 1
            result.append(f"{key}_{seen[key]}")
    return result


def _prepare_dataframe_for_import(raw_df, import_type, return_meta=False):
    raw_df = raw_df.fillna('')
    rows = raw_df.values.tolist()
    if not rows:
        raise ValueError('File is empty.')

    header_idx = _find_header_index(rows, import_type)
    raw_headers = rows[header_idx]
    header_score, header_set = _score_header_row(raw_headers, import_type)
    headers = _make_unique_headers([_normalize_header_name(v) for v in raw_headers])

    data_rows = rows[header_idx + 1:]
    df = pd.DataFrame(data_rows, columns=headers)
    df = df.fillna('')
    df = df.loc[df.apply(lambda r: any(str(v).strip() for v in r.values), axis=1)]
    df = df.reset_index(drop=True)
    if return_meta:
        return df, {'header_index': header_idx, 'header_score': header_score, 'header_set': header_set}
    return df


def _pick_best_sheet_dataframe(raw_sheets, import_type):
    best_df = None
    best_metric = -1

    for _, raw_df in raw_sheets.items():
        try:
            candidate_df, meta = _prepare_dataframe_for_import(raw_df, import_type, return_meta=True)
        except Exception:
            continue
        # Prefer sheets with strong header match first, then with more rows.
        metric = (meta['header_score'] * 100000) + len(candidate_df)
        if metric > best_metric:
            best_metric = metric
            best_df = candidate_df

    if best_df is None:
        raise ValueError('Could not find a usable sheet in this Excel file.')
    return best_df


def _prepare_csv_rows_for_import(content, import_type):
    all_rows = list(csv.reader(io.StringIO(content)))
    if not all_rows:
        return []
    header_idx = _find_header_index(all_rows, import_type)
    headers = _make_unique_headers([_normalize_header_name(v) for v in all_rows[header_idx]])

    rows = []
    for raw in all_rows[header_idx + 1:]:
        if not any(str(v).strip() for v in raw):
            continue
        padded = raw + [''] * max(0, len(headers) - len(raw))
        row = {headers[i]: padded[i] for i in range(len(headers))}
        rows.append(row)
    return rows


def _import_location_row(row, user):
    g = lambda *keys, **kw: _get_col(row, *keys, **kw)

    name = (g('name') or g('location_name', 'locationname') or
            g('venue_name', 'venue', 'location_venue_name') or
            g('organization_name', 'organisation_name', 'company_name') or
            g('business_name', 'businessname') or g('apartment_name', 'complex_name') or
            g('property_name')).strip()
    if not name:
        raise ValueError('Location name is required')

    city  = g('city')
    state = g('state') or 'Florida'

    category = g('category', 'type', 'location_type')
    location_type = _map_category_to_type(category)
    notes = g('notes', 'note', 'comments', 'remarks')
    if category and category.lower() not in notes.lower():
        notes = f"[Category] {category}\n{notes}".strip()

    location, _ = Location.objects.update_or_create(
        name=_clip(name, 200),
        city=_clip(city, 100),
        defaults={
            'address':      _clip(g('address', 'street', 'street_address'), 300),
            'state':        _clip(state, 50),
            'zip_code':     _clip(g('zip', 'zip_code', 'zipcode', 'postal_code'), 10),
            'county':       _clip(g('county'), 100),
            'phone':        _clip(g('phone', 'telephone', 'phone_number'), 30),
            'email':        _clip(g('email', 'email_address'), 254),
            'website':      _clip(g('website_url', 'website', 'web', 'url'), 200),
            'facebook':     _clip(g('facebook', 'facebook_url', 'facebook_page'), 200),
            'instagram':    _clip(g('instagram', 'instagram_url', 'instagram_profile'), 200),
            'twitter':      _clip(g('twitter', 'twitter_url', 'x', 'x_url'), 200),
            'social_link':  _clip(g('social_link', 'social', 'social_url'), 200),
            'contact_name': _clip(g('contact', 'contact_name', 'contact_person', 'manager'), 150),
            'contact_title':_clip(g('title', 'contact_title', 'position'), 100),
            'type':         location_type,
            'notes':        notes,
            'amenities':    g('amenities', 'features'),
            'description':  g('description', 'desc'),
            'created_by':   user,
        }
    )
    _sync_location_contact(location)
    return location


def _import_contact_row(row, default_location=None, user=None):
    g = lambda *keys, **kw: _get_col(row, *keys, **kw)

    email = g('contact_email', 'email', 'email_address', 'e_mail')

    first = g('first_name', 'firstname', 'first', 'contact_first_name')
    last  = g('last_name',  'lastname',  'last', 'surname', 'contact_last_name')

    # Support "Full Name" column
    if not first and not last:
        full = g('contact_name', 'name', 'full_name', 'fullname', 'link_to_location', 'location_name', 'venue_name')
        if full and ' ' in full:
            parts = full.split(' ', 1)
            first, last = parts[0], parts[1]
        elif full:
            first = full

    location = default_location or _resolve_or_create_location_from_row(row, user)
    job_title = g('job_title', 'title', 'designation')
    notes = g('notes', 'note', 'comments')
    if job_title and job_title.lower() not in notes.lower():
        notes = f"[Job Title] {job_title}\n{notes}".strip()

    phone = _clip(g('contact_phone', 'phone', 'telephone', 'phone_number', 'mobile'), 30)
    city = _clip(g('contact_city', 'city'), 100)
    state = _clip(g('contact_state', 'state') or 'Florida', 50)
    first = _strip_contact_suffix(first or 'Unknown')
    last = _strip_contact_suffix(last or '')

    defaults = {
        'first_name': first,
        'last_name': last,
        'phone': phone,
        'city': city,
        'state': state,
        'zip_code': g('zip', 'zip_code', 'zipcode'),
        'age': _safe_int(g('age')),
        'gender': g('gender', 'sex')[:1].upper() if g('gender', 'sex') else '',
        'notes': notes,
        'location': location,
        'created_by': user,
    }

    existing = _find_existing_contact(
        email=email,
        first_name=first,
        last_name=last,
        phone=phone,
        location=location,
        user=user,
    )
    if existing:
        for k, v in defaults.items():
            if k == 'created_by':
                if not existing.created_by:
                    existing.created_by = v
            elif getattr(existing, k) in (None, '', 0, False) and v not in (None, ''):
                setattr(existing, k, v)
        if email and '@' in email and not existing.email:
            existing.email = email.strip().lower()
        existing.save()
        if location:
            _dedupe_contacts_for_location(location, user)
        return

    # No email: still create/update by weak natural key instead of silently skipping.
    if first == 'Unknown' and not last and not phone and not location:
        raise ValueError('Contact row has no email and no usable identity fields.')

    created = Contact.objects.create(email=(email.strip().lower() if email and '@' in email else None), **defaults)
    if location:
        _dedupe_contacts_for_location(location, user)
    return created


def _import_both_row(row, user):
    location = _import_location_row(row, user)
    _import_contact_row(row, default_location=location, user=user)


def _resolve_location_from_row(row, user=None):
    g = lambda *keys, **kw: _get_col(row, *keys, **kw)

    loc_id = _safe_int(g('location_id', 'locationid', 'loc_id'))
    if loc_id:
        loc = Location.objects.filter(created_by=user, id=loc_id).first()
        if loc:
            return loc

    location_name = g(
        'link_to_location', 'location', 'location_name', 'venue',
        'venue_name', 'property_name', 'name'
    ).strip()
    if location_name:
        city = g('city', 'location_city').strip()
        qs = Location.objects.filter(created_by=user, name__iexact=location_name)
        if city:
            qs = qs.filter(city__iexact=city)
        loc = qs.first()
        if loc:
            return loc

        # Fallback when exact name doesn't match but city+partial name does.
        if city:
            loc = Location.objects.filter(created_by=user, city__iexact=city, name__icontains=location_name).first()
            if loc:
                return loc

    return None


def _resolve_or_create_location_from_row(row, user=None):
    """Resolve an existing location from a row, or create one when location fields are present."""
    existing = _resolve_location_from_row(row, user)
    if existing:
        return existing

    g = lambda *keys, **kw: _get_col(row, *keys, **kw)

    # For contact import, avoid generic 'name' to prevent using person's name as location.
    location_name = g(
        'link_to_location', 'location', 'location_name', 'venue',
        'venue_name', 'property_name', 'business_name', 'company_name',
        'organization_name', 'organisation_name', 'apartment_name', 'complex_name'
    ).strip()

    city = g('location_city', 'city').strip()
    state = g('location_state', 'state').strip() or 'Florida'
    category = g('location_type', 'type', 'category', 'venue_type')
    location_type = _map_category_to_type(category)

    # If upload has no explicit location name but has type/city, synthesize one
    # so dashboard location-by-type chart can still reflect imported contact rows.
    if not location_name and (city or category):
        city_label = city or 'Unknown City'
        type_label = location_type.replace('_', ' ').title() if location_type else 'Venue'
        location_name = f"{type_label} - {city_label}"
    if not location_name:
        return None

    # Double-check in case name/city exists but resolver missed a fuzzy case.
    qs = Location.objects.filter(created_by=user, name__iexact=location_name)
    if city:
        qs = qs.filter(city__iexact=city)
    loc = qs.first()
    if loc:
        return loc

    address = _clip(g('location_address', 'address', 'street', 'street_address'), 300) or 'N/A'

    return Location.objects.create(
        name=_clip(location_name, 200),
        type=location_type,
        status='active',
        address=address,
        city=_clip(city, 100),
        state=_clip(state, 50),
        zip_code=_clip(g('zip', 'zip_code', 'zipcode', 'postal_code'), 10),
        county=_clip(g('county'), 100),
        contact_name=_clip(g('contact_name', 'location_contact', 'manager'), 150),
        contact_title=_clip(g('contact_title', 'title', 'position'), 100),
        phone=_clip(g('location_phone', 'phone', 'telephone', 'phone_number'), 30),
        email=_clip(g('location_email', 'email', 'email_address'), 254),
        website=_clip(g('website_url', 'website', 'web', 'url'), 200),
        created_by=user,
    )


def _normalize_choice(value, choices, default):
    if not value:
        return default
    raw = str(value).strip().lower()
    keys = {k for k, _ in choices}
    if raw in keys:
        return raw
    labels = {str(label).strip().lower(): key for key, label in choices}
    return labels.get(raw, default)


def _parse_date(value):
    if not value:
        return None
    if hasattr(value, 'date'):
        try:
            return value.date()
        except Exception:
            pass

    s = str(value).strip()
    if not s:
        return None

    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%m-%d-%Y', '%d-%m-%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def _parse_time(value):
    if not value:
        return None
    if isinstance(value, time):
        return value
    if hasattr(value, 'time') and not isinstance(value, str):
        try:
            return value.time()
        except Exception:
            pass

    s = str(value).strip()
    if not s:
        return None

    for fmt in ('%H:%M', '%H:%M:%S', '%I:%M %p', '%I:%M%p'):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


def _safe_int(val):
    """Convert to int safely, return None on failure."""
    try:
        return int(float(val)) if val else None
    except (ValueError, TypeError):
        return None


def _safe_decimal(val, default=Decimal('0')):
    if val in (None, ''):
        return default
    try:
        return Decimal(str(val).strip())
    except (InvalidOperation, ValueError, TypeError):
        return default


def _safe_bool(val, default=False):
    if val in (None, ''):
        return default
    raw = str(val).strip().lower()
    if raw in {'1', 'true', 'yes', 'y'}:
        return True
    if raw in {'0', 'false', 'no', 'n'}:
        return False
    return default


def _clip(val, max_len):
    if val is None:
        return ''
    s = str(val).strip()
    if not max_len or len(s) <= max_len:
        return s
    return s[:max_len]


CATEGORY_TO_TYPE = {
    'apartment': 'apartment',
    'apartment complex': 'apartment',
    'apartment building': 'apartment',
    'venue': 'venue',
    'event venue': 'venue',
    'lounge/bar': 'club',
    'lounge': 'lounge',
    'bar': 'bar',
    'club': 'club',
    'night club': 'club',
    'nightclub': 'club',
    'restaurant': 'restaurant',
    'restaurant/bar': 'restaurant',
    'hotel': 'hotel',
    'resort': 'hotel',
    'park': 'park',
    'building': 'building',
}


def _map_category_to_type(category):
    if not category:
        return 'venue'

    raw = str(category).strip().lower()
    if raw in CATEGORY_TO_TYPE:
        return CATEGORY_TO_TYPE[raw]

    # Handle fuzzy matches like "Lounge / Bar", "Apartment (Luxury)"
    normalized = raw.replace('&', '/').replace(' ', '')
    if 'apartment' in raw:
        return 'apartment'
    if 'lounge' in raw and 'bar' in raw:
        return 'club'
    if 'club' in raw or 'night' in raw:
        return 'club'
    if 'bar' in raw:
        return 'bar'
    if 'lounge' in raw:
        return 'lounge'
    if 'restaurant' in raw:
        return 'restaurant'
    if 'venue' in raw:
        return 'venue'
    if 'hotel' in raw or 'resort' in raw:
        return 'hotel'
    if 'park' in raw:
        return 'park'
    if 'building' in raw:
        return 'building'
    return 'other'


# ══════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ══════════════════════════════════════════════════════════════════

@login_required
def api_stats(request):
    return JsonResponse({
        'locations': Location.objects.filter(Q(created_by=request.user) | Q(created_by__isnull=True)).count(),
        'contacts': Contact.objects.filter(Q(created_by=request.user) | Q(created_by__isnull=True)).count(),
    })


@login_required
def api_locations(request):
    locs = Location.objects.filter(created_by=request.user, status='active').values(
        'id', 'name', 'city', 'address', 'phone', 'email', 'type', 'latitude', 'longitude'
    )
    return JsonResponse({'locations': list(locs)})


@login_required
def api_locations_by_type(request):
    loc_type = (request.GET.get('type') or 'all').strip().lower()
    qs = Location.objects.filter(created_by=request.user, status='active').order_by('city', 'name')
    if loc_type != 'all':
        qs = qs.filter(type__iexact=loc_type)

    locations = [
        {
            'id': loc.id,
            'name': loc.name,
            'type': loc.type,
            'city': loc.city,
            'email': loc.email or '',
        }
        for loc in qs
    ]

    type_counts = dict(
        Location.objects.filter(created_by=request.user, status='active')
        .values_list('type')
        .annotate(cnt=Count('id'))
        .values_list('type', 'cnt')
    )
    type_counts = {k.lower(): v for k, v in type_counts.items()}

    return JsonResponse({
        'locations': locations,
        'type_counts': type_counts,
        'total': len(locations),
    })


# ══════════════════════════════════════════════════════════════════
# OTP SETUP PAGE
# ══════════════════════════════════════════════════════════════════

@login_required
def otp_setup(request):
    from django.conf import settings as django_settings
    import os
    from pathlib import Path
    from dotenv import load_dotenv, set_key

    env_path = Path(__file__).resolve().parent.parent / '.env'

    current_provider = getattr(django_settings, 'OTP_PROVIDER', 'console')

    # Read current SMTP settings from environment
    smtp_settings = {
        'host': os.environ.get('EMAIL_HOST', 'smtp.gmail.com'),
        'port': os.environ.get('EMAIL_PORT', '587'),
        'use_tls': os.environ.get('EMAIL_USE_TLS', 'True'),
        'username': os.environ.get('EMAIL_HOST_USER', ''),
        'password': os.environ.get('EMAIL_HOST_PASSWORD', ''),
        'from_email': os.environ.get('DEFAULT_FROM_EMAIL', ''),
    }

    # Handle POST — save settings
    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'save_provider':
            provider = request.POST.get('otp_provider', 'smtp')
            if env_path.exists():
                set_key(str(env_path), 'OTP_PROVIDER', provider)
            current_provider = provider
            messages.success(request, f'OTP provider changed to {provider.upper()}.')

        elif action == 'save_smtp':
            host = request.POST.get('smtp_host', 'smtp.gmail.com').strip()
            port = request.POST.get('smtp_port', '587').strip()
            use_tls = 'True' if request.POST.get('smtp_use_tls') else 'False'
            username = request.POST.get('smtp_username', '').strip()
            password = request.POST.get('smtp_password', '').strip()
            from_email = request.POST.get('smtp_from_email', '').strip()

            if not username or not password:
                messages.error(request, 'Email username and password are required.')
            else:
                if env_path.exists():
                    set_key(str(env_path), 'EMAIL_HOST', host)
                    set_key(str(env_path), 'EMAIL_PORT', port)
                    set_key(str(env_path), 'EMAIL_USE_TLS', use_tls)
                    set_key(str(env_path), 'EMAIL_HOST_USER', username)
                    set_key(str(env_path), 'EMAIL_HOST_PASSWORD', password)
                    set_key(str(env_path), 'DEFAULT_FROM_EMAIL', from_email or f'Event Directory and Logistic <{username}>')
                    set_key(str(env_path), 'OTP_PROVIDER', 'smtp')

                # Update live settings so test works immediately
                django_settings.EMAIL_HOST = host
                django_settings.EMAIL_PORT = int(port)
                django_settings.EMAIL_USE_TLS = use_tls == 'True'
                django_settings.EMAIL_HOST_USER = username
                django_settings.EMAIL_HOST_PASSWORD = password
                django_settings.DEFAULT_FROM_EMAIL = from_email or username
                django_settings.OTP_PROVIDER = 'smtp'
                current_provider = 'smtp'

                smtp_settings = {
                    'host': host, 'port': port, 'use_tls': use_tls,
                    'username': username, 'password': password,
                    'from_email': from_email or username,
                }
                messages.success(request, 'SMTP settings saved successfully.')

        elif action == 'test_otp':
            test_email = request.POST.get('test_email', '').strip()
            if not test_email:
                messages.error(request, 'Enter an email address to send test OTP.')
            else:
                from .otp_service import send_otp, generate_otp
                code = generate_otp()
                from .models import OTPCode
                OTPCode.objects.create(email=test_email, code=code, purpose='test')
                result = send_otp(test_email, code, 'login')
                if result.get('success'):
                    messages.success(request, f'Test OTP sent to {test_email} via {result.get("provider", "unknown")}.')
                else:
                    messages.error(request, f'OTP send failed: {result.get("error", "Unknown error")}')

        return redirect('otp_setup')

    # Check which providers have credentials configured
    smtp_configured = bool(smtp_settings['username'] and smtp_settings['password'])

    return render(request, 'admin_dash/otp_setup.html', {
        'current_provider': current_provider,
        'smtp_settings': smtp_settings,
        'smtp_configured': smtp_configured,
    })


@login_required
def api_dedupe(request):
    from django.db import IntegrityError
    dedupe_type = request.GET.get('type', 'all').strip().lower()
    results = {'locations_merged': 0, 'contacts_merged': 0, 'locations_deleted': 0, 'contacts_deleted': 0, 'errors': []}
    user = request.user

    try:
        if dedupe_type in ('all', 'location'):
            by_name_city = {}
            for loc in Location.objects.filter(created_by=user).order_by('id'):
                key = (loc.name.strip().lower(), loc.city.strip().lower())
                by_name_city.setdefault(key, []).append(loc)

            for group in by_name_city.values():
                if len(group) < 2:
                    continue
                primary = group[0]
                for dup in group[1:]:
                    Contact.objects.filter(location=dup, created_by=user).update(location=primary)
                    dup.delete()
                    results['locations_deleted'] += 1

        if dedupe_type in ('all', 'contact'):
            contacts = list(Contact.objects.filter(created_by=user).order_by('id'))
            merged_contacts = set()
            deleted_ids = []

            for i, contact in enumerate(contacts):
                if contact.id in merged_contacts:
                    continue
                norm_email = (contact.email or '').strip().lower()
                norm_phone = _normalize_phone(contact.phone)
                canonical = _canonical_contact_name(contact.first_name, contact.last_name)

                for other in contacts[i+1:]:
                    if other.id in merged_contacts or other.id in deleted_ids:
                        continue
                    is_dup = False
                    other_email = (other.email or '').strip().lower()
                    other_phone = _normalize_phone(other.phone)
                    other_canonical = _canonical_contact_name(other.first_name, other.last_name)

                    if norm_email and other_email and norm_email == other_email:
                        is_dup = True
                    elif norm_phone and other_phone and norm_phone == other_phone:
                        is_dup = True
                    elif canonical and other_canonical and canonical == other_canonical:
                        is_dup = True

                    if is_dup:
                        for field in ('phone', 'city', 'state', 'zip_code', 'age', 'gender', 'location'):
                            if not getattr(contact, field) and getattr(other, field):
                                setattr(contact, field, getattr(other, field))
                        if other.notes:
                            if not contact.notes:
                                contact.notes = other.notes
                            elif other.notes not in contact.notes:
                                contact.notes = f"{contact.notes}\n{other.notes}"

                        new_email = None
                        if contact.email and other.email:
                            if other.email != contact.email:
                                if not Contact.objects.filter(email__iexact=other.email).exclude(pk=contact.pk).exists():
                                    new_email = other.email
                        elif not contact.email and other.email:
                            if not Contact.objects.filter(email__iexact=other.email).exists():
                                new_email = other.email
                        
                        if new_email:
                            contact.email = new_email
                        
                        try:
                            contact.save()
                            other.delete()
                            deleted_ids.append(other.id)
                            merged_contacts.add(other.id)
                            results['contacts_deleted'] += 1
                        except Exception as e:
                            error_msg = str(e).lower()
                            if 'duplicate' in error_msg and 'email' in error_msg:
                                results['errors'].append(f"Skipped duplicate email {other.email} during merge")
                            else:
                                results['errors'].append(f"Error merging contact {other.id}: {str(e)}")
                            continue

            results['contacts_merged'] = results['contacts_deleted']

    except IntegrityError as e:
        results['errors'].append(f"IntegrityError: {str(e)}")
        return JsonResponse({'status': 'error', 'message': 'Database integrity error', 'details': results}, status=500)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e), 'details': results}, status=500)

    return JsonResponse({'status': 'success', 'message': 'Contact processed successfully', 'details': results})




# ══════════════════════════════════════════════════════════════════
# PROMOTION HUB
# ══════════════════════════════════════════════════════════════════

@login_required
def promotion_hub(request):
    from .models import EmailBlast, EmailTemplate, SMSBlast, SocialPost
    user = request.user
    recent_blasts   = EmailBlast.objects.filter(created_by=user)[:5]
    recent_posts    = SocialPost.objects.filter(created_by=user)[:5]
    recent_sms      = SMSBlast.objects.filter(created_by=user)[:5]
    locations       = Location.objects.filter(Q(created_by=user) | Q(created_by__isnull=True), status='active')[:30]

    # Aggregate recent activity across all channels (newest 10)
    activity = []
    for b in EmailBlast.objects.filter(created_by=user)[:4]:
        activity.append({'channel':'Email', 'icon':'envelope-fill', 'color':'#f87171',
                         'action':'Blast', 'title': b.subject, 'status': b.status,
                         'date': b.created_at})
    for p in SocialPost.objects.filter(created_by=user)[:3]:
        activity.append({'channel': p.platform.title(), 'icon':'share-fill', 'color':'#818cf8',
                         'action':'Post', 'title': p.caption[:50], 'status': p.status,
                         'date': p.created_at})
    for s in SMSBlast.objects.filter(created_by=user)[:3]:
        activity.append({'channel':'SMS', 'icon':'chat-dots-fill', 'color':'#34d399',
                         'action':'Blast', 'title': s.message[:50], 'status': s.status,
                         'date': s.created_at})
    activity.sort(key=lambda x: x['date'], reverse=True)

    stats = {
        'email_blasts':  EmailBlast.objects.filter(created_by=user).count(),
        'sent_blasts':   EmailBlast.objects.filter(created_by=user, status='sent').count(),
        'subscribed':    Contact.objects.filter(Q(created_by=user) | Q(created_by__isnull=True), is_subscribed=True).count(),
        'template_count': EmailTemplate.objects.filter(created_by=user).count(),
        'sms_sent':      SMSBlast.objects.filter(created_by=user, status='sent').aggregate(
                             t=Sum('total_sent'))['t'] or 0,
        'social_posts':  SocialPost.objects.filter(created_by=user, status='posted').count(),
    }
    return render(request, 'admin_dash/promotion_hub.html', {
        'recent_blasts': recent_blasts,
        'recent_posts':  recent_posts,
        'recent_sms':    recent_sms,
        'locations':     locations,
        'stats':         stats,
        'activity':      activity[:10],
    })


# ══════════════════════════════════════════════════════════════════
# SOCIAL MEDIA
# ══════════════════════════════════════════════════════════════════

@login_required
def social_media(request):
    from .models import SocialPlatformConfig, SocialPost
    from .forms  import SocialPlatformConfigForm, SocialPostForm

    # Save platform config (AJAX-friendly POST)
    if request.method == 'POST' and 'save_config' in request.POST:
        platform = request.POST.get('platform')
        account_id = request.POST.get('account_id')
        instance = SocialPlatformConfig.objects.filter(created_by=request.user, pk=account_id).first() if account_id else None
        form = SocialPlatformConfigForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.platform = platform
            obj.account_name = form.cleaned_data.get('account_name', '') or ''
            obj.is_connected = bool(obj.access_token.strip())
            obj.created_by = request.user
            if obj.is_primary:
                # enforce a single primary per platform
                SocialPlatformConfig.objects.filter(created_by=request.user, platform=platform).exclude(pk=obj.pk).update(is_primary=False)
            obj.save()
            messages.success(request, f'{platform.title()} account {"updated" if instance else "saved"}.')
        else:
            messages.error(request, f'Invalid configuration: {form.errors}')
        return redirect('social_media')

    # Delete platform config
    if request.method == 'POST' and 'delete_config' in request.POST:
        config_id = request.POST.get('config_id')
        cfg = get_object_or_404(SocialPlatformConfig.objects.filter(created_by=request.user), pk=config_id)
        cfg.delete()
        messages.success(request, 'Account deleted.')
        return redirect('social_media')

    # Edit platform config (load data into modal)
    if request.method == 'POST' and 'edit_config' in request.POST:
        config_id = request.POST.get('config_id')
        cfg = get_object_or_404(SocialPlatformConfig.objects.filter(created_by=request.user), pk=config_id)
        return JsonResponse({
            'id': cfg.pk,
            'platform': cfg.platform,
            'account_name': cfg.account_name,
            'access_token': cfg.access_token,
            'app_id': cfg.app_id,
            'app_secret': cfg.app_secret,
            'extra_field': cfg.extra_field,
            'is_connected': cfg.is_connected,
        })

    # Create post
    if request.method == 'POST' and 'create_post' in request.POST:
        import sys
        print("=== CREATE POST START ===", file=sys.stderr)
        from .models import SocialPost, Location as Loc
        from .social_service import (
            post_to_facebook, post_to_facebook_photo, post_to_facebook_video,
            post_to_twitter, post_to_linkedin, post_to_instagram,
            post_to_threads, post_to_pinterest,
            get_public_media_url, ensure_public_https_url, verify_public_url,
            upload_to_cloudinary
        )

        platforms_sel = request.POST.getlist('platforms')
        caption       = request.POST.get('caption', '').strip()
        loc_id        = request.POST.get('location_id')
        schedule_str  = request.POST.get('schedule_at', '').strip()
        media_type    = request.POST.get('media_type', 'text')
        media_url     = request.POST.get('media_url', '')
        link_url      = request.POST.get('link_url', '').strip()

        print(f"DEBUG: platforms={platforms_sel}, media_type={media_type}, caption_len={len(caption)}", file=sys.stderr)
        print(f"DEBUG: FILES={list(request.FILES.keys())}", file=sys.stderr)

        # Block text-only posts to Instagram
        if 'instagram' in platforms_sel and media_type == 'text':
            messages.error(request, 'Instagram requires an image or video. Please select Image or Video, or deselect Instagram.')
            return redirect('social_media')

        # Upload media file once
        uploaded_file_path = None
        if request.FILES.get('media_file'):
            media_file = request.FILES['media_file']
            from django.conf import settings
            import os
            import uuid
            ext = os.path.splitext(media_file.name)[1].lower()
            filename = f"{uuid.uuid4().hex}{ext}"
            upload_dir = os.path.join(settings.MEDIA_ROOT, 'social')
            os.makedirs(upload_dir, exist_ok=True)
            filepath = os.path.join(upload_dir, filename)
            with open(filepath, 'wb+') as dest:
                for chunk in media_file.chunks():
                    dest.write(chunk)
            uploaded_file_path = filepath
            media_url = get_public_media_url(f'social/{filename}')
            print(f"DEBUG: File saved to {filepath}, size={os.path.getsize(filepath)}", file=sys.stderr)
            print(f"DEBUG: media_url={media_url}", file=sys.stderr)
            ok, reason = verify_public_url(media_url)
            print(f"DEBUG: URL verify={ok}, reason={reason}", file=sys.stderr)
        else:
            print("DEBUG: No media_file in request.FILES", file=sys.stderr)

        # If media type requires a file or URL, ensure we have one
        if media_type in ['image', 'video'] and not (uploaded_file_path or media_url):
            messages.error(request, f'{media_type.title()} selected but no file or URL provided.')
            return redirect('social_media')

        if not caption:
            messages.error(request, 'Caption is required.')
            return redirect('social_media')

        loc = None
        if loc_id:
            loc = Loc.objects.filter(created_by=request.user, pk=loc_id).first()

        schedule_dt = None
        if schedule_str:
            try:
                from django.utils.dateparse import parse_datetime
                schedule_dt = parse_datetime(schedule_str)
            except Exception:
                pass

        immediate_post = not schedule_dt
        created = 0
        failed = 0
        no_config_platforms = []

        # Validate all platform configs before posting
        for plat in platforms_sel:
            configs = SocialPlatformConfig.objects.filter(platform=plat, is_connected=True)
            if not configs.exists():
                platform_names = {'facebook': 'Facebook', 'instagram': 'Instagram', 'twitter': 'X (Twitter)', 
                                 'linkedin': 'LinkedIn', 'threads': 'Threads', 'pinterest': 'Pinterest'}
                no_config_platforms.append(platform_names.get(plat, plat.title()))
            elif plat == 'instagram' and media_type == 'text':
                no_config_platforms.append('Instagram (text posts not supported)')

        if no_config_platforms:
            messages.error(request, f'Cannot post to: {", ".join(no_config_platforms)}. Please configure these platforms or deselect them.')
            return redirect('social_media')

        # Post to ALL selected platforms
        for plat in platforms_sel:
            configs = SocialPlatformConfig.objects.filter(platform=plat, is_connected=True)
            print(f"DEBUG: Platform={plat}, configs_count={configs.count()}", file=sys.stderr)
            platform_posts_created = 0
            
            for cfg in configs:
                print(f"DEBUG: cfg={cfg.account_name}, extra_field={cfg.extra_field}, token_len={len(cfg.access_token) if cfg.access_token else 0}", file=sys.stderr)
                post_status = 'scheduled' if schedule_dt else 'draft'
                post_url = ''
                error_msg = ''
                # Explicit link supplied by user (optional)
                link = link_url or None

                if immediate_post:
                    try:
                        if plat == 'facebook':
                            if cfg.extra_field:
                                if media_type == 'image' and (uploaded_file_path or media_url):
                                    result = post_to_facebook_photo(cfg.extra_field, cfg.access_token, caption, media_url, file_path=uploaded_file_path)
                                elif media_type == 'video' and (uploaded_file_path or media_url):
                                    result = post_to_facebook_video(cfg.extra_field, cfg.access_token, caption, media_url, file_path=uploaded_file_path)
                                else:
                                    result = post_to_facebook(cfg.extra_field, cfg.access_token, caption, link)
                            else:
                                result = {'success': False, 'error': 'Page ID not configured'}

                        elif plat == 'instagram':
                            if cfg.extra_field:
                                # Instagram needs a public HTTPS URL
                                print(f"[DEBUG IG] media_url from upload = {media_url}", file=sys.stderr)
                                print(f"[DEBUG IG] uploaded_file_path    = {uploaded_file_path}", file=sys.stderr)

                                ig_url = media_url
                                local_file = uploaded_file_path

                                # Strategy 1: Upload to Cloudinary (permanent public URL)
                                if uploaded_file_path:
                                    cloud_url, cloud_err = upload_to_cloudinary(uploaded_file_path)
                                    if cloud_url:
                                        ig_url = cloud_url
                                        local_file = None  # Clear local path - don't pass to post_to_instagram
                                        print(f"[DEBUG IG] Cloudinary OK: {ig_url}", file=sys.stderr)
                                    else:
                                        print(f"[DEBUG IG] Cloudinary FAILED: {cloud_err}", file=sys.stderr)
                                        # Strategy 2: Rewrite localhost URL to ngrok/public URL
                                        ig_url = ensure_public_https_url(media_url) if media_url else media_url
                                else:
                                    # No local file — rewrite URL if needed
                                    ig_url = ensure_public_https_url(media_url) if media_url else media_url

                                print(f"[DEBUG IG] FINAL URL for Instagram = {ig_url}", file=sys.stderr)
                                print(f"[DEBUG IG] starts with https://    = {ig_url.startswith('https://') if ig_url else False}", file=sys.stderr)

                                if media_type == 'image' and ig_url:
                                    result = post_to_instagram(cfg.access_token, cfg.extra_field, caption, image_url=ig_url, file_path=local_file)
                                elif media_type == 'video' and ig_url:
                                    result = post_to_instagram(cfg.access_token, cfg.extra_field, caption, video_url=ig_url, file_path=local_file)
                                else:
                                    result = {'success': False, 'error': 'Image or video required'}
                            else:
                                result = {'success': False, 'error': 'Business Account ID not configured'}

                        elif plat == 'twitter':
                            if cfg.extra_field:
                                result = post_to_twitter(cfg.app_id, cfg.app_secret, cfg.access_token, cfg.extra_field, caption, file_path=uploaded_file_path)
                            else:
                                result = {'success': False, 'error': 'Access Token Secret not configured'}

                        elif plat == 'linkedin':
                            if cfg.extra_field:
                                result = post_to_linkedin(cfg.access_token, cfg.extra_field, caption, link)
                            else:
                                result = {'success': False, 'error': 'Organization URN not configured'}

                        elif plat == 'threads':
                            result = post_to_threads(cfg.access_token, cfg.app_id, caption)

                        elif plat == 'pinterest':
                            if cfg.extra_field:
                                result = post_to_pinterest(cfg.access_token, cfg.extra_field, caption,
                                                           image_url=media_url if media_type == 'image' else None,
                                                           link=link)
                            else:
                                result = {'success': False, 'error': 'Board ID not configured'}

                        else:
                            result = {'success': False, 'error': 'Platform not supported'}

                        if result.get('success'):
                            post_status = 'posted'
                            post_url = result.get('post_url', '')
                            print(f"DEBUG: {plat} POSTED to {post_url}", file=sys.stderr)
                        else:
                            post_status = 'failed'
                            error_msg = result.get('error', 'Unknown error')
                            failed += 1
                            print(f"DEBUG: {plat} FAILED: {error_msg}", file=sys.stderr)

                    except Exception as e:
                        post_status = 'failed'
                        error_msg = str(e)
                        failed += 1
                        print(f"DEBUG: {plat} EXCEPTION: {error_msg}", file=sys.stderr)

                # Create post record for each account
                SocialPost.objects.create(
                    platform=plat,
                    account_name=cfg.account_name,
                    caption=caption,
                    location=loc,
                    media_type=media_type,
                    media_url=media_url,
                    link_url=link_url,
                    status=post_status,
                    scheduled_at=schedule_dt,
                    posted_at=timezone.now() if post_status == 'posted' else None,
                    post_url=post_url,
                    error_message=error_msg,
                    created_by=request.user,
                )
                platform_posts_created += 1

            created += platform_posts_created

        messages.success(request, f'Post created for {created} platform(s).' + (f' {failed} failed.' if failed > 0 else ''))
        return redirect('social_media')

    PLATFORMS = [
        {'key':'facebook',  'name':'Facebook Pages',  'icon':'facebook',   'color':'#1877f2',
         'bg':'rgba(24,119,242,.15)',  'desc':'Post events to your Facebook Page via Graph API.',
         'help':'Requires Facebook Developer App + Page Access Token. Free tier available.',
         'fields':['App ID','App Secret','Access Token','Page ID']},
        {'key':'instagram', 'name':'Instagram',       'icon':'instagram',  'color':'#e1306c',
         'bg':'rgba(225,48,108,.15)', 'desc':'Post event images via Instagram Graph API.',
         'help':'Requires Meta Business Account linked to Instagram Professional Account.',
         'fields':['App ID','App Secret','Access Token','Instagram Business Account ID']},
        {'key':'twitter',   'name':'X (Twitter)',     'icon':'twitter-x',  'color':'#e7e9ea',
         'bg':'rgba(255,255,255,.06)','desc':'Tweet event announcements and updates.',
         'help':'Requires X Developer Account. Free Basic tier allows read/write.',
         'fields':['API Key','API Secret','Access Token','Access Token Secret']},
        {'key':'linkedin',  'name':'LinkedIn Pages',  'icon':'linkedin',   'color':'#0a66c2',
         'bg':'rgba(10,102,194,.15)', 'desc':'Share events to your LinkedIn company page.',
         'help':'Requires LinkedIn Developer App + w_member_social permission.',
         'fields':['Client ID','Client Secret','Access Token','Organization URN']},
        {'key':'threads',   'name':'Threads',         'icon':'threads',    'color':'#e7e9ea',
         'bg':'rgba(255,255,255,.06)','desc':'Post to Meta Threads via the new Threads API.',
         'help':'Available via Meta Developer Portal. Requires Instagram account linked.',
         'fields':['App ID','App Secret','Access Token','']},
        {'key':'pinterest', 'name':'Pinterest',        'icon':'pinterest',  'color':'#e60023',
         'bg':'rgba(230,0,35,.12)',   'desc':'Pin event images and links to your Pinterest boards.',
         'help':'Pinterest API v5 – requires a Pinterest Business account.',
         'fields':['App ID','App Secret','Access Token','Board ID']},
    ]

    # Group all accounts by platform
    all_accounts = SocialPlatformConfig.objects.filter(created_by=request.user).order_by('-is_primary', 'platform', '-created_at')
    accounts_by_platform = {}
    for cfg in all_accounts:
        if cfg.platform not in accounts_by_platform:
            accounts_by_platform[cfg.platform] = []
        accounts_by_platform[cfg.platform].append(cfg)
    
    # Check if any platform has connected accounts
    configs = {c.platform: c for c in SocialPlatformConfig.objects.filter(created_by=request.user, is_connected=True)}
    for p in PLATFORMS:
        p['has_connected'] = p['key'] in configs
        p['accounts'] = accounts_by_platform.get(p['key'], [])

    posts     = SocialPost.objects.filter(created_by=request.user).select_related('location')[:50]
    locations = Location.objects.filter(Q(created_by=request.user) | Q(created_by__isnull=True), status='active')[:50]
    cities    = Contact.objects.filter(Q(created_by=request.user) | Q(created_by__isnull=True)).values_list('city', flat=True).distinct().exclude(city='').order_by('city')

    return render(request, 'admin_dash/social_media.html', {
        'platforms': PLATFORMS,
        'accounts_by_platform': accounts_by_platform,
        'all_accounts': all_accounts,
        'posts':     posts,
        'locations': locations,
        'cities':    cities,
        'now':       timezone.now(),
    })


@login_required
def social_edit_account(request):
    from django.http import JsonResponse
    from .models import SocialPlatformConfig
    
    if request.method == 'POST' and 'edit_config' in request.POST:
        config_id = request.POST.get('config_id')
        cfg = get_object_or_404(SocialPlatformConfig.objects.filter(created_by=request.user), pk=config_id)
        return JsonResponse({
            'id': cfg.pk,
            'platform': cfg.platform,
            'account_name': cfg.account_name,
            'access_token': cfg.access_token,
            'app_id': cfg.app_id,
            'app_secret': cfg.app_secret,
            'extra_field': cfg.extra_field,
            'is_connected': cfg.is_connected,
        })
    return JsonResponse({'error': 'Invalid request'}, status=400)


@login_required
def social_post_delete(request, pk):
    from .models import SocialPost
    post = get_object_or_404(SocialPost.objects.filter(created_by=request.user), pk=pk)
    post.delete()
    messages.success(request, 'Post deleted.')
    return redirect('social_media')


@login_required
def social_post_now(request, pk):
    import os
    from django.http import JsonResponse
    from django.conf import settings
    from .models import SocialPost, SocialPlatformConfig
    from .social_service import (
        post_to_facebook, post_to_facebook_photo, post_to_facebook_video,
        post_to_twitter, post_to_linkedin, post_to_instagram,
        post_to_threads, post_to_pinterest,
        get_public_media_url, ensure_public_https_url, verify_public_url,
        upload_to_cloudinary
    )
    
    try:
        post = get_object_or_404(SocialPost.objects.filter(created_by=request.user), pk=pk)
        
        if post.status not in ['scheduled', 'draft']:
            return JsonResponse({'success': False, 'error': 'Post cannot be sent. Status: ' + post.status})
        
        # Block text-only posts to Instagram
        if post.platform == 'instagram' and post.media_type == 'text':
            return JsonResponse({'success': False, 'error': 'Instagram posts require an image or video. Only photo or video can be accepted as media type.'})
        
        config = SocialPlatformConfig.objects.filter(created_by=request.user, platform=post.platform, is_connected=True).first()
        if not config or not config.access_token:
            return JsonResponse({'success': False, 'error': f'{post.platform.title()} not connected'})
        
        # Build local file path from media_url if it's a local upload
        local_file_path = None
        if post.media_url and '/media/' in post.media_url:
            try:
                from django.conf import settings
                rel_path = post.media_url.split('/media/')[-1].split('?')[0]
                local_file_path = os.path.join(settings.MEDIA_ROOT, rel_path)
                if not os.path.isfile(local_file_path):
                    local_file_path = None
            except Exception:
                pass

        # Do not auto-attach location website links to avoid unintended previews.
        link = post.link_url or None
        result = {'success': False, 'post_url': None, 'error': 'Unknown platform'}

        if post.platform == 'facebook':
            if not config.extra_field:
                return JsonResponse({'success': False, 'error': 'Facebook Page ID not configured'})
            if post.media_type == 'image' and (post.media_url or local_file_path):
                result = post_to_facebook_photo(config.extra_field, config.access_token, post.caption, post.media_url, file_path=local_file_path)
            elif post.media_type == 'video' and (post.media_url or local_file_path):
                result = post_to_facebook_video(config.extra_field, config.access_token, post.caption, post.media_url, file_path=local_file_path)
            else:
                result = post_to_facebook(config.extra_field, config.access_token, post.caption, link)

        elif post.platform == 'twitter':
            if not config.extra_field:
                return JsonResponse({'success': False, 'error': 'Twitter Access Token Secret not configured'})
            result = post_to_twitter(config.app_id, config.app_secret, config.access_token, config.extra_field, post.caption, file_path=local_file_path)

        elif post.platform == 'linkedin':
            if not config.extra_field:
                return JsonResponse({'success': False, 'error': 'LinkedIn Organization URN not configured'})
            result = post_to_linkedin(config.access_token, config.extra_field, post.caption, link)

        elif post.platform == 'instagram':
            if not config.extra_field:
                return JsonResponse({'success': False, 'error': 'Instagram Business Account ID not configured'})
            print(f"[DEBUG IG NOW] post.media_url    = {post.media_url}", flush=True)
            print(f"[DEBUG IG NOW] local_file_path   = {local_file_path}", flush=True)
            ig_url = post.media_url
            local_file = local_file_path
            # Strategy 1: Upload to Cloudinary
            if local_file_path:
                cloud_url, cloud_err = upload_to_cloudinary(local_file_path)
                if cloud_url:
                    ig_url = cloud_url
                    local_file = None  # Clear local path - don't pass to post_to_instagram
                    print(f"[DEBUG IG NOW] Cloudinary OK: {ig_url}", flush=True)
                else:
                    print(f"[DEBUG IG NOW] Cloudinary FAILED: {cloud_err}", flush=True)
                    ig_url = ensure_public_https_url(post.media_url) if post.media_url else post.media_url
            else:
                ig_url = ensure_public_https_url(post.media_url) if post.media_url else post.media_url
            print(f"[DEBUG IG NOW] FINAL URL = {ig_url}", flush=True)
            print(f"[DEBUG IG NOW] https:// = {ig_url.startswith('https://') if ig_url else False}", flush=True)
            if post.media_type == 'image' and ig_url:
                result = post_to_instagram(config.access_token, config.extra_field, post.caption, image_url=ig_url, file_path=local_file)
            elif post.media_type == 'video' and ig_url:
                result = post_to_instagram(config.access_token, config.extra_field, post.caption, video_url=ig_url, file_path=local_file)
            else:
                return JsonResponse({'success': False, 'error': 'Instagram requires an image or video URL'})

        elif post.platform == 'threads':
            result = post_to_threads(config.access_token, config.app_id, post.caption)

        elif post.platform == 'pinterest':
            if not config.extra_field:
                return JsonResponse({'success': False, 'error': 'Pinterest Board ID not configured'})
            result = post_to_pinterest(config.access_token, config.extra_field, post.caption,
                                        image_url=post.media_url if post.media_type == 'image' else None,
                                        link=post.link_url or None)
        
        else:
            return JsonResponse({'success': False, 'error': f'Platform {post.platform} not supported'})
        
        if result['success']:
            post.status = 'posted'
            post.posted_at = timezone.now()
            post.post_url = result['post_url']
            post.scheduled_at = None
            post.save()
            return JsonResponse({'success': True})
        else:
            post.status = 'failed'
            post.error_message = result['error']
            post.save()
            return JsonResponse({'success': False, 'error': result['error']})
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def upload_image(request):
    from django.http import JsonResponse
    from django.conf import settings
    import os
    import uuid
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'})
    
    if 'file' not in request.FILES:
        return JsonResponse({'success': False, 'error': 'No file provided'})
    
    file = request.FILES['file']
    
    allowed_types = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
    if file.content_type not in allowed_types:
        return JsonResponse({'success': False, 'error': 'Invalid file type. Use JPEG, PNG, GIF, or WebP'})
    
    max_size = 10 * 1024 * 1024
    if file.size > max_size:
        return JsonResponse({'success': False, 'error': 'File too large. Max 10MB'})
    
    ext = file.name.split('.')[-1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    upload_dir = os.path.join(settings.MEDIA_ROOT, 'social_uploads')
    os.makedirs(upload_dir, exist_ok=True)
    
    filepath = os.path.join(upload_dir, filename)
    with open(filepath, 'wb+') as dest:
        for chunk in file.chunks():
            dest.write(chunk)
    
    url = request.build_absolute_uri(settings.MEDIA_URL + 'social_uploads/' + filename)
    return JsonResponse({'success': True, 'url': url, 'filename': filename})


@login_required
def social_post_edit(request, pk):
    from django.http import JsonResponse
    from .models import SocialPost, Location
    
    post = get_object_or_404(SocialPost.objects.filter(created_by=request.user), pk=pk)
    
    if request.method == 'POST':
        caption = request.POST.get('caption', '').strip()
        media_type = request.POST.get('media_type', post.media_type)
        media_url = request.POST.get('media_url', post.media_url)
        link_url = request.POST.get('link_url', getattr(post, 'link_url', ''))
        loc_id = request.POST.get('location_id')
        
        if request.FILES.get('media_file'):
            media_file = request.FILES['media_file']
            import os
            import uuid
            from django.conf import settings
            from .social_service import get_public_media_url
            ext = os.path.splitext(media_file.name)[1].lower()
            filename = f"{uuid.uuid4().hex}{ext}"
            upload_dir = os.path.join(settings.MEDIA_ROOT, 'social')
            os.makedirs(upload_dir, exist_ok=True)
            filepath = os.path.join(upload_dir, filename)
            with open(filepath, 'wb+') as dest:
                for chunk in media_file.chunks():
                    dest.write(chunk)
            media_url = get_public_media_url(f'social/{filename}')
        
        post.caption = caption
        post.media_type = media_type
        post.media_url = media_url
        post.link_url = link_url
        if loc_id:
            post.location = Location.objects.filter(created_by=request.user, pk=loc_id).first()
        post.save()
        
        messages.success(request, 'Post updated.')
        return redirect('social_media')
    
    locations = Location.objects.filter(status='active')[:50]
    return render(request, 'admin_dash/social_post_edit.html', {'post': post, 'locations': locations})


# ══════════════════════════════════════════════════════════════════
# EMAIL TEMPLATES
# ══════════════════════════════════════════════════════════════════

@login_required
def email_templates(request):
    from .models import EmailTemplate
    from .forms  import EmailTemplateForm

    if request.method == 'POST':
        tpl_id = request.POST.get('template_id')
        if tpl_id:
            tpl = get_object_or_404(EmailTemplate.objects.filter(created_by=request.user), pk=tpl_id)
            form = EmailTemplateForm(request.POST, instance=tpl)
        else:
            form = EmailTemplateForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.created_by = request.user
            obj.save()
            messages.success(request, f'Template "{obj.name}" saved.')
            return redirect('email_templates')
        else:
            messages.error(request, f'Errors: {form.errors}')

    form      = EmailTemplateForm()
    templates = EmailTemplate.objects.filter(created_by=request.user)
    VARS = ['{Venue_Name}','{Event_Name}','{Date}','{Time}','{City}',
            '{First_Name}','{Last_Name}','{Organizer_Name}',
            '{Website_Link}','{Event_Link}','{Address}','{Phone}']

    # Starter templates for first-time setup
    starters = [
        {'name':'Venue Partnership Outreach','category':'outreach',
         'subject':"We'd Love to Feature {Venue_Name}!",
         'body':"Hello {Venue_Name},\n\nWe would love to feature {Event_Name} happening at your venue on {Date}.\n\nOur platform connects thousands ofEvent Directory and Logistic-goers with amazing venues like yours in {City}.\n\nTo learn more or get your venue featured, please visit {Website_Link}.\n\nBest regards,\n{Organizer_Name}\nEvent Directory and Logistic"},
        {'name':'Event Announcement Blast','category':'announcement',
         'subject':"🌴 {Event_Name} is Happening in {City}!",
         'body':"Hi {First_Name}!\n\nWe're thrilled to announce {Event_Name} on {Date} at {Venue_Name}.\n\nSeats are limited — secure yours now:\n{Event_Link}\n\nSee you there!\nEvent Directory and Logistic Team"},
        {'name':'Event Reminder','category':'reminder',
         'subject':"Reminder: {Event_Name} is Tomorrow!",
         'body':"Hey {First_Name}!\n\nJust a friendly reminder that {Event_Name} is TOMORROW at {Venue_Name}.\n\nDoors open at {Time}. We can't wait to see you!\n\n{Event_Link}\n\nEvent Directory and Logistic"},
        {'name':'Post-Event Follow-up','category':'followup',
         'subject':"Thank You for Attending {Event_Name}!",
         'body':"Dear {First_Name},\n\nThank you for joining us at {Event_Name} in {City}! We hope you had an amazing time.\n\nStay connected and watch for our next event announcement.\n\nEvent Directory and Logistic\n{Website_Link}"},
    ]

    return render(request, 'admin_dash/email_templates.html', {
        'form':      form,
        'templates': templates,
        'starters':  starters,
        'vars':      VARS,
    })


@login_required
def email_template_detail(request, pk):
    from .models import EmailTemplate
    from .forms  import EmailTemplateForm
    tpl = get_object_or_404(EmailTemplate.objects.filter(created_by=request.user), pk=pk)
    if request.method == 'POST':
        form = EmailTemplateForm(request.POST, instance=tpl)
        if form.is_valid():
            form.save()
            messages.success(request, 'Template updated.')
            return redirect('email_templates')
    else:
        form = EmailTemplateForm(instance=tpl)
    return JsonResponse({'id': tpl.pk, 'name': tpl.name, 'category': tpl.category,
                         'subject': tpl.subject, 'body': tpl.body, 'from_name': tpl.from_name})


@login_required
def email_template_delete(request, pk):
    from .models import EmailTemplate
    tpl = get_object_or_404(EmailTemplate.objects.filter(created_by=request.user), pk=pk)
    name = tpl.name
    tpl.delete()
    messages.success(request, f'Template "{name}" deleted.')
    return redirect('email_templates')


@login_required
def email_template_use(request, pk):
    """Redirect to email compose with template pre-filled."""
    return redirect(f"/dashboard/email/compose/?template_id={pk}")


# ══════════════════════════════════════════════════════════════════
# SMS BLAST
# ══════════════════════════════════════════════════════════════════

@login_required
def sms_blast(request):
    from .models import SMSConfig, SMSBlast
    from .forms  import SMSConfigForm, SMSBlastForm

    # Save provider config
    if request.method == 'POST' and 'save_sms_config' in request.POST:
        provider = request.POST.get('provider')
        cfg, _ = SMSConfig.objects.get_or_create(provider=provider)
        form = SMSConfigForm(request.POST, instance=cfg)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.provider = provider
            obj.save()
            messages.success(request, f'{provider.title()} SMS config saved.')
        else:
            messages.error(request, f'Config errors: {form.errors}')
        return redirect('sms_blast')

    # Send / schedule blast
    if request.method == 'POST' and 'send_sms' in request.POST:
        form = SMSBlastForm(request.POST)
        if form.is_valid():
            blast = form.save(commit=False)
            blast.created_by = request.user
            schedule_str = request.POST.get('scheduled_at', '').strip()
            if schedule_str:
                try:
                    from django.utils.dateparse import parse_datetime
                    blast.scheduled_at = parse_datetime(schedule_str)
                    blast.status = 'scheduled'
                except Exception:
                    pass
            else:
                blast.status = 'draft'
            blast.save()

            # Attempt immediate send if provider configured and no schedule
            active_cfg = SMSConfig.objects.filter(is_active=True).first()
            if active_cfg and not blast.scheduled_at:
                def _missing_fields(cfg):
                    required = {
                        'twilio': ['api_key', 'api_secret', 'from_number'],
                        'textbelt': [],
                        'vonage': ['api_key', 'api_secret'],
                        'plivo': ['api_key', 'api_secret', 'from_number'],
                    }
                    return [f for f in required.get(cfg.provider, []) if not getattr(cfg, f)]

                missing = _missing_fields(active_cfg)
                if missing:
                    blast.status = 'failed'
                    blast.error_message = f"{active_cfg.provider.title()} config missing: {', '.join(missing)}"
                    blast.save(update_fields=['status', 'error_message'])
                    messages.error(request, blast.error_message)
                else:
                    sent, failed, last_error = _send_sms_blast(blast, active_cfg)
                    blast.total_sent   = sent
                    blast.total_failed = failed
                    blast.status       = 'sent' if sent > 0 else 'failed'
                    blast.sent_at      = timezone.now()
                    if last_error:
                        blast.error_message = last_error
                    blast.save()
                    if sent:
                        messages.success(request, f'SMS blast sent: {sent} delivered, {failed} failed.')
                    else:
                        err_msg = last_error or "Unknown error"
                        messages.error(request, f'SMS blast failed: {err_msg}')
            else:
                messages.success(request, 'SMS blast saved. Configure a provider to send.')
        else:
            messages.error(request, f'Form errors: {form.errors}')
        return redirect('sms_blast')

    sms_configs = {c.provider: c for c in SMSConfig.objects.all()}
    blasts      = SMSBlast.objects.filter(created_by=request.user)
    blast_form  = SMSBlastForm()

    PROVIDERS = [
        {'key':'twilio',   'name':'Twilio',         'note':'$15 free trial credit. Most reliable.',
         'link':'https://www.twilio.com/try-twilio',
         'fields':[('api_key','Account SID'),('api_secret','Auth Token'),('from_number','Twilio Phone Number (+1...)')]},
        {'key':'textbelt', 'name':'Textbelt',       'note':'1 free SMS/day without key. Paid from $5.',
         'link':'https://textbelt.com',
         'fields':[('api_key','API Key (use "textbelt" for 1 free/day)')]},
        {'key':'vonage',   'name':'Vonage (Nexmo)', 'note':'Use numeric number only. Alphanumeric sender (like "MyBrand") does NOT work in India and many countries.',
         'link':'https://www.vonage.com/communications-apis/sms/',
         'fields':[('api_key','API Key'),('api_secret','API Secret'),('from_number','Vonage Virtual Number (e.g. 12025551234)')]},
        {'key':'plivo',    'name':'Plivo',          'note':'Free trial: 200 messages.',
         'link':'https://www.plivo.com/free-sms-api/',
         'fields':[('api_key','Auth ID'),('api_secret','Auth Token'),('from_number','Plivo Number')]},
    ]
    for p in PROVIDERS:
        p['config'] = sms_configs.get(p['key'])

    cities = Contact.objects.filter(Q(created_by=request.user) | Q(created_by__isnull=True)).values_list('city', flat=True).distinct().exclude(city='').order_by('city')
    stats  = {
        'subscribed':   Contact.objects.filter(Q(created_by=request.user) | Q(created_by__isnull=True), is_subscribed=True).count(),
        'with_phone':   Contact.objects.filter(Q(created_by=request.user) | Q(created_by__isnull=True), is_subscribed=True).exclude(phone='').count(),
        'blasts_sent':  SMSBlast.objects.filter(created_by=request.user, status='sent').count(),
        'total_sent':   SMSBlast.objects.filter(created_by=request.user, status='sent').aggregate(
                            t=Sum('total_sent'))['t'] or 0,
    }
    return render(request, 'admin_dash/sms_blast.html', {
        'providers':  PROVIDERS,
        'blasts':     blasts,
        'blast_form': blast_form,
        'cities':     cities,
        'stats':      stats,
        'active_provider': SMSConfig.objects.filter(is_active=True).first(),
    })


def _send_sms_blast(blast, config):
    """Internal helper: send SMS blast using active provider."""
    recipients = blast.get_recipients()
    sent = 0
    failed = 0
    last_error = ''
    for contact in recipients:
        phone = contact.phone.strip()
        if not phone:
            continue
        # Clean phone: remove spaces, dashes, parentheses
        phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        if not phone.startswith('+'):
            phone = '+1' + phone  # default US country code
        try:
            if config.provider == 'twilio':
                msg_id = _twilio_send(config, phone, blast.message)
            elif config.provider == 'textbelt':
                msg_id = _textbelt_send(config, phone, blast.message)
            elif config.provider == 'vonage':
                msg_id = _vonage_send(config, phone, blast.message)
            elif config.provider == 'plivo':
                msg_id = _plivo_send(config, phone, blast.message)
            sent += 1
            last_error = ''
            if msg_id:
                logger.info(f"SMS sent via {config.provider} to {phone}, id={msg_id}")
        except Exception as e:
            logger.error(f"SMS send failed to {phone}: {e}")
            failed += 1
            last_error = str(e)
    return sent, failed, last_error


def _twilio_send(config, to, body):
    """Send SMS via Twilio API."""
    import urllib.request, urllib.parse
    url = f"https://api.twilio.com/2010-04-01/Accounts/{config.api_key}/Messages.json"
    auth = (f"{config.api_key}", config.api_secret)
    data = urllib.parse.urlencode({
        'To': to,
        'From': config.from_number,
        'Body': body
    }).encode()
    req = urllib.request.Request(url, data=data, method='POST', headers={'Content-Type': 'application/x-www-form-urlencoded'})
    import base64
    creds = base64.b64encode(f"{config.api_key}:{config.api_secret}".encode()).decode()
    req.add_header('Authorization', f'Basic {creds}')
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read())
    return resp.get('sid')


def _textbelt_send(config, to, body):
    """Send SMS via Textbelt API."""
    import urllib.request, urllib.parse
    url = 'https://textbelt.com/text'
    data = urllib.parse.urlencode({
        'phone': to,
        'message': body,
        'key': config.api_key or 'textbelt'
    }).encode()
    req = urllib.request.Request(url, data=data, method='POST')
    with urllib.request.urlopen(req, timeout=10) as r:
        resp = json.loads(r.read())
    if not resp.get('success'):
        raise Exception(resp.get('error', 'Failed'))
    return resp.get('id')


def _vonage_send(config, to, body):
    """Send SMS via Vonage (Nexmo) API using POST request."""
    import urllib.request, urllib.parse

    raw_from = (config.from_number or '').strip()

    # If no from_number configured, use Vonage default virtual number
    if not raw_from:
        raw_from = '12028837037'  # Vonage shared US number

    # Clean the from_number
    raw_from = raw_from.replace('+', '').replace(' ', '').replace('-', '')

    # Determine sender type
    if raw_from.isdigit():
        sender = raw_from[:15]  # numeric sender
    else:
        # Alphanumeric - NOT supported in India and many countries
        sender = raw_from[:11]

    # Vonage expects 'to' without '+' prefix
    to_number = to.lstrip('+')

    logger.info(f"Vonage SMS: to={to_number}, from={sender}({'numeric' if sender.isdigit() else 'alpha'}), msg_len={len(body)}")

    payload = urllib.parse.urlencode({
        'api_key': config.api_key,
        'api_secret': config.api_secret,
        'to': to_number,
        'from': sender,
        'text': body
    }).encode()

    url = 'https://rest.nexmo.com/sms/json'

    try:
        req = urllib.request.Request(url, data=payload, method='POST')
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
    except Exception as e:
        logger.error(f"Vonage API connection error: {e}")
        raise Exception(f'Vonage connection error: {e}')

    logger.info(f"Vonage response: {json.dumps(resp)}")

    msgs = resp.get('messages', [{}])
    if not msgs:
        raise Exception(f'Vonage: no messages in response: {resp}')

    msg = msgs[0]
    status = msg.get('status')
    err_text = msg.get('error-text', '')
    msg_id = msg.get('message-id', '')

    if status != '0':
        raise Exception(err_text or f'Vonage error: status={status}')

    logger.info(f"Vonage SUCCESS: msg_id={msg_id}, to={to_number}")
    return msg_id


def _plivo_send(config, to, body):
    import urllib.request, urllib.parse, base64
    url  = f"https://api.plivo.com/v1/Account/{config.api_key}/Message/"
    data = json.dumps({'src': config.from_number, 'dst': to, 'text': body}).encode()
    creds = base64.b64encode(f"{config.api_key}:{config.api_secret}".encode()).decode()
    req = urllib.request.Request(url, data=data, method='POST',
                                 headers={'Authorization': f'Basic {creds}',
                                          'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        resp = json.loads(r.read())
    if resp.get('error'):
        raise Exception(resp['error'])
    return resp.get('message_uuid') or resp.get('api_id')


@login_required
def sms_blast_delete(request, pk):
    from .models import SMSBlast
    blast = get_object_or_404(SMSBlast, pk=pk)
    blast.delete()
    messages.success(request, 'SMS blast deleted.')
    return redirect('sms_blast')


# ══════════════════════════════════════════════════════════════════
# REPORTS
# ══════════════════════════════════════════════════════════════════

@login_required
def reports(request):
    from django.db.models import Sum, Avg
    from .models import EmailTemplate, SMSBlast, SocialPost

    user = request.user

    # Date range filter
    date_from_str = request.GET.get('date_from', '')
    date_to_str   = request.GET.get('date_to', '')
    try:
        from datetime import date
        date_from = date.fromisoformat(date_from_str) if date_from_str else None
        date_to   = date.fromisoformat(date_to_str)   if date_to_str   else None
    except ValueError:
        date_from = date_to = None

    def apply_date(qs, field='created_at'):
        if date_from:
            qs = qs.filter(**{f'{field}__date__gte': date_from})
        if date_to:
            qs = qs.filter(**{f'{field}__date__lte': date_to})
        return qs

    # ── Location Stats ──────────────────────────────────────────
    locations_by_city = (Location.objects.filter(Q(created_by=user) | Q(created_by__isnull=True)).values('city').annotate(count=Count('id'))
                         .order_by('-count')[:10])
    locations_by_type = (Location.objects.filter(Q(created_by=user) | Q(created_by__isnull=True)).values('type').annotate(count=Count('id'))
                         .order_by('-count'))
    locations_by_status = (Location.objects.filter(Q(created_by=user) | Q(created_by__isnull=True)).values('status').annotate(count=Count('id')))

    # ── Contact Stats ───────────────────────────────────────────
    contacts_by_city = (Contact.objects.filter(Q(created_by=user) | Q(created_by__isnull=True)).values('city').annotate(count=Count('id'))
                        .order_by('-count')[:10])
    contacts_by_gender = (Contact.objects.filter(Q(created_by=user) | Q(created_by__isnull=True)).values('gender').annotate(count=Count('id')))
    contacts_subscribed = Contact.objects.filter(Q(created_by=user) | Q(created_by__isnull=True), is_subscribed=True).count()
    contacts_total      = Contact.objects.filter(Q(created_by=user) | Q(created_by__isnull=True)).count()

    # ── Email Stats ─────────────────────────────────────────────
    blast_qs = apply_date(EmailBlast.objects.filter(created_by=user))
    email_stats = {
        'total':      blast_qs.count(),
        'sent':       blast_qs.filter(status='sent').count(),
        'draft':      blast_qs.filter(status='draft').count(),
        'scheduled':  blast_qs.filter(status='scheduled').count(),
        'failed':     blast_qs.filter(status='failed').count(),
        'total_sent': blast_qs.aggregate(t=Sum('total_sent'))['t'] or 0,
        'total_fail': blast_qs.aggregate(t=Sum('total_failed'))['t'] or 0,
    }
    recent_blasts = apply_date(EmailBlast.objects.filter(created_by=user))[:10]

    # ── SMS Stats ───────────────────────────────────────────────
    sms_qs = apply_date(SMSBlast.objects.filter(created_by=user))
    sms_stats = {
        'total':       sms_qs.count(),
        'sent':        sms_qs.filter(status='sent').count(),
        'total_sent':  sms_qs.aggregate(t=Sum('total_sent'))['t'] or 0,
        'total_fail':  sms_qs.aggregate(t=Sum('total_failed'))['t'] or 0,
    }

    # ── Social Stats ─────────────────────────────────────────────
    post_qs = apply_date(SocialPost.objects.filter(created_by=user))
    social_by_platform = (post_qs.values('platform')
                          .annotate(count=Count('id'),
                                    posted=Count('id', filter=Q(status='posted')))
                          .order_by('-count'))
    social_stats = {
        'total':  post_qs.count(),
        'posted': post_qs.filter(status='posted').count(),
        'failed': post_qs.filter(status='failed').count(),
    }

    return render(request, 'admin_dash/reports.html', {
        'date_from': date_from_str,
        'date_to':   date_to_str,
        # Location
        'locations_by_city':   list(locations_by_city),
        'locations_by_type':   list(locations_by_type),
        'locations_by_status': list(locations_by_status),
        'total_locations':     Location.objects.filter(created_by=user).count(),
        'active_locations':    Location.objects.filter(created_by=user, status='active').count(),
        # Contact
        'contacts_by_city':   list(contacts_by_city),
        'contacts_by_gender': list(contacts_by_gender),
        'contacts_subscribed': contacts_subscribed,
        'contacts_total':      contacts_total,
        # Email
        'email_stats':   email_stats,
        'recent_blasts': recent_blasts,
        # SMS
        'sms_stats': sms_stats,
        # Social
        'social_by_platform': list(social_by_platform),
        'social_stats':       social_stats,
    })


# ══════════════════════════════════════════════════════════════════
# USER PROFILE & SETTINGS
# ══════════════════════════════════════════════════════════════════

@login_required
def user_profile(request):
    """User Profile Page"""
    from .models import UserProfile
    user = request.user
    
    # Get or create profile
    user_profile, created = UserProfile.objects.get_or_create(user=user)
    
    profile_data = {
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': user.email,
        'username': user.username,
        'date_joined': user.date_joined.strftime('%B %d, %Y') if user.date_joined else 'N/A',
        'last_login': user.last_login.strftime('%B %d, %Y at %I:%M %p') if user.last_login else 'Never',
        'photo': user_profile.photo.url if user_profile.photo else None,
        'bio': user_profile.bio,
        'phone': user_profile.phone,
        'city': user_profile.city,
        'company': user_profile.company,
    }
    return render(request, 'admin_dash/user_profile.html', {'profile': profile_data, 'user_profile': user_profile})


@login_required
def user_profile_update(request):
    """Update User Profile"""
    if request.method == 'POST':
        from .models import UserProfile
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        bio = request.POST.get('bio', '').strip()
        phone = request.POST.get('phone', '').strip()
        city = request.POST.get('city', '').strip()
        company = request.POST.get('company', '').strip()
        
        if not first_name or not last_name or not email:
            messages.error(request, 'All fields are required.')
            return redirect('user_profile')
        
        user = request.user
        user.first_name = first_name
        user.last_name = last_name
        user.email = email
        user.save()
        
        # Update profile
        user_profile, _ = UserProfile.objects.get_or_create(user=user)
        user_profile.bio = bio
        user_profile.phone = phone
        user_profile.city = city
        user_profile.company = company
        user_profile.save()
        
        messages.success(request, 'Profile updated successfully!')
        return redirect('user_profile')
    
    return redirect('user_profile')


@login_required
def user_upload_photo(request):
    """Upload User Profile Photo"""
    if request.method == 'POST' and request.FILES.get('photo'):
        from .models import UserProfile
        from django.conf import settings
        import os
        
        user_profile, created = UserProfile.objects.get_or_create(user=request.user)
        
        # Delete old photo if exists
        if user_profile.photo:
            old_path = user_profile.photo.path
            if os.path.exists(old_path):
                os.remove(old_path)
        
        user_profile.photo = request.FILES['photo']
        user_profile.save()
        
        return JsonResponse({'success': True, 'photo_url': user_profile.photo.url})
    
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)


@login_required
def user_remove_photo(request):
    """Remove User Profile Photo"""
    if request.method == 'POST':
        from .models import UserProfile
        
        user_profile, created = UserProfile.objects.get_or_create(user=request.user)
        user_profile.delete_photo()
        
        return JsonResponse({'success': True})
    
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)


@login_required
def user_settings(request):
    """User Settings Page"""
    return render(request, 'admin_dash/user_settings.html')


@login_required
def user_update_password(request):
    """Update User Password"""
    if request.method == 'POST':
        current_password = request.POST.get('current_password', '')
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')
        
        user = request.user
        
        if not user.check_password(current_password):
            messages.error(request, 'Current password is incorrect.')
            return redirect('user_settings')
        
        if len(new_password) < 8:
            messages.error(request, 'New password must be at least 8 characters long.')
            return redirect('user_settings')
        
        if new_password != confirm_password:
            messages.error(request, 'New passwords do not match.')
            return redirect('user_settings')
        
        user.set_password(new_password)
        user.save()
        
        # Re-authenticate user
        from django.contrib.auth import login
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        
        messages.success(request, 'Password changed successfully!')
        return redirect('user_settings')
    
    return redirect('user_settings')


# ══════════════════════════════════════════════════════════════════
# LINKEDIN OAUTH 2.0
# ══════════════════════════════════════════════════════════════════

@login_required
def linkedin_connect(request):
    """
    Step 1 of LinkedIn OAuth 2.0 flow.
    
    Redirects the user to LinkedIn's authorization page. After the user
    approves, LinkedIn redirects back to /linkedin/callback/ with a code.
    
    URL structure:
        /linkedin/connect/ → LinkedIn login → /linkedin/callback/?code=XXX&state=XXX
    
    Requirements:
        - LINKEDIN_CLIENT_ID in settings/.env
        - LINKEDIN_CLIENT_SECRET in settings/.env
        - Redirect URI registered in LinkedIn Developer Portal
    """
    from django.conf import settings
    
    # Check credentials are configured
    client_id = getattr(settings, 'LINKEDIN_CLIENT_ID', None)
    if not client_id:
        messages.error(request, 'LinkedIn Client ID not configured. Please add LINKEDIN_CLIENT_ID to your .env file.')
        return redirect('social_media')
    
    client_secret = getattr(settings, 'LINKEDIN_CLIENT_SECRET', None)
    if not client_secret:
        messages.error(request, 'LinkedIn Client Secret not configured. Please add LINKEDIN_CLIENT_SECRET to your .env file.')
        return redirect('social_media')
    
    try:
        # Build OAuth URL - uses w_member_social only (r_liteprofile not needed)
        from .linkedin_oauth import build_linkedin_oauth_url
        auth_url, state = build_linkedin_oauth_url()
        
        # Store state in session to validate callback (CSRF protection)
        request.session['linkedin_oauth_state'] = state
        request.session['linkedin_oauth_initiated'] = True
        
        logger.info(f"LinkedIn OAuth: Redirecting to authorization. State: {state[:16]}...")
        return redirect(auth_url)
        
    except ValueError as e:
        messages.error(request, f'LinkedIn configuration error: {str(e)}')
        return redirect('social_media')
    except Exception as e:
        logger.error(f"LinkedIn OAuth initiation failed: {str(e)}")
        messages.error(request, f'Failed to start LinkedIn authorization: {str(e)}')
        return redirect('social_media')


def linkedin_callback(request):
    """
    Step 2 of LinkedIn OAuth 2.0 flow.
    
    Handles the redirect from LinkedIn after user authorization.
    Extracts the authorization code and exchanges it for an access token.
    
    Success URL:
        /linkedin/callback/?code=AUTH_CODE&state=STATE_VALUE
    
    Error URL:
        /linkedin/callback/?error=ERROR_CODE&error_description=DESCRIPTION&state=STATE_VALUE
    
    After successful token exchange, stores the token in the database
    and redirects to the social media page with a success message.
    """
    from .models import SocialPlatformConfig
    from .linkedin_oauth import exchange_code_for_token, store_linkedin_token, get_linkedin_user_id
    
    # ── Step 1: Handle errors from LinkedIn ─────────────────────────
    error = request.GET.get('error', '')
    error_description = request.GET.get('error_description', '')
    
    if error:
        logger.warning(f"LinkedIn OAuth error: {error} - {error_description}")
        messages.error(request, f'LinkedIn authorization failed: {error_description or error}')
        return redirect('social_media')
    
    # ── Step 2: Validate CSRF state ────────────────────────────────
    code = request.GET.get('code', '')
    state = request.GET.get('state', '')
    
    stored_state = request.session.pop('linkedin_oauth_state', None)
    if not stored_state or stored_state != state:
        logger.warning(f"LinkedIn OAuth state mismatch")
        messages.error(request, 'Security check failed. Please try connecting again.')
        return redirect('social_media')
    
    if not request.session.pop('linkedin_oauth_initiated', False):
        messages.error(request, 'No authorization request found. Please start from the Social Media page.')
        return redirect('social_media')
    
    # ── Step 3: Extract authorization code ─────────────────────────
    if not code:
        messages.error(request, 'No authorization code received from LinkedIn.')
        return redirect('social_media')
    
    logger.info(f"LinkedIn OAuth: Received code, exchanging for token...")
    
    # ── Step 4: Exchange code for access token ────────────────────
    from .linkedin_oauth import exchange_code_for_token
    token_result = exchange_code_for_token(code)
    
    if not token_result.get('success'):
        error_msg = token_result.get('error', 'Unknown error')
        logger.error(f"Token exchange failed: {error_msg}")
        messages.error(request, f'Failed to get access token: {error_msg}')
        return redirect('social_media')
    
    access_token = token_result['access_token']
    expires_in = token_result.get('expires_in')
    granted_scope = token_result.get('scope', 'w_member_social')
    
    logger.info(f"Token obtained. Expires in {expires_in} seconds. Scope: {granted_scope}")
    
    # ── Step 5: Get user profile ID (optional, may fail) ───────────
    member_id = ''
    display_name = 'LinkedIn Account'
    
    user_info = get_linkedin_user_id(access_token)
    if user_info.get('success') and user_info.get('id'):
        member_id = user_info['id']
        first_name = user_info.get('localizedFirstName', '')
        last_name = user_info.get('localizedLastName', '')
        display_name = f"{first_name} {last_name}".strip() or 'LinkedIn Account'
        logger.info(f"Got user profile: {member_id} ({display_name})")
    else:
        err = user_info.get('error', 'Unknown')
        logger.warning(f"Could not get LinkedIn profile info: {err}. User will need to enter Profile ID manually.")
    
    # ── Step 6: Store token in database ──────────────────────────
    from .linkedin_oauth import store_linkedin_token
    config = store_linkedin_token(
        user=request.user,
        access_token=access_token,
        expires_in=expires_in,
        scope=granted_scope,
        member_id=member_id,
    )
    
    if not config:
        messages.error(request, 'Failed to save LinkedIn token to database.')
        return redirect('social_media')
    
    # ── Step 7: Success ───────────────────────────────────────────
    if member_id:
        messages.success(request, f'LinkedIn connected successfully! You can now post to LinkedIn.')
    else:
        messages.warning(request, 'LinkedIn connected, but could not fetch your Profile ID. Please go to Social Media → Manage Accounts → Edit your LinkedIn account and enter your numeric LinkedIn Profile ID manually.')
    
    return redirect('social_media')


@login_required
def linkedin_disconnect(request):
    """
    Disconnect LinkedIn by clearing the stored access token.
    """
    from .models import SocialPlatformConfig
    
    SocialPlatformConfig.objects.filter(
        platform='linkedin',
        created_by=request.user
    ).update(access_token='', is_connected=False, extra_field='')
    
    messages.success(request, 'LinkedIn disconnected.')
    return redirect('social_media')


@login_required
def linkedin_test_post(request):
    """
    Test posting to LinkedIn with a simple message.
    """
    from django.http import JsonResponse
    from .models import SocialPlatformConfig
    from .social_service import post_to_linkedin
    
    config = SocialPlatformConfig.objects.filter(
        platform='linkedin',
        created_by=request.user,
        is_connected=True
    ).first()
    
    if not config or not config.access_token:
        messages.error(request, 'LinkedIn is not connected. Please connect it first.')
        return redirect('social_media')
    
    test_message = f"Test post from Event Directory and Logistic - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    result = post_to_linkedin(
        access_token=config.access_token,
        org_urn=config.extra_field or '',
        message=test_message,
    )
    
    if result['success']:
        messages.success(request, f'Test post successful! <a href="{result["post_url"]}" target="_blank">View on LinkedIn</a>')
    else:
        messages.error(request, f'Test post failed: {result["error"]}')
    
    return redirect('social_media')


# ═══════════════════════════════════════════════════════════════════════════════
# WEBINAR DASHBOARD - Decipher the Dating Code
# ═══════════════════════════════════════════════════════════════════════════════

@login_required
def webinar_dashboard(request):
    from django.db.models import Sum
    from .models import WebinarGroup, WebinarAccount, WebinarEvent, WebinarPost, WebinarPostLog
    
    user = request.user
    
    total_groups = WebinarGroup.objects.filter(created_by=user).count()
    small_groups = WebinarGroup.objects.filter(created_by=user, size_category='small').count()
    medium_groups = WebinarGroup.objects.filter(created_by=user, size_category='medium').count()
    large_groups = WebinarGroup.objects.filter(created_by=user, size_category='large').count()
    
    total_accounts = WebinarAccount.objects.filter(created_by=user).count()
    total_reach = WebinarGroup.objects.filter(created_by=user).aggregate(
        total=Sum('member_count'))['total'] or 0
    
    posts_today = WebinarPost.objects.filter(
        created_by=user,
        posted_at__date=timezone.now().date()
    ).count()
    
    total_posts = WebinarPost.objects.filter(created_by=user).count()
    scheduled_posts = WebinarPost.objects.filter(created_by=user, status='scheduled').count()
    
    recent_logs = WebinarPostLog.objects.filter(
        post__created_by=user
    ).select_related('post', 'group', 'account').order_by('-created_at')[:10]
    
    active_events = WebinarEvent.objects.filter(
        created_by=user,
        status__in=['active', 'scheduled']
    ).order_by('-event_date')[:5]
    
    return render(request, 'admin_dash/webinar_dashboard.html', {
        'stats': {
            'total_groups': total_groups,
            'small_groups': small_groups,
            'medium_groups': medium_groups,
            'large_groups': large_groups,
            'total_accounts': total_accounts,
            'total_reach': total_reach,
            'posts_today': posts_today,
            'total_posts': total_posts,
            'scheduled_posts': scheduled_posts,
        },
        'recent_logs': recent_logs,
        'active_events': active_events,
    })


@login_required
def webinar_groups(request):
    from .models import WebinarGroup
    
    user = request.user
    q = request.GET.get('q', '')
    size = request.GET.get('size', '')
    status = request.GET.get('status', '')
    engagement = request.GET.get('engagement', '')
    
    qs = WebinarGroup.objects.filter(created_by=user)
    
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(niche__icontains=q) | Q(tags__icontains=q))
    if size:
        qs = qs.filter(size_category=size)
    if status:
        qs = qs.filter(status=status)
    if engagement:
        qs = qs.filter(engagement=engagement)
    
    groups = qs.order_by('-member_count')
    
    size_categories = [
        ('small', 'Below 10,000'),
        ('medium', '10,000 - 100,000'),
        ('large', 'Above 100,000'),
    ]
    status_choices = WebinarGroup.STATUS_CHOICES
    engagement_choices = WebinarGroup.ENGAGEMENT_CHOICES
    
    return render(request, 'admin_dash/webinar_groups.html', {
        'groups': groups,
        'filters': {'q': q, 'size': size, 'status': status, 'engagement': engagement},
        'size_categories': size_categories,
        'status_choices': status_choices,
        'engagement_choices': engagement_choices,
    })


@login_required
def webinar_import_groups(request):
    from .models import WebinarGroup
    
    user = request.user
    
    if request.method == 'POST':
        if request.FILES.get('file'):
            file = request.FILES['file']
            try:
                _ensure_pandas()
                if pd is None:
                    messages.error(request, 'Pandas library not available. Please install it.')
                    return redirect('webinar_groups')
                
                if file.name.endswith('.csv'):
                    df = pd.read_csv(file)
                else:
                    df = pd.read_excel(file)
                
                imported = 0
                skipped = 0
                errors = []
                
                messages.info(request, f'File has columns: {list(df.columns)}. Total rows: {len(df)}')
                
                for idx, row in df.iterrows():
                    cols = row.to_dict()
                    
                    name = ''
                    for col in df.columns:
                        val = str(cols[col]).strip() if cols[col] else ''
                        if val and val.lower() not in ['nan', 'none', '']:
                            name = val
                            break
                    
                    if not name or name.lower() in ['nan', 'none', '']:
                        skipped += 1
                        continue
                    
                    url = ''
                    for col in ['url', 'link', 'URL', 'Group URL', 'group_url']:
                        if col in cols and str(cols[col]).strip():
                            url = str(cols[col]).strip()
                            break
                    if url.lower() in ['nan', 'none']:
                        url = ''
                    
                    members = 0
                    for col in ['members', 'member_count', 'Member Count', 'Members', 'membercount']:
                        if col in cols and cols[col]:
                            try:
                                members = int(float(str(cols[col])))
                                break
                            except (ValueError, TypeError):
                                pass
                    
                    niche = ''
                    for col in ['niche', 'Niche']:
                        if col in cols and str(cols[col]).strip():
                            niche = str(cols[col]).strip()
                            break
                    if niche.lower() in ['nan', 'none']:
                        niche = ''
                    
                    tags = ''
                    for col in ['tags', 'Tags']:
                        if col in cols and str(cols[col]).strip():
                            tags = str(cols[col]).strip()
                            break
                    if tags.lower() in ['nan', 'none']:
                        tags = ''
                    
                    existing = WebinarGroup.objects.filter(created_by=user, name__iexact=name).first()
                    if existing:
                        skipped += 1
                        continue
                    
                    try:
                        WebinarGroup.objects.create(
                            name=name,
                            url=url,
                            member_count=members,
                            niche=niche,
                            tags=tags,
                            created_by=user,
                        )
                        imported += 1
                    except Exception as e:
                        errors.append(f"Row {idx+1}: {str(e)}")
                
                msg = f'Imported {imported} groups. {skipped} skipped.'
                if errors:
                    msg += f' Errors: {", ".join(errors[:5])}'
                messages.success(request, msg)
            except Exception as e:
                messages.error(request, f'Import failed: {str(e)}')
        
        elif request.POST.get('manual_add'):
            name = request.POST.get('name', '').strip()
            url = request.POST.get('url', '').strip()
            members = request.POST.get('member_count', '0').strip()
            niche = request.POST.get('niche', '').strip()
            tags = request.POST.get('tags', '').strip()
            engagement = request.POST.get('engagement', 'medium')
            
            if not name:
                messages.error(request, 'Group name is required.')
                return redirect('webinar_groups')
            
            try:
                members = int(members) if members else 0
            except ValueError:
                members = 0
            
            WebinarGroup.objects.create(
                name=name,
                url=url,
                member_count=members,
                niche=niche,
                tags=tags,
                engagement=engagement,
                created_by=user,
            )
            messages.success(request, f'Group "{name}" added successfully.')
        
        return redirect('webinar_groups')
    
    return redirect('webinar_groups')


@login_required
def webinar_delete_group(request, pk):
    from .models import WebinarGroup
    group = get_object_or_404(WebinarGroup.objects.filter(created_by=request.user), pk=pk)
    name = group.name
    group.delete()
    messages.success(request, f'Group "{name}" deleted.')
    return redirect('webinar_groups')


@login_required
def webinar_accounts(request):
    from .models import WebinarAccount
    
    user = request.user
    q = request.GET.get('q', '')
    account_type = request.GET.get('type', '')
    status = request.GET.get('status', '')
    
    qs = WebinarAccount.objects.filter(created_by=user)
    
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(tags__icontains=q))
    if account_type:
        qs = qs.filter(account_type=account_type)
    if status:
        qs = qs.filter(status=status)
    
    accounts = qs.order_by('account_type', 'name')
    
    return render(request, 'admin_dash/webinar_accounts.html', {
        'accounts': accounts,
        'filters': {'q': q, 'type': account_type, 'status': status},
        'account_types': WebinarAccount.ACCOUNT_TYPE_CHOICES,
        'status_choices': WebinarAccount.STATUS_CHOICES,
    })


@login_required
def webinar_import_accounts(request):
    from .models import WebinarAccount
    
    user = request.user
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        account_type = request.POST.get('account_type', 'page')
        url = request.POST.get('url', '').strip()
        page_id = request.POST.get('page_id', '').strip()
        access_token = request.POST.get('access_token', '').strip()
        tags = request.POST.get('tags', '').strip()
        
        if not name:
            messages.error(request, 'Account name is required.')
            return redirect('webinar_accounts')
        
        WebinarAccount.objects.create(
            name=name,
            account_type=account_type,
            url=url,
            page_id=page_id,
            access_token=access_token,
            tags=tags,
            created_by=user,
        )
        messages.success(request, f'Account "{name}" added successfully.')
        return redirect('webinar_accounts')
    
    return redirect('webinar_accounts')


@login_required
def webinar_delete_account(request, pk):
    from .models import WebinarAccount
    account = get_object_or_404(WebinarAccount.objects.filter(created_by=request.user), pk=pk)
    name = account.name
    account.delete()
    messages.success(request, f'Account "{name}" deleted.')
    return redirect('webinar_accounts')


@login_required
def webinar_events(request):
    from .models import WebinarEvent
    
    user = request.user
    events = WebinarEvent.objects.filter(created_by=user).order_by('-event_date')
    
    return render(request, 'admin_dash/webinar_events.html', {
        'events': events,
    })


@login_required
def webinar_event_create(request):
    from .models import WebinarEvent
    
    user = request.user
    
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        registration_link = request.POST.get('registration_link', '').strip()
        event_date = request.POST.get('event_date', '')
        event_end_date = request.POST.get('event_end_date', '')
        status = request.POST.get('status', 'draft')
        
        if not title:
            messages.error(request, 'Event title is required.')
            return redirect('webinar_events')
        
        parsed_date = None
        if event_date:
            try:
                parsed_date = datetime.fromisoformat(event_date.replace('T', ' '))
            except ValueError:
                try:
                    parsed_date = datetime.strptime(event_date, '%Y-%m-%d %H:%M')
                except ValueError:
                    pass
        
        parsed_end = None
        if event_end_date:
            try:
                parsed_end = datetime.fromisoformat(event_end_date.replace('T', ' '))
            except ValueError:
                try:
                    parsed_end = datetime.strptime(event_end_date, '%Y-%m-%d %H:%M')
                except ValueError:
                    pass
        
        event = WebinarEvent.objects.create(
            title=title,
            description=description,
            registration_link=registration_link,
            event_date=parsed_date,
            event_end_date=parsed_end,
            status=status,
            created_by=user,
        )
        
        if request.FILES.get('cover_image'):
            event.cover_image = request.FILES['cover_image']
            event.save()
        
        messages.success(request, f'Event "{title}" created successfully.')
        return redirect('webinar_events')
    
    return redirect('webinar_events')


@login_required
def webinar_delete_event(request, pk):
    from .models import WebinarEvent
    event = get_object_or_404(WebinarEvent.objects.filter(created_by=request.user), pk=pk)
    title = event.title
    event.delete()
    messages.success(request, f'Event "{title}" deleted.')
    return redirect('webinar_events')


@login_required
def webinar_create_post(request):
    from .models import WebinarGroup, WebinarAccount, WebinarEvent, WebinarPost
    
    user = request.user
    
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        content = request.POST.get('content', '').strip()
        link_url = request.POST.get('link_url', '').strip()
        event_id = request.POST.get('event_id', '')
        spin_variations = request.POST.get('spin_variations', '').strip()
        scheduled_at = request.POST.get('scheduled_at', '')
        delay_minutes = request.POST.get('delay_minutes', '5').strip()
        selected_groups = request.POST.getlist('selected_groups')
        selected_accounts = request.POST.getlist('selected_accounts')
        
        if not title or not content:
            messages.error(request, 'Title and content are required.')
            return redirect('webinar_create_post')
        
        try:
            delay_minutes = int(delay_minutes) if delay_minutes else 5
        except ValueError:
            delay_minutes = 5
        
        parsed_schedule = None
        if scheduled_at:
            try:
                parsed_schedule = datetime.fromisoformat(scheduled_at.replace('T', ' '))
            except ValueError:
                try:
                    parsed_schedule = datetime.strptime(scheduled_at, '%Y-%m-%d %H:%M')
                except ValueError:
                    pass
        
        event = None
        if event_id:
            event = WebinarEvent.objects.filter(created_by=user, pk=event_id).first()
            if event and not link_url and event.registration_link:
                link_url = event.registration_link
        
        post = WebinarPost.objects.create(
            title=title,
            content=content,
            link_url=link_url,
            event=event,
            spin_variations=spin_variations,
            scheduled_at=parsed_schedule,
            status='scheduled' if parsed_schedule else 'draft',
            delay_minutes=delay_minutes,
            created_by=user,
        )
        
        if selected_groups:
            groups = WebinarGroup.objects.filter(created_by=user, pk__in=selected_groups)
            for g in groups:
                post.target_groups.add(g)
        
        if selected_accounts:
            accounts = WebinarAccount.objects.filter(created_by=user, pk__in=selected_accounts)
            for a in accounts:
                post.target_accounts.add(a)
        
        if request.FILES.get('image'):
            post.image = request.FILES['image']
            post.save()
        
        messages.success(request, f'Post "{title}" created successfully.')
        return redirect('webinar_scheduled')
    
    groups = WebinarGroup.objects.filter(created_by=user).order_by('-member_count')
    accounts = WebinarAccount.objects.filter(created_by=user).order_by('account_type', 'name')
    events = WebinarEvent.objects.filter(created_by=user, status__in=['draft', 'scheduled', 'active']).order_by('-event_date')
    
    return render(request, 'admin_dash/webinar_create_post.html', {
        'groups': groups,
        'accounts': accounts,
        'events': events,
    })


@login_required
def webinar_facebook_login(request):
    """
    Redirect user to Facebook OAuth authorization page.
    """
    import secrets
    import logging
    from urllib.parse import urlencode
    logger = logging.getLogger(__name__)
    
    app_id = request.session.get('facebook_app_id', '824209346621581')
    app_secret = request.session.get('facebook_app_secret')
    
    # Get ngrok_url - strip any trailing slashes and spaces
    ngrok_url = request.session.get('ngrok_url', '')
    if isinstance(ngrok_url, str):
        ngrok_url = ngrok_url.strip().rstrip('/')
    
    if not app_id:
        logger.warning("Facebook login - App ID missing")
        messages.error(request, 'Please configure Facebook App ID first.')
        return redirect('webinar_facebook_settings')
    
    if not app_secret:
        logger.warning("Facebook login - App Secret missing")
        messages.error(request, 'Please configure Facebook App Secret first.')
        return redirect('webinar_facebook_settings')
    
    if not ngrok_url or ngrok_url == '':
        logger.warning("Facebook login - Ngrok URL missing")
        messages.error(request, 'Please enter your ngrok URL first. Example: https://abc123.ngrok-free.app')
        return redirect('webinar_facebook_settings')
    
    # Validate ngrok URL format
    if not ngrok_url.startswith('http'):
        messages.error(request, 'Invalid ngrok URL. Must start with http:// or https://')
        return redirect('webinar_facebook_settings')
    
    # Generate secure state token to prevent CSRF
    state = secrets.token_hex(16)
    request.session['facebook_oauth_state'] = state
    
    # CRITICAL: Build EXACT same redirect_uri - NO TRAILING SLASH
    redirect_uri = f"{ngrok_url}/webinar/facebook/callback"
    
    # Store in session for exact match in callback
    request.session['fb_redirect_uri'] = redirect_uri
    
    # Build Facebook OAuth URL - keep scope unencoded
    auth_url = (
        f"https://www.facebook.com/v18.0/dialog/oauth"
        f"?client_id={app_id}"
        f"&redirect_uri={urlencode({'redirect_uri': redirect_uri})}"
        f"&state={state}"
        f"&scope=email,public_profile"
        f"&response_type=code"
    )
    
    logger.info(f"Facebook OAuth - Redirect URI: {redirect_uri}")
    logger.info(f"Facebook OAuth - Full URL: {auth_url}")
    
    return redirect(auth_url)


def webinar_facebook_callback(request):
    """
    Handle Facebook OAuth callback.
    """
    import requests
    import logging
    from urllib.parse import urlencode
    logger = logging.getLogger(__name__)
    
    code = request.GET.get('code')
    state = request.GET.get('state')
    error = request.GET.get('error')
    error_reason = request.GET.get('error_reason', '')
    error_description = request.GET.get('error_description', '')
    
    logger.info(f"Callback received - error: {error}, code: {bool(code)}")
    
    # Handle Facebook errors
    if error:
        error_msg = error_reason or error_description or error
        logger.warning(f"Facebook OAuth error: {error} - {error_msg}")
        
        if 'redirect_uri_mismatch' in str(error_msg).lower() or 'redirect_uri_mismatch' in str(error).lower():
            messages.error(request, f'Redirect URI mismatch! Make sure this URL is in Facebook Console: {request.session.get("fb_redirect_uri", "N/A")}')
        elif 'user_denied' in str(error).lower():
            messages.error(request, 'You denied the Facebook login request.')
        else:
            messages.error(request, f'Facebook error: {error_msg}')
        return redirect('webinar_facebook_settings')
    
    if not code:
        logger.error("Facebook OAuth callback - no code received")
        messages.error(request, 'Authorization failed. No code received from Facebook.')
        return redirect('webinar_facebook_settings')
    
    # Verify state if it exists
    stored_state = request.session.get('facebook_oauth_state')
    if stored_state and state != stored_state:
        logger.warning(f"State mismatch. Expected: {stored_state}, Got: {state}")
    
    if 'facebook_oauth_state' in request.session:
        del request.session['facebook_oauth_state']
    
    app_id = request.session.get('facebook_app_id', '824209346621581')
    app_secret = request.session.get('facebook_app_secret')
    
    if not app_secret:
        messages.error(request, 'Facebook credentials not found.')
        return redirect('webinar_facebook_settings')
    
    # CRITICAL: Use EXACT same redirect_uri as login
    redirect_uri = request.session.get('fb_redirect_uri')
    logger.info(f"Using redirect_uri: {redirect_uri}")
    
    if not redirect_uri:
        messages.error(request, 'Redirect URI not found in session. Please try logging in again.')
        return redirect('webinar_facebook_settings')
    
    try:
        # Exchange code for token
        params = {
            'client_id': app_id,
            'client_secret': app_secret,
            'redirect_uri': redirect_uri,
            'code': code,
        }
        
        response = requests.get(
            'https://graph.facebook.com/v18.0/oauth/access_token',
            params=params,
            timeout=30
        )
        data = response.json()
        
        logger.info(f"Token response: {data}")
        
        if 'access_token' in data:
            access_token = data['access_token']
            request.session['facebook_access_token'] = access_token
            
            # Get user info
            me_response = requests.get(
                'https://graph.facebook.com/v18.0/me',
                params={'access_token': access_token, 'fields': 'id,name,email'},
                timeout=30
            )
            me_data = me_response.json()
            
            request.session['facebook_user_id'] = me_data.get('id')
            request.session['facebook_user_name'] = me_data.get('name')
            request.session['facebook_user_email'] = me_data.get('email')
            
            logger.info(f"Facebook connected: {me_data.get('name')}")
            messages.success(request, f'Facebook connected! Welcome, {me_data.get("name")}!')
            
        else:
            error_msg = data.get('error', {}).get('message', data.get('error_description', 'Unknown'))
            logger.error(f"Token error: {error_msg}")
            messages.error(request, f'Token error: {error_msg}')
            
    except Exception as e:
        logger.exception(f"Callback error: {str(e)}")
        messages.error(request, f'Error: {str(e)}')
    
    return redirect('webinar_dashboard')


@login_required
def webinar_facebook_logout(request):
    """
    Disconnect Facebook by clearing session data.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # Clear all Facebook-related session data
    facebook_session_keys = [
        'facebook_access_token',
        'facebook_user_id',
        'facebook_user_name',
        'facebook_user_email',
        'facebook_oauth_state',
    ]
    
    for key in facebook_session_keys:
        if key in request.session:
            del request.session[key]
    
    logger.info(f"Facebook disconnected for user {request.user.id}")
    messages.success(request, 'Facebook disconnected successfully.')
    return redirect('webinar_dashboard')


@login_required
def webinar_facebook_post_now(request, pk):
    from .models import WebinarPost, WebinarPostLog, WebinarGroup
    from django.utils import timezone
    import requests
    import random
    import time
    
    post = get_object_or_404(WebinarPost.objects.filter(created_by=request.user), pk=pk)
    
    access_token = request.session.get('facebook_access_token')
    if not access_token:
        messages.error(request, 'Please connect your Facebook account first.')
        return redirect('webinar_facebook_settings')
    
    groups = list(post.target_groups.all())
    if not groups:
        messages.error(request, 'No groups selected for this post.')
        return redirect('webinar_scheduled')
    
    def get_content():
        if post.spin_variations:
            variations = [v.strip() for v in post.spin_variations.split('|')]
            return random.choice(variations)
        return post.content
    
    sent_count = 0
    failed_count = 0
    
    for group in groups:
        if not group.url:
            WebinarPostLog.objects.create(
                post=post,
                group=group,
                status='failed',
                error_message='No URL configured',
                created_at=timezone.now(),
            )
            failed_count += 1
            continue
        
        group_id = group.url.split('/')[-1]
        if not group_id or not group_id.isdigit():
            group_id = group.name
        
        message = get_content()
        if post.link_url:
            message += f"\n\n{post.link_url}"
        
        try:
            url = f'https://graph.facebook.com/v18.0/{group_id}/feed'
            data = {
                'access_token': access_token,
                'message': message,
            }
            
            response = requests.post(url, data=data, timeout=30)
            result = response.json()
            
            if response.status_code == 200 and 'id' in result:
                WebinarPostLog.objects.create(
                    post=post,
                    group=group,
                    status='success',
                    post_url=f'https://facebook.com/groups/{result["id"]}',
                    sent_at=timezone.now(),
                    created_at=timezone.now(),
                )
                sent_count += 1
                group.posts_count += 1
                group.last_posted = timezone.now()
                group.save()
            else:
                error_msg = result.get('error', {}).get('message', 'Unknown error')
                WebinarPostLog.objects.create(
                    post=post,
                    group=group,
                    status='failed',
                    error_message=error_msg,
                    created_at=timezone.now(),
                )
                failed_count += 1
        except Exception as e:
            WebinarPostLog.objects.create(
                post=post,
                group=group,
                status='failed',
                error_message=str(e),
                created_at=timezone.now(),
            )
            failed_count += 1
        
        time.sleep(3)
    
    if sent_count > 0:
        post.status = 'posted'
        post.posted_at = timezone.now()
        post.save()
    
    messages.success(request, f'Posted to {sent_count} groups! {failed_count} failed.')
    return redirect('webinar_scheduled')


@login_required
def webinar_facebook_settings(request):
    ngrok_url = request.session.get('ngrok_url', '') or ''
    facebook_app_id = request.session.get('facebook_app_id', '') or '824209346621581'
    facebook_app_secret = request.session.get('facebook_app_secret', '') or ''
    redirect_uri = f"{ngrok_url.rstrip('/')}/webinar/facebook/callback" if ngrok_url else ''
    
    return render(request, 'admin_dash/webinar_facebook_settings.html', {
        'ngrok_url': ngrok_url,
        'facebook_app_id': facebook_app_id,
        'facebook_app_secret': facebook_app_secret,
        'redirect_uri': redirect_uri,
    })


@login_required
def webinar_save_facebook_settings(request):
    if request.method == 'POST':
        app_id = request.POST.get('facebook_app_id', '').strip()
        app_secret = request.POST.get('facebook_app_secret', '').strip()
        ngrok_url = request.POST.get('ngrok_url', '').strip()
        
        if app_id:
            request.session['facebook_app_id'] = app_id
        else:
            request.session.pop('facebook_app_id', None)
        
        if app_secret:
            request.session['facebook_app_secret'] = app_secret
        else:
            request.session.pop('facebook_app_secret', None)
        
        if ngrok_url:
            request.session['ngrok_url'] = ngrok_url.rstrip('/')
        else:
            request.session.pop('ngrok_url', None)
        
        messages.success(request, 'Facebook settings saved!')
    
    return redirect('webinar_facebook_settings')


@login_required
def webinar_scheduled(request):
    from .models import WebinarPost
    
    user = request.user
    posts = WebinarPost.objects.filter(created_by=user).order_by('-created_at')
    
    status_filter = request.GET.get('status', '')
    if status_filter:
        posts = posts.filter(status=status_filter)
    
    return render(request, 'admin_dash/webinar_scheduled.html', {
        'posts': posts,
        'status_filter': status_filter,
    })


@login_required
def webinar_delete_post(request, pk):
    from .models import WebinarPost
    post = get_object_or_404(WebinarPost.objects.filter(created_by=request.user), pk=pk)
    title = post.title
    post.delete()
    messages.success(request, f'Post "{title}" deleted.')
    return redirect('webinar_scheduled')


@login_required
def webinar_send_post(request, pk):
    from .models import WebinarPost, WebinarPostLog, WebinarGroup, WebinarAccount
    from django.utils import timezone
    import time
    
    post = get_object_or_404(WebinarPost.objects.filter(created_by=request.user), pk=pk)
    user = request.user
    
    groups = list(post.target_groups.all())
    accounts = list(post.target_accounts.all())
    
    if not groups and not accounts:
        messages.error(request, 'No groups or accounts selected for this post.')
        return redirect('webinar_scheduled')
    
    sent_count = 0
    failed_count = 0
    
    delay = post.delay_minutes or 5
    
    def get_content():
        if post.spin_variations:
            import random
            variations = [v.strip() for v in post.spin_variations.split('|')]
            return random.choice(variations)
        return post.content
    
    for group in groups:
        if group.url:
            content = f"{get_content()}\n\n🔗 {post.link_url if post.link_url else ''}"
            
            WebinarPostLog.objects.create(
                post=post,
                group=group,
                status='success',
                post_url=group.url,
                sent_at=timezone.now(),
                created_at=timezone.now(),
            )
            sent_count += 1
            group.posts_count += 1
            group.last_posted = timezone.now()
            group.save()
        else:
            WebinarPostLog.objects.create(
                post=post,
                group=group,
                status='failed',
                error_message='No URL configured for this group',
                created_at=timezone.now(),
            )
            failed_count += 1
        
        time.sleep(2)
    
    for account in accounts:
        WebinarPostLog.objects.create(
            post=post,
            account=account,
            status='success',
            post_url=account.url if account.url else None,
            sent_at=timezone.now(),
            created_at=timezone.now(),
        )
        sent_count += 1
        time.sleep(2)
    
    post.status = 'posted'
    post.posted_at = timezone.now()
    post.save()
    
    messages.success(request, f'Post logged! {sent_count} destinations recorded. {failed_count} failed.')
    return redirect('webinar_scheduled')


@login_required
def webinar_analytics(request):
    from django.db.models import Sum, Count
    from .models import WebinarGroup, WebinarAccount, WebinarPost, WebinarPostLog
    
    user = request.user
    
    total_logs = WebinarPostLog.objects.filter(post__created_by=user)
    
    success_count = total_logs.filter(status='success').count()
    failed_count = total_logs.filter(status='failed').count()
    pending_count = total_logs.filter(status='pending').count()
    
    total_likes = total_logs.aggregate(total=Sum('likes_count'))['total'] or 0
    total_comments = total_logs.aggregate(total=Sum('comments_count'))['total'] or 0
    total_shares = total_logs.aggregate(total=Sum('shares_count'))['total'] or 0
    
    recent_activity = total_logs.order_by('-created_at')[:50]
    
    posts_by_status = WebinarPost.objects.filter(created_by=user).values('status').annotate(count=Count('id'))
    
    return render(request, 'admin_dash/webinar_analytics.html', {
        'stats': {
            'success_count': success_count,
            'failed_count': failed_count,
            'pending_count': pending_count,
            'total_likes': total_likes,
            'total_comments': total_comments,
            'total_shares': total_shares,
        },
        'recent_activity': recent_activity,
        'posts_by_status': list(posts_by_status),
    })


@login_required
def webinar_export_report(request):
    from .models import WebinarGroup, WebinarPostLog
    import csv
    
    user = request.user
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="webinar_report_{timezone.now().strftime("%Y%m%d_%H%M")}.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Group Name', 'URL', 'Member Count', 'Size Category', 'Status', 'Engagement', 'Niche', 'Last Posted', 'Posts Count'])
    
    groups = WebinarGroup.objects.filter(created_by=user).order_by('-member_count')
    for g in groups:
        writer.writerow([
            g.name,
            g.url,
            g.member_count,
            g.get_size_category_display(),
            g.status,
            g.engagement,
            g.niche,
            g.last_posted.strftime('%Y-%m-%d %H:%M') if g.last_posted else 'Never',
            g.posts_count,
        ])
    
    return response
