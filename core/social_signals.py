from django.contrib.auth import login
from django.dispatch import receiver
from allauth.socialaccount.signals import pre_social_login, social_account_added
from allauth.account.models import EmailAddress
import logging

logger = logging.getLogger(__name__)


@receiver(pre_social_login)
def on_social_login(request, sociallogin, **kwargs):
    try:
        user = sociallogin.user
        if user and user.is_active:
            sociallogin.state['process'] = 'login'
    except Exception as e:
        logger.error(f"pre_social_login error: {e}")


@receiver(social_account_added)
def on_social_account_added(request, sociallogin, **kwargs):
    try:
        user = sociallogin.user
        email = sociallogin.account.extra_data.get('email', '')
        
        if email:
            if not EmailAddress.objects.filter(user=user, email__iexact=email).exists():
                EmailAddress.objects.create(
                    user=user,
                    email=email,
                    verified=True,
                    primary=True
                )
    except Exception as e:
        logger.error(f"social_account_added error: {e}")