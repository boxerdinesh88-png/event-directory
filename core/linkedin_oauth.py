"""
LinkedIn OAuth 2.0 Service - Production-Ready Implementation

Scope documentation:
- w_member_social: Required for posting on behalf of users (granted by "Share on LinkedIn" product)
- r_liteprofile: NOT needed and NOT authorized by default - DO NOT USE
- openid/profile/email: OpenID Connect scopes - NOT compatible with LinkedIn OAuth 2.0

This module provides:
1. build_linkedin_oauth_url() - Build authorization URL with ONLY w_member_social
2. exchange_code_for_token() - Exchange authorization code for access token
3. get_linkedin_user_info() - Get user profile (may fail without r_liteprofile)
4. store_linkedin_token() - Store token in database
5. post_to_linkedin_v2() - Post using Posts API v2
"""

import requests
import logging
from urllib.parse import urlencode
from django.conf import settings

logger = logging.getLogger(__name__)

# ─── LinkedIn API Endpoints ────────────────────────────────────────────────────
LINKEDIN_AUTH_URL = 'https://www.linkedin.com/oauth/v2/authorization'
LINKEDIN_TOKEN_URL = 'https://www.linkedin.com/oauth/v2/accessToken'
LINKEDIN_API_V2 = 'https://api.linkedin.com/v2'
LINKEDIN_API_REST = 'https://api.linkedin.com/rest'

# ─── Scopes ───────────────────────────────────────────────────────────────────
# IMPORTANT: Use ONLY w_member_social
# r_liteprofile requires special approval and is NOT needed for posting
LINKEDIN_SCOPE = 'w_member_social'


def get_linkedin_config():
    """
    Get LinkedIn app credentials from Django settings.
    
    Settings should be in .env:
        LINKEDIN_CLIENT_ID=your_client_id
        LINKEDIN_CLIENT_SECRET=your_client_secret
        LINKEDIN_REDIRECT_URI=http://127.0.0.1:8000/linkedin/callback/
    """
    return {
        'client_id': getattr(settings, 'LINKEDIN_CLIENT_ID', ''),
        'client_secret': getattr(settings, 'LINKEDIN_CLIENT_SECRET', ''),
        'redirect_uri': getattr(settings, 'LINKEDIN_REDIRECT_URI', 'http://127.0.0.1:8000/linkedin/callback/'),
    }


def build_linkedin_oauth_url(state=None, scope=None, redirect_uri=None):
    """
    Build the LinkedIn OAuth 2.0 authorization URL.
    
    The authorization URL format:
        https://www.linkedin.com/oauth/v2/authorization?
            response_type=code&
            client_id=YOUR_CLIENT_ID&
            redirect_uri=http://127.0.0.1:8000/linkedin/callback/&
            state=RANDOM_STATE&
            scope=w_member_social
    
    Args:
        state: CSRF protection token. Auto-generated if not provided.
        scope: OAuth scope. Defaults to 'w_member_social' only.
        redirect_uri: Callback URL. Defaults to settings.LINKEDIN_REDIRECT_URI.
    
    Returns:
        tuple: (authorization_url, state_token)
    
    Example:
        >>> url, state = build_linkedin_oauth_url()
        >>> print(url)
        https://www.linkedin.com/oauth/v2/authorization?response_type=code&...
    """
    import secrets
    
    if state is None:
        state = secrets.token_urlsafe(32)
    
    if scope is None:
        scope = LINKEDIN_SCOPE
    
    config = get_linkedin_config()
    
    # Validate credentials
    if not config['client_id']:
        raise ValueError(
            "LINKEDIN_CLIENT_ID is not configured. "
            "Add it to your .env file: LINKEDIN_CLIENT_ID=your_client_id"
        )
    
    if not config['client_secret']:
        raise ValueError(
            "LINKEDIN_CLIENT_SECRET is not configured. "
            "Add it to your .env file: LINKEDIN_CLIENT_SECRET=your_client_secret"
        )
    
    if redirect_uri is None:
        redirect_uri = config['redirect_uri']
    
    params = {
        'response_type': 'code',
        'client_id': config['client_id'],
        'redirect_uri': redirect_uri,
        'state': state,
        'scope': scope,
    }
    
    auth_url = f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"
    logger.info(f"Built LinkedIn OAuth URL. State: {state[:10]}... Scope: {scope}")
    
    return auth_url, state


def exchange_code_for_token(authorization_code, redirect_uri=None):
    """
    Exchange authorization code for access token.
    
    The authorization code expires in 30 minutes and can only be used once.
    
    Args:
        authorization_code: The code received from LinkedIn callback URL
        redirect_uri: Must match the redirect_uri used in authorization request
    
    Returns:
        dict with keys:
            - success (bool)
            - access_token (str, if successful)
            - expires_in (int, seconds until expiry, ~5184000 = 60 days)
            - refresh_token (str, if available)
            - scope (str, granted scopes)
            - error (str, if failed)
            - error_code (int, HTTP status code if failed)
    
    Example token response:
        {
            "access_token": "AQV...xyz",
            "expires_in": 5184000,
            "refresh_token": "...",
            "scope": "w_member_social",
            "token_type": "Bearer"
        }
    """
    config = get_linkedin_config()
    
    if not authorization_code:
        return {'success': False, 'error': 'Authorization code is required'}
    
    if redirect_uri is None:
        redirect_uri = config['redirect_uri']
    
    data = {
        'grant_type': 'authorization_code',
        'code': authorization_code,
        'client_id': config['client_id'],
        'client_secret': config['client_secret'],
        'redirect_uri': redirect_uri,
    }
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    
    try:
        logger.info("Exchanging authorization code for access token...")
        response = requests.post(
            LINKEDIN_TOKEN_URL,
            data=data,
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            token_data = response.json()
            logger.info(f"Token obtained. Expires in: {token_data.get('expires_in')} seconds")
            return {
                'success': True,
                'access_token': token_data.get('access_token'),
                'expires_in': token_data.get('expires_in'),
                'refresh_token': token_data.get('refresh_token'),
                'scope': token_data.get('scope'),
                'token_type': token_data.get('token_type', 'Bearer'),
            }
        else:
            try:
                error_data = response.json()
                error_msg = error_data.get('error_description', error_data.get('error', 'Unknown error'))
            except Exception:
                error_msg = f'HTTP {response.status_code}'
            
            logger.error(f"Token exchange failed: {error_msg}")
            return {
                'success': False,
                'error': error_msg,
                'error_code': response.status_code,
            }
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Token exchange request failed: {str(e)}")
        return {'success': False, 'error': str(e)}


def get_linkedin_user_id(access_token):
    """
    Get the current user's LinkedIn member ID using v2/me API.
    
    Returns:
        dict with:
            - success (bool)
            - id (str, numeric member ID) 
            - localizedFirstName (str)
            - localizedLastName (str)
            - error (str, if failed)
    
    Note: This requires r_liteprofile scope. If not granted, it will fail.
    Use the returned ID as: urn:li:person:{id}
    """
    headers = {
        'Authorization': f'Bearer {access_token}',
        'LinkedIn-Version': '202504',
        'X-Restli-Protocol-Version': '2.0.0',
    }
    
    try:
        response = requests.get(
            f'{LINKEDIN_API_V2}/me',
            headers=headers,
            timeout=15
        )
        
        if response.status_code == 200:
            data = response.json()
            return {
                'success': True,
                'id': data.get('id'),
                'localizedFirstName': data.get('localizedFirstName'),
                'localizedLastName': data.get('localizedLastName'),
            }
        else:
            return {
                'success': False,
                'error': f'HTTP {response.status_code}: {response.text}'
            }
    except Exception as e:
        return {'success': False, 'error': str(e)}


def store_linkedin_token(user, access_token, expires_in=None, scope=None, member_id=None):
    """
    Store LinkedIn access token in the SocialPlatformConfig model.
    
    Args:
        user: Django User instance
        access_token: The OAuth access token string
        expires_in: Token expiry in seconds
        scope: Granted OAuth scopes
        member_id: LinkedIn member ID (numeric)
    
    Returns:
        SocialPlatformConfig instance if successful, None if failed
    """
    from .models import SocialPlatformConfig
    
    try:
        config, created = SocialPlatformConfig.objects.update_or_create(
            platform='linkedin',
            created_by=user,
            defaults={
                'access_token': access_token,
                'is_connected': True,
                'is_primary': True,
                'extra_field': member_id or '',
            }
        )
        logger.info(f"Stored LinkedIn token for user {user.id} (created={created})")
        return config
    except Exception as e:
        logger.error(f"Failed to store LinkedIn token: {str(e)}")
        return None


def get_linkedin_token(user):
    """
    Retrieve stored LinkedIn access token for a user.
    
    Returns:
        str: Access token or None if not found
    """
    from .models import SocialPlatformConfig
    
    try:
        config = SocialPlatformConfig.objects.get(
            platform='linkedin',
            created_by=user,
            is_connected=True
        )
        return config.access_token if config.access_token else None
    except SocialPlatformConfig.DoesNotExist:
        return None
