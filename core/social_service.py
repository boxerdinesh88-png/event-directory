"""
Social Media API Service - All platforms with text, image, and video support
"""
import json
import requests
import logging
import os
import sys
import time as _time
import ipaddress
from urllib.parse import urlparse
from django.conf import settings

logger = logging.getLogger(__name__)


def get_media_public_base():
    """Get and validate MEDIA_PUBLIC_BASE. Returns the base URL or empty string."""
    base = getattr(settings, 'MEDIA_PUBLIC_BASE', '').strip().rstrip('/')
    return base


def get_public_media_url(relative_path):
    """
    Build a public HTTPS URL for a media file.

    Usage:
        url = get_public_media_url('social/abc123.jpg')
        # -> 'https://xyz123.ngrok-free.app/media/social/abc123.jpg'
    """
    base = get_media_public_base()
    media_url = getattr(settings, 'MEDIA_URL', '/media/')

    if not base:
        print("[ERROR] MEDIA_PUBLIC_BASE is empty!", file=sys.stderr)
        print("[ERROR] Open .env file and set:", file=sys.stderr)
        print("[ERROR]   MEDIA_PUBLIC_BASE=https://your-real-ngrok-url.ngrok-free.app", file=sys.stderr)
        print("[ERROR] Steps: 1) Run 'ngrok http 8000'  2) Copy https URL  3) Paste in .env  4) Restart server", file=sys.stderr)
        return f"http://localhost{media_url}{relative_path}"

    if not base.startswith('https://'):
        print(f"[ERROR] MEDIA_PUBLIC_BASE must start with https://, got: {base}", file=sys.stderr)
        return f"http://localhost{media_url}{relative_path}"

    return f"{base}{media_url}{relative_path}"


def ensure_public_https_url(url):
    """
    Convert a localhost/private URL to a public HTTPS URL using MEDIA_PUBLIC_BASE.

    Handles these patterns:
        http://127.0.0.1:8000/media/social/abc.jpg
        http://localhost:8000/media/social/abc.jpg
        http://localhost/media/social/abc.jpg

    Returns converted URL or original if already public.
    """
    if not url:
        return url

    base = get_media_public_base()

    # Already HTTPS public URL — return as-is
    if url.startswith('https://'):
        parsed = urlparse(url)
        host = parsed.hostname or ''
        if host not in ('localhost', '127.0.0.1', '0.0.0.0'):
            try:
                ip = ipaddress.ip_address(host)
                if not ip.is_private and not ip.is_loopback:
                    return url
            except ValueError:
                return url  # domain name, OK

    # Extract the path from the URL
    parsed = urlparse(url)
    path = parsed.path or ''

    # If we have a public base, rewrite the URL
    if base and base.startswith('https://'):
        if path:
            new_url = f"{base}{path}"
            print(f"[DEBUG] URL converted: {url} -> {new_url}", file=sys.stderr)
            return new_url

    # Cannot convert — print exact fix instructions
    print(f"[ERROR] Cannot convert '{url}' to public HTTPS URL", file=sys.stderr)
    if not base:
        print("[ERROR] MEDIA_PUBLIC_BASE is not set in .env!", file=sys.stderr)
    elif not base.startswith('https://'):
        print(f"[ERROR] MEDIA_PUBLIC_BASE must start with https://, got: {base}", file=sys.stderr)
    print("[ERROR] Fix: Open .env, set MEDIA_PUBLIC_BASE=https://your-ngrok-url", file=sys.stderr)
    print("[ERROR] Steps: 1) ngrok http 8000  2) Copy https URL  3) Paste in .env  4) Restart", file=sys.stderr)
    return url


def verify_public_url(url, media_type=None):
    """
    Verify that a URL is publicly accessible via HTTPS.
    Returns (True, 'OK') or (False, 'error reason').

    For videos, also checks that content-type starts with video/.
    """
    if not url:
        return False, "URL is empty"

    parsed = urlparse(url)

    # Check HTTPS
    if parsed.scheme != 'https':
        return False, f"URL must use HTTPS, got: {parsed.scheme}://"

    # Check not localhost/private
    host = parsed.hostname or ''
    if host in ('localhost', '127.0.0.1', '0.0.0.0'):
        return False, f"URL points to localhost ({host}) - Instagram cannot access this"

    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback:
            return False, f"URL points to private IP ({host}) - Instagram cannot access this"
    except ValueError:
        pass  # domain name, OK

    # Try to verify URL is accessible (HEAD first, fall back to GET)
    try:
        resp = requests.head(url, timeout=10, allow_redirects=True)
        # Some servers return 405 for HEAD — fall back to GET
        if resp.status_code in (405, 501):
            print(f"[DEBUG] HEAD returned {resp.status_code}, retrying with GET...", flush=True)
            resp = requests.get(url, timeout=10, allow_redirects=True, stream=True)
            # Close immediately — we only need status and headers
            resp.close()

        if resp.status_code == 200:
            content_type = resp.headers.get('content-type', '')
            content_length = resp.headers.get('content-length', 'unknown')
            print(f"[DEBUG] URL OK: {url}", flush=True)
            print(f"[DEBUG]   content-type: {content_type}", flush=True)
            print(f"[DEBUG]   content-length: {content_length}", flush=True)

            # Warn if content-type doesn't match expected media type
            if media_type == 'video' and not content_type.startswith('video/'):
                print(f"[WARNING] Expected video/* content-type, got: {content_type}", flush=True)
                print(f"[WARNING] Instagram may reject this URL", flush=True)

            return True, "OK"
        elif resp.status_code == 403:
            return False, f"URL returned 403 Forbidden - file requires authentication or is blocked"
        elif resp.status_code == 404:
            return False, f"URL returned 404 Not Found - check ngrok is running and pointing to correct port"
        elif resp.status_code == 400:
            return False, f"URL returned HTTP 400 - the remote server rejected the request. Check ngrok tunnel is active"
        else:
            return False, f"URL returned HTTP {resp.status_code}"
    except requests.exceptions.ConnectionError:
        return False, "Cannot connect to URL - is ngrok running?"
    except requests.exceptions.Timeout:
        return False, "URL request timed out"
    except Exception as e:
        return False, f"URL check failed: {str(e)}"

GRAPH_API = 'https://graph.facebook.com/v19.0'


def get_instagram_accounts(access_token):
    """
    Fetch all Instagram Business Accounts linked to the user's Facebook Pages.
    Returns list of dicts: [{'ig_user_id': '...', 'page_id': '...', 'page_name': '...', 'ig_username': '...'}]
    """
    accounts = []
    try:
        # Step 1: Get user's Facebook Pages
        resp = requests.get(
            f'{GRAPH_API}/me/accounts',
            params={'access_token': access_token, 'fields': 'id,name,instagram_business_account'},
            timeout=15
        )
        data = resp.json()
        pages = data.get('data', [])

        for page in pages:
            ig = page.get('instagram_business_account')
            if ig:
                ig_id = ig.get('id')
                # Step 2: Get IG username
                ig_resp = requests.get(
                    f'{GRAPH_API}/{ig_id}',
                    params={'access_token': access_token, 'fields': 'username'},
                    timeout=10
                )
                ig_data = ig_resp.json()
                accounts.append({
                    'ig_user_id': ig_id,
                    'page_id': page.get('id'),
                    'page_name': page.get('name'),
                    'ig_username': ig_data.get('username', 'unknown'),
                })
    except Exception as e:
        logger.error(f"get_instagram_accounts error: {e}")
    return accounts


def validate_ig_user_id(access_token, ig_user_id):
    """
    Validate that an IG User ID is accessible with the given token.
    Returns (True, 'OK') or (False, 'error reason').
    Also checks required permissions for content publishing.
    """
    if not ig_user_id:
        return False, "IG User ID is empty"

    # Check if the token has required permissions
    try:
        perm_resp = requests.get(
            f'{GRAPH_API}/me/permissions',
            params={'access_token': access_token},
            timeout=10
        )
        perm_data = perm_resp.json()
        granted = {p['permission'] for p in perm_data.get('data', []) if p.get('status') == 'granted'}
        required = ['instagram_basic', 'instagram_content_publish', 'pages_read_engagement']
        missing = [p for p in required if p not in granted]
        if missing:
            return False, f"Missing permissions: {', '.join(missing)}. Re-login with Facebook and grant all Instagram permissions."
    except Exception:
        pass

    # Check if the IG User ID is accessible
    try:
        resp = requests.get(
            f'{GRAPH_API}/{ig_user_id}',
            params={'access_token': access_token, 'fields': 'id,username'},
            timeout=10
        )
        data = resp.json()
        if 'id' in data:
            return True, f"OK (@{data.get('username', 'unknown')})"
        if 'error' in data:
            err = data['error']
            return False, f"Cannot access IG account: {err.get('message', 'Unknown error')}. Check token permissions."
    except Exception as e:
        return False, f"API error: {e}"

    # Fallback: try to find accounts
    accounts = get_instagram_accounts(access_token)
    if accounts:
        ids = ', '.join([f"@{a['ig_username']} ({a['ig_user_id']})" for a in accounts])
        return False, f"Invalid IG User ID. Your accounts: {ids}"
    else:
        return False, "No Instagram Business Account found. Link an IG account to your Facebook Page."


def upload_to_cloudinary(file_path, resource_type='auto'):
    """
    Upload a local file to Cloudinary and return the public HTTPS URL.
    For videos, applies Instagram-compatible format (MP4/H.264/AAC).

    Returns: (url, error) tuple
        url: public HTTPS URL on success, None on failure
        error: error message string, None on success
    """
    cloud_name = getattr(settings, 'CLOUDINARY_CLOUD_NAME', '')
    api_key    = getattr(settings, 'CLOUDINARY_API_KEY', '')
    api_secret = getattr(settings, 'CLOUDINARY_API_SECRET', '')

    if not all([cloud_name, api_key, api_secret]):
        return None, "Cloudinary not configured (set CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET in .env)"

    if not os.path.isfile(file_path):
        return None, f"File not found: {file_path}"

    try:
        import cloudinary
        import cloudinary.uploader

        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
            secure=True
        )

        ext = os.path.splitext(file_path)[1].lower()
        is_video = ext in VIDEO_EXTENSIONS

        if is_video:
            resource_type = 'video'
            print(f"CLOUDINARY: Uploading video {file_path}...", flush=True)

            # Upload with transformation preset for Instagram
            result = cloudinary.uploader.upload(
                file_path,
                resource_type='video',
                folder='florida_events/social',
                # Instagram-compatible settings
                format='mp4',
                video_codec='h264',
                audio_codec='aac',
                bit_rate='2000k',
                timeout=120,
            )

            # Get the secure URL - Cloudinary will auto-convert to mp4
            url = result.get('secure_url', '')
            print(f"CLOUDINARY: Video uploaded -> {url}", flush=True)
        else:
            print(f"CLOUDINARY: Uploading image {file_path}...", flush=True)
            result = cloudinary.uploader.upload(
                file_path,
                resource_type='image',
                folder='florida_events/social',
                timeout=60,
            )
            url = result.get('secure_url', '')
            print(f"CLOUDINARY: Image uploaded -> {url}", flush=True)

        if url:
            return url, None
        return None, "Cloudinary upload returned no URL"

    except ImportError:
        return None, "cloudinary package not installed. Run: pip install cloudinary"
    except Exception as e:
        return None, f"Cloudinary upload failed: {str(e)}"

def _facebook_page_token(page_id, token):
    # Ensure Page Access Token even if user token supplied
    try:
        resp = requests.get(
            f"{GRAPH_API}/{page_id}",
            params={'fields': 'access_token', 'access_token': token},
            timeout=10
        )
        data = resp.json()
        if resp.status_code == 200 and data.get('access_token'):
            return data['access_token']
    except Exception:
        pass
    return token


# ─── FACEBOOK ─────────────────────────────────────────────────────

def post_to_facebook(page_id, access_token, message, link=None):
    """Post text to Facebook Page."""
    try:
        page_token = _facebook_page_token(page_id, access_token)
        url = f"{GRAPH_API}/{page_id}/feed"
        data = {'message': message, 'access_token': page_token}
        if link:
            data['link'] = link

        resp = requests.post(url, data=data, timeout=30)
        result = resp.json()

        if resp.status_code == 200 and 'id' in result:
            post_id = result['id']
            return {
                'success': True,
                'post_id': post_id,
                'post_url': f"https://www.facebook.com/{post_id}",
                'error': None
            }
        else:
            error_msg = result.get('error', {}).get('message', 'Unknown error')
            return {'success': False, 'post_id': None, 'post_url': None, 'error': error_msg}
    except Exception as e:
        return {'success': False, 'post_id': None, 'post_url': None, 'error': str(e)}


def post_to_facebook_photo(page_id, access_token, message, photo_url=None, file_path=None):
    """
    Post image to Facebook Page.
    - file_path: local file upload via multipart (preferred)
    - photo_url: publicly accessible URL fallback
    """
    try:
        page_token = _facebook_page_token(page_id, access_token)
        url = f"{GRAPH_API}/{page_id}/photos"
        data = {'message': message, 'access_token': page_token}

        if file_path and os.path.isfile(file_path):
            with open(file_path, 'rb') as f:
                files = {'source': (os.path.basename(file_path), f, 'image/jpeg')}
                resp = requests.post(url, data=data, files=files, timeout=120)
        elif photo_url:
            data['url'] = photo_url
            resp = requests.post(url, data=data, timeout=60)
        else:
            return {'success': False, 'post_id': None, 'post_url': None, 'error': 'No photo provided'}

        result = resp.json()
        if resp.status_code == 200 and 'id' in result:
            post_id = result['id']
            return {
                'success': True,
                'post_id': post_id,
                'post_url': f"https://www.facebook.com/{page_id}/photos/{post_id}",
                'error': None
            }
        else:
            error_msg = result.get('error', {}).get('message', str(result))
            return {'success': False, 'post_id': None, 'post_url': None, 'error': error_msg}
    except Exception as e:
        return {'success': False, 'post_id': None, 'post_url': None, 'error': str(e)}


def post_to_facebook_video(page_id, access_token, message, video_url=None, file_path=None):
    """
    Post video to Facebook Page.
    - file_path: local file upload via multipart (preferred)
    - video_url: publicly accessible URL fallback
    """
    try:
        page_token = _facebook_page_token(page_id, access_token)
        url = f"{GRAPH_API}/{page_id}/videos"
        data = {'description': message, 'access_token': page_token}

        if file_path and os.path.isfile(file_path):
            with open(file_path, 'rb') as f:
                files = {'source': (os.path.basename(file_path), f, 'video/mp4')}
                resp = requests.post(url, data=data, files=files, timeout=300)
        elif video_url:
            data['file_url'] = video_url
            resp = requests.post(url, data=data, timeout=300)
        else:
            return {'success': False, 'post_id': None, 'post_url': None, 'error': 'No video provided'}

        result = resp.json()
        if resp.status_code == 200 and 'id' in result:
            video_id = result['id']
            return {
                'success': True,
                'post_id': video_id,
                'post_url': f"https://www.facebook.com/{page_id}/videos/{video_id}",
                'error': None
            }
        else:
            error_msg = result.get('error', {}).get('message', str(result))
            return {'success': False, 'post_id': None, 'post_url': None, 'error': error_msg}
    except Exception as e:
        return {'success': False, 'post_id': None, 'post_url': None, 'error': str(e)}


def get_facebook_page_info(page_id, access_token):
    """Get Facebook Page info to verify credentials."""
    try:
        resp = requests.get(
            f"{GRAPH_API}/{page_id}",
            params={'fields': 'id,name,link', 'access_token': access_token},
            timeout=15
        )
        if resp.status_code == 200:
            return {'success': True, 'data': resp.json()}
        return {'success': False, 'error': resp.json().get('error', {}).get('message', 'Error')}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ─── TWITTER ──────────────────────────────────────────────────────

def post_to_twitter(api_key, api_secret, access_token, access_token_secret, message, media_url=None, file_path=None):
    """Post to X (Twitter) with optional media."""
    try:
        import base64
        import hashlib
        import hmac
        import time
        import urllib.parse
        import uuid

        def _oauth_sig(method, url, params, consumer_secret, token_secret):
            sorted_params = sorted(params.items())
            param_str = '&'.join(f'{urllib.parse.quote(str(k))}={urllib.parse.quote(str(v))}' for k, v in sorted_params)
            base_str = f'{method}&{urllib.parse.quote(url)}&{urllib.parse.quote(param_str)}'
            signing_key = f'{urllib.parse.quote(consumer_secret)}&{urllib.parse.quote(token_secret)}'
            return base64.b64encode(
                hmac.new(signing_key.encode(), base_str.encode(), hashlib.sha1).digest()
            ).decode()

        def _oauth_hdr(url, method, params, ck, cs, tok, tsecret):
            params.update({
                'oauth_consumer_key': ck,
                'oauth_nonce': uuid.uuid4().hex,
                'oauth_signature_method': 'HMAC-SHA1',
                'oauth_timestamp': str(int(time.time())),
                'oauth_token': tok,
                'oauth_version': '1.0',
            })
            params['oauth_signature'] = _oauth_sig(method, url, params, cs, tsecret)
            auth = 'OAuth ' + ', '.join(
                f'{urllib.parse.quote(str(k))}="{urllib.parse.quote(str(v))}"'
                for k, v in sorted(params.items()) if k.startswith('oauth_')
            )
            return auth

        media_ids = []

        # Upload media if file provided
        if file_path and os.path.isfile(file_path):
            upload_url = 'https://upload.twitter.com/1.1/media/upload.json'
            headers = {'Authorization': _oauth_hdr(upload_url, 'POST', {}, api_key, api_secret, access_token, access_token_secret)}
            with open(file_path, 'rb') as f:
                files = {'media': (os.path.basename(file_path), f)}
                upload_resp = requests.post(upload_url, headers=headers, files=files, timeout=120)
                if upload_resp.status_code == 200:
                    media_id = upload_resp.json().get('media_id_string')
                    if media_id:
                        media_ids.append(media_id)

        # Post the tweet
        url = 'https://api.twitter.com/2/tweets'
        headers = {'Authorization': _oauth_hdr(url, 'POST', {}, api_key, api_secret, access_token, access_token_secret)}
        payload = {'text': message}
        if media_ids:
            payload['media'] = {'media_ids': media_ids}

        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        result = resp.json()

        if resp.status_code in (200, 201) and 'data' in result:
            tweet_id = result['data']['id']
            return {
                'success': True,
                'post_id': tweet_id,
                'post_url': f'https://twitter.com/i/status/{tweet_id}',
                'error': None
            }
        else:
            error_msg = result.get('errors', [{}])[0].get('message', str(result))
            return {'success': False, 'post_id': None, 'post_url': None, 'error': error_msg}
    except Exception as e:
        return {'success': False, 'post_id': None, 'post_url': None, 'error': str(e)}


# ─── LINKEDIN ─────────────────────────────────────────────────────

def post_to_linkedin(access_token, org_urn, message, link=None, image_url=None):
    """
    Post to LinkedIn using the Posts API v2 (or legacy ugcPosts API).
    
    Args:
        access_token: LinkedIn OAuth access token
        org_urn: Can be:
                 - Full URN: urn:li:person:123456 or urn:li:organization:123456
                 - Numeric ID: 123456 (auto-detects person vs org)
                 - Vanity name: johndoe123 (from linkedin.com/in/johndoe123)
                   For vanity names, person is assumed (vanity names are for profiles)
        message: Post text/commentary
        link: Optional article URL
        image_url: Optional image URL
    
    Returns:
        dict with success, post_id, post_url, error
    """
    def is_numeric(s):
        return bool(s and s.isdigit())
    
    def parse_urn(raw):
        """Parse various input formats into (type, id) tuple."""
        raw = (raw or '').strip()
        
        if not raw or raw == 'unknown':
            return None, None
        
        # Full URN provided
        if raw.startswith('urn:'):
            parts = raw.split(':')
            if len(parts) >= 4:
                urn_type = parts[2]  # 'person' or 'organization'
                urn_id = parts[3]
                # If ID is non-numeric (vanity name), force to person
                if not is_numeric(urn_id):
                    urn_type = 'person'
                return urn_type, urn_id
            return None, None
        
        # Just the ID portion - figure out if person or org
        if is_numeric(raw):
            # Numeric IDs are ambiguous; default to person for posting
            return 'person', raw
        
        # Non-numeric = vanity name = always a person profile
        return 'person', raw
    
    try:
        urn_type, urn_id = parse_urn(org_urn)
        
        if urn_type is None or urn_id is None:
            # Try to get the user's profile ID from the access token
            logger.info("Could not parse org_urn, attempting to get user profile ID...")
            user_id = None
            
            for profile_url in [
                'https://api.linkedin.com/v2/me',
                'https://api.linkedin.com/rest/me',
            ]:
                try:
                    headers = {
                        'Authorization': f'Bearer {access_token}',
                        'LinkedIn-Version': '202504',
                    }
                    resp = requests.get(profile_url, headers=headers, timeout=15)
                    if resp.status_code == 200:
                        profile_data = resp.json()
                        user_id = profile_data.get('id')
                        if user_id:
                            urn_type = 'person'
                            urn_id = user_id
                            logger.info(f"Got user ID from {profile_url}: {user_id}")
                            break
                except Exception:
                    continue
            
            if urn_id is None:
                return {
                    'success': False,
                    'post_id': None,
                    'post_url': None,
                    'error': (
                        'Could not determine your LinkedIn Profile ID. '
                        'Please reconnect with the "Share on LinkedIn" product enabled, '
                        'or enter your numeric Profile ID manually in Social Media settings.'
                    )
                }
        
        # Validate: person URNs must have numeric IDs
        if urn_type == 'person' and not is_numeric(urn_id):
            return {
                'success': False,
                'post_id': None,
                'post_url': None,
                'error': (
                    f'Invalid Profile ID format. "{urn_id}" appears to be a vanity name, not a numeric ID. '
                    'LinkedIn API requires your NUMERIC member ID (not the URL slug). '
                    'To find your numeric ID: Go to your LinkedIn profile page → View Source → search for "memberId" or "profileId". '
                    'Or visit: https://www.linkedin.com/developers/apps and ensure "Share on LinkedIn" is enabled, then reconnect your account.'
                )
            }
        
        # Build the author URN
        author = f'urn:li:{urn_type}:{urn_id}'
        
        # Try new Posts API first (v2)
        try:
            url = 'https://api.linkedin.com/rest/posts'
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
                'LinkedIn-Version': '202504',
                'X-Restli-Protocol-Version': '2.0.0',
            }
            
            # Build payload for Posts API v2
            payload = {
                'author': author,
                'commentary': message[:3000],  # Max 3000 chars
                'visibility': 'PUBLIC',
                'distribution': {
                    'feedDistribution': 'MAIN_FEED',
                    'targetEntities': [],
                    'thirdPartyDistributionChannels': []
                },
                'lifecycleState': 'PUBLISHED',
                'isReshareDisabledByAuthor': False
            }
            
            # Add content if link or image provided
            if link:
                payload['content'] = {
                    'article': {
                        'source': link,
                        'title': message[:100],
                        'description': message[:200],
                    }
                }
            elif image_url:
                payload['content'] = {
                    'media': {
                        'id': image_url,
                        'title': 'Image',
                    }
                }
            
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            
            if resp.status_code in (200, 201):
                post_id = resp.headers.get('x-restli-id', '')
                return {
                    'success': True,
                    'post_id': post_id,
                    'post_url': f'https://www.linkedin.com/feed/update/{post_id}',
                    'error': None
                }
            
            # If Posts API fails, try legacy ugcPosts API
            error_data = resp.json() if resp.content else {}
            error_msg = error_data.get('message', error_data.get('error', ''))
            
            # Only fallback for certain errors
            if resp.status_code == 403:
                # Permission denied - try legacy API
                pass
            elif 'INVALID_AUTHOR' in str(error_msg):
                # Invalid author - try legacy API
                pass
            else:
                return {'success': False, 'post_id': None, 'post_url': None, 'error': error_msg or f'HTTP {resp.status_code}'}
                
        except Exception:
            pass
        
        # Fallback to legacy ugcPosts API
        url = 'https://api.linkedin.com/v2/ugcPosts'
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'X-Restli-Protocol-Version': '2.0.0'
        }
        
        if image_url:
            specific_content = {
                'com.linkedin.ugc.ShareContent': {
                    'shareCommentary': {'text': message},
                    'shareMediaCategory': 'IMAGE',
                    'media': [{'status': 'READY', 'originalUrl': image_url}]
                }
            }
        elif link:
            specific_content = {
                'com.linkedin.ugc.ShareContent': {
                    'shareCommentary': {'text': message},
                    'shareMediaCategory': 'ARTICLE',
                    'media': [{'status': 'READY', 'originalUrl': link}]
                }
            }
        else:
            specific_content = {
                'com.linkedin.ugc.ShareContent': {
                    'shareCommentary': {'text': message},
                    'shareMediaCategory': 'NONE'
                }
            }

        payload = {
            'author': author,
            'lifecycleState': 'PUBLISHED',
            'specificContent': specific_content,
            'visibility': {'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC'}
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=30)

        if resp.status_code in (200, 201):
            post_id = resp.json().get('id', '')
            return {
                'success': True,
                'post_id': post_id,
                'post_url': f'https://www.linkedin.com/feed/update/{post_id}',
                'error': None
            }
        else:
            error_data = resp.json()
            error_msg = error_data.get('message', error_data.get('error', f'HTTP {resp.status_code}'))
            return {'success': False, 'post_id': None, 'post_url': None, 'error': error_msg}
    except Exception as e:
        return {'success': False, 'post_id': None, 'post_url': None, 'error': str(e)}


# ─── INSTAGRAM ────────────────────────────────────────────────────

VIDEO_EXTENSIONS = ('.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv')
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')


def _get_ffmpeg_exe():
    """Get path to ffmpeg binary. Uses imageio-ffmpeg bundle or system ffmpeg."""
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        return os.fspath(get_ffmpeg_exe())
    except ImportError:
        pass
    import shutil
    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg:
        return os.fspath(system_ffmpeg)
    return None


def _probe_video(file_path):
    """Probe video file and return dict with codec, container, duration info."""
    ffmpeg = _get_ffmpeg_exe()
    if not ffmpeg:
        return None
    import subprocess
    try:
        result = subprocess.run(
            [ffmpeg, '-i', file_path],
            capture_output=True, text=True, timeout=15
        )
        output = result.stderr
        info = {'raw': output}
        for line in output.split('\n'):
            line = line.strip()
            if 'Video:' in line:
                # Example: Stream #0:0: Video: hevc (Main), yuvj420p, 1280x720
                parts = line.split('Video:')[1].strip()
                codec = parts.split(',')[0].strip().split('(')[0].strip()
                info['video_codec'] = codec
            if 'Audio:' in line:
                parts = line.split('Audio:')[1].strip()
                codec = parts.split(',')[0].strip().split('(')[0].strip()
                info['audio_codec'] = codec
            if 'Duration:' in line:
                # Example: Duration: 00:00:16.67
                dur = line.split('Duration:')[1].split(',')[0].strip()
                info['duration'] = dur
        return info
    except Exception:
        return None


def _transcode_for_instagram(input_path):
    """
    Transcode a video file to Instagram-compatible format.
    - MP4 container
    - H.264 Baseline profile (max compatibility)
    - AAC audio
    - Max width 1080px (Instagram limit)
    - Max 90 seconds for Reels

    Returns: (output_path, error) tuple.
    output_path is a temp .mp4 file on success.
    """
    ffmpeg = _get_ffmpeg_exe()
    if not ffmpeg:
        return None, "ffmpeg not found. Install with: pip install imageio-ffmpeg"

    if not os.path.isfile(input_path):
        return None, f"File not found: {input_path}"

    import subprocess
    import tempfile

    output_path = os.path.join(tempfile.gettempdir(), f"ig_{os.path.basename(input_path)}.mp4")

    # Build ffmpeg command for Instagram-compatible output
    cmd = [
        ffmpeg, '-y',                   # overwrite output
        '-i', input_path,               # input file
        '-c:v', 'libx264',              # H.264 video codec
        '-profile:v', 'baseline',       # Baseline profile (max compatibility)
        '-level', '3.0',                # H.264 level 3.0
        '-pix_fmt', 'yuv420p',          # Required pixel format
        '-vf', 'scale=\'min(1080,iw)\':-2',  # Max width 1080, keep aspect ratio
        '-preset', 'fast',              # Fast encoding
        '-crf', '23',                   # Quality (lower = better, 23 is good balance)
        '-c:a', 'aac',                  # AAC audio codec
        '-b:a', '128k',                 # Audio bitrate
        '-ac', '2',                     # Stereo
        '-ar', '44100',                 # Audio sample rate
        '-movflags', '+faststart',      # Web-optimized (moov atom at start)
        '-t', '90',                     # Max 90 seconds for Reels
        '-f', 'mp4',                    # Force MP4 container
        output_path
    ]

    print(f"[TRANSCODE] Input:  {input_path}", flush=True)
    print(f"[TRANSCODE] Output: {output_path}", flush=True)
    print(f"[TRANSCODE] Running ffmpeg...", flush=True)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=300  # 5 min max for transcoding
        )

        if result.returncode != 0:
            error_detail = result.stderr[-500:] if result.stderr else 'unknown'
            print(f"[TRANSCODE] FAILED: {error_detail}", flush=True)
            return None, f"ffmpeg transcoding failed: {error_detail}"

        output_size = os.path.getsize(output_path)
        input_size = os.path.getsize(input_path)
        print(f"[TRANSCODE] SUCCESS: {input_size/1024/1024:.1f}MB -> {output_size/1024/1024:.1f}MB", flush=True)

        # Verify the output is valid
        probe = _probe_video(output_path)
        if probe:
            print(f"[TRANSCODE] Output codec: video={probe.get('video_codec','?')}, audio={probe.get('audio_codec','?')}", flush=True)

        return output_path, None

    except subprocess.TimeoutExpired:
        return None, "ffmpeg transcoding timed out after 5 minutes"
    except Exception as e:
        return None, f"ffmpeg transcoding error: {str(e)}"


def _detect_media_type(video_url=None, file_path=None):
    """Detect if the media is video or image."""
    if video_url:
        return 'video'
    if file_path and os.path.isfile(file_path):
        ext = os.path.splitext(file_path)[1].lower()
        if ext in VIDEO_EXTENSIONS:
            return 'video'
    return 'image'


def post_to_instagram(access_token, ig_user_id, message, image_url=None, video_url=None, file_path=None):
    """
    Post to Instagram Professional Account via Graph API v19.0.

    Image flow:
        1. Upload local file to Cloudinary if needed
        2. Create container with image_url
        3. Publish immediately

    Video flow:
        1. Upload local file to Cloudinary if needed (auto-transcodes)
        2. Create container with video_url + media_type=VIDEO
        3. Poll status until FINISHED (or ERROR/timeout)
        4. Publish

    Instagram does NOT accept file uploads directly.
    The caller must provide a publicly accessible HTTPS URL.
    """
    try:
        print(f"[IG] Validating ig_user_id={ig_user_id}...", flush=True)
        ok, reason = validate_ig_user_id(access_token, ig_user_id)
        if not ok:
            return {'success': False, 'post_id': None, 'post_url': None,
                    'error': f'Instagram setup error: {reason}'}
        print(f"[IG] IG User ID valid: {reason}", flush=True)

        media_type = _detect_media_type(video_url, file_path)

        if media_type == 'video':
            if file_path and os.path.isfile(file_path) and not video_url:
                is_cloudinary = 'res.cloudinary.com' in (video_url or '')
                if not is_cloudinary:
                    print(f"[IG VIDEO] Uploading local video to Cloudinary...", flush=True)
                    cloud_url, cloud_err = upload_to_cloudinary(file_path, resource_type='video')
                    if cloud_url:
                        video_url = cloud_url
                        print(f"[IG VIDEO] Cloudinary URL: {video_url}", flush=True)
                    elif cloud_err:
                        return {'success': False, 'post_id': None, 'post_url': None,
                                'error': f'Video upload failed: {cloud_err}. Set up Cloudinary in .env'}

            if not video_url:
                return {'success': False, 'post_id': None, 'post_url': None,
                        'error': 'Instagram video requires a publicly accessible video_url'}

            video_url = ensure_public_https_url(video_url)
            print(f"[IG VIDEO] Video URL: {video_url}", flush=True)

            # Verify video URL is accessible before sending to Instagram
            try:
                resp = requests.head(video_url, timeout=15, allow_redirects=True)
                if resp.status_code != 200:
                    resp = requests.get(video_url, timeout=15, stream=True)
                    resp.close()
                if resp.status_code != 200:
                    return {'success': False, 'post_id': None, 'post_url': None,
                            'error': f'Video URL not accessible (HTTP {resp.status_code}). Upload video to Cloudinary first.'}
                content_type = resp.headers.get('content-type', '')
                print(f"[IG VIDEO] URL check: {resp.status_code}, content-type: {content_type}", flush=True)
                if not content_type.startswith('video/'):
                    print(f"[IG VIDEO] Warning: content-type is {content_type}, Instagram may reject", flush=True)
            except Exception as url_err:
                print(f"[IG VIDEO] URL check failed: {url_err}", flush=True)
                return {'success': False, 'post_id': None, 'post_url': None,
                        'error': f'Video URL not accessible: {url_err}. Use Cloudinary for reliable uploads.'}

            print("=" * 60, flush=True)
            print("INSTAGRAM VIDEO POST:", flush=True)
            print(f"  media_type = VIDEO", flush=True)
            print(f"  video_url  = {video_url}", flush=True)
            print(f"  ig_user_id = {ig_user_id}", flush=True)
            print("=" * 60, flush=True)

            container_url = f'{GRAPH_API}/{ig_user_id}/media'
            container_data = {
                'media_type': 'REELS',
                'video_url': video_url,
                'caption': message,
                'access_token': access_token
            }

            print("[IG VIDEO] Step 1: Creating container...", flush=True)
            container_resp = requests.post(container_url, data=container_data, timeout=60)
            container_result = container_resp.json()

            print(f"[IG VIDEO] Container response ({container_resp.status_code}): {json.dumps(container_result)}", flush=True)

            if container_resp.status_code != 200 or 'id' not in container_result:
                err = container_result.get('error', {})
                error_msg = err.get('message', str(container_result))
                error_code = err.get('code', '')
                error_subcode = err.get('error_subcode', '')
                
                # Provide helpful error messages
                if str(error_code) == '100' and str(error_subcode) == '2207067':
                    full_error = (f"Instagram cannot access video URL. "
                                f"Ensure video is uploaded to Cloudinary and URL is publicly accessible. "
                                f"Error: {error_msg}")
                else:
                    full_error = f"Instagram video container failed: {error_msg} (code={error_code}, subcode={error_subcode})"
                print(f"[IG VIDEO] ERROR: {full_error}", flush=True)
                return {'success': False, 'post_id': None, 'post_url': None, 'error': full_error}

            creation_id = container_result['id']
            print(f"[IG VIDEO] Container created: creation_id={creation_id}", flush=True)

            print("[IG VIDEO] Step 2: Waiting for video processing...", flush=True)
            status_url = f'{GRAPH_API}/{creation_id}'
            status_params = {'fields': 'status_code,status', 'access_token': access_token}

            poll_delays = [2, 2, 3, 3, 4, 5, 5, 5, 5, 5]  # Start fast, settle at 5s
            for i in range(60):
                delay = poll_delays[min(i, len(poll_delays)-1)]
                _time.sleep(delay)
                try:
                    status_resp = requests.get(status_url, params=status_params, timeout=10)
                    status_data = status_resp.json()
                    sc = status_data.get('status_code', '')
                    print(f"[IG VIDEO] Poll #{i+1}: status_code={sc}", flush=True)

                    if sc == 'FINISHED':
                        print("[IG VIDEO] Processing FINISHED", flush=True)
                        break
                    elif sc == 'ERROR':
                        err_msg = status_data.get('error', {}).get('message', 'Video processing failed')
                        print(f"[IG VIDEO] Processing ERROR: {err_msg}", flush=True)
                        return {'success': False, 'post_id': None, 'post_url': None,
                                'error': f'Instagram video processing error: {err_msg}'}
                except Exception as poll_err:
                    print(f"[IG VIDEO] Poll error: {poll_err}", flush=True)
            else:
                return {'success': False, 'post_id': None, 'post_url': None,
                        'error': 'Instagram video processing timed out after 5 minutes'}

            print("[IG VIDEO] Step 3: Publishing...", flush=True)
            publish_url = f'{GRAPH_API}/{ig_user_id}/media_publish'
            publish_data = {'creation_id': creation_id, 'access_token': access_token}

            publish_resp = requests.post(publish_url, data=publish_data, timeout=30)
            publish_result = publish_resp.json()

            print(f"[IG VIDEO] Publish response ({publish_resp.status_code}): {json.dumps(publish_result)}", flush=True)

            if publish_resp.status_code == 200 and 'id' in publish_result:
                post_id = publish_result['id']
                print(f"[IG VIDEO] SUCCESS! post_id={post_id}", flush=True)
                return {
                    'success': True,
                    'post_id': post_id,
                    'post_url': f'https://www.instagram.com/p/{post_id}',
                    'error': None
                }
            else:
                err = publish_result.get('error', {})
                error_msg = err.get('message', str(publish_result))
                return {'success': False, 'post_id': None, 'post_url': None,
                        'error': f'Instagram video publish failed: {error_msg}'}

        else:
            if file_path and os.path.isfile(file_path) and not image_url:
                is_cloudinary = 'res.cloudinary.com' in (image_url or '')
                if not is_cloudinary:
                    print(f"[IG IMAGE] Uploading local image to Cloudinary...", flush=True)
                    cloud_url, cloud_err = upload_to_cloudinary(file_path, resource_type='image')
                    if cloud_url:
                        image_url = cloud_url
                        print(f"[IG IMAGE] Cloudinary URL: {image_url}", flush=True)
                    elif cloud_err:
                        return {'success': False, 'post_id': None, 'post_url': None,
                                'error': f'Image upload failed: {cloud_err}. Set up Cloudinary in .env'}

            if not image_url:
                return {'success': False, 'post_id': None, 'post_url': None,
                        'error': 'Instagram image requires image_url'}

            image_url = ensure_public_https_url(image_url)
            print(f"[IG IMAGE] Verifying image URL: {image_url}", flush=True)
            ok, reason = verify_public_url(image_url, media_type='image')
            if not ok:
                return {'success': False, 'post_id': None, 'post_url': None,
                        'error': f'Instagram image URL invalid: {reason}. Use Cloudinary for reliable uploads.'}

            print("=" * 60, flush=True)
            print("INSTAGRAM IMAGE POST:", flush=True)
            print(f"  image_url  = {image_url}", flush=True)
            print("=" * 60, flush=True)

            container_url = f'{GRAPH_API}/{ig_user_id}/media'
            container_data = {
                'media_type': 'IMAGE',
                'image_url': image_url,
                'caption': message,
                'access_token': access_token
            }

            container_resp = requests.post(container_url, data=container_data, timeout=60)
            container_result = container_resp.json()

            print(f"[IG IMAGE] Container response ({container_resp.status_code}): {json.dumps(container_result)}", flush=True)

            if container_resp.status_code != 200 or 'id' not in container_result:
                err = container_result.get('error', {})
                error_msg = err.get('message', str(container_result))
                error_code = err.get('code', '')
                return {'success': False, 'post_id': None, 'post_url': None,
                        'error': f'Instagram image container failed: {error_msg} (code={error_code})'}

            creation_id = container_result['id']

            publish_url = f'{GRAPH_API}/{ig_user_id}/media_publish'
            publish_data = {'creation_id': creation_id, 'access_token': access_token}

            publish_resp = requests.post(publish_url, data=publish_data, timeout=30)
            publish_result = publish_resp.json()

            print(f"[IG IMAGE] Publish response ({publish_resp.status_code}): {json.dumps(publish_result)}", flush=True)

            if publish_resp.status_code == 200 and 'id' in publish_result:
                post_id = publish_result['id']
                return {
                    'success': True,
                    'post_id': post_id,
                    'post_url': f'https://www.instagram.com/p/{post_id}',
                    'error': None
                }
            else:
                err = publish_result.get('error', {})
                error_msg = err.get('message', str(publish_result))
                return {'success': False, 'post_id': None, 'post_url': None,
                        'error': f'Instagram image publish failed: {error_msg}'}

    except Exception as e:
        print(f"[IG] Exception: {e}", flush=True)
        return {'success': False, 'post_id': None, 'post_url': None,
                'error': f'Instagram exception: {str(e)}'}


# ─── THREADS ──────────────────────────────────────────────────────

def post_to_threads(access_token, ig_user_id, message, image_url=None, video_url=None):
    """Post to Meta Threads via Instagram API."""
    try:
        url = f'{GRAPH_API}/{ig_user_id}/threads'
        data = {
            'message': message,
            'access_token': access_token
        }
        
        if image_url:
            data['image_url'] = image_url
        elif video_url:
            data['video_url'] = video_url
        
        resp = requests.post(url, data=data, timeout=60)
        result = resp.json()

        if resp.status_code == 200 and 'id' in result:
            post_id = result['id']
            return {
                'success': True,
                'post_id': post_id,
                'post_url': f'https://www.threads.net/post/{post_id}',
                'error': None
            }
        else:
            error_msg = result.get('error', {}).get('message', str(result))
            return {'success': False, 'post_id': None, 'post_url': None, 'error': error_msg}
    except Exception as e:
        return {'success': False, 'post_id': None, 'post_url': None, 'error': str(e)}


# ─── PINTEREST ─────────────────────────────────────────────────────

def post_to_pinterest(access_token, board_id, message, image_url=None, link=None):
    """Create a Pin on a Pinterest board via API v5."""
    try:
        url = 'https://api.pinterest.com/v5/pins'
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }

        payload = {
            'board_id': board_id,
            'title': message[:100],
            'description': message,
        }

        if image_url:
            payload['media_source'] = {
                'source_type': 'image_url',
                'url': image_url
            }
        if link:
            payload['link'] = link

        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        result = resp.json()

        if resp.status_code in (200, 201) and result.get('id'):
            pin_id = result['id']
            return {
                'success': True,
                'post_id': pin_id,
                'post_url': f'https://www.pinterest.com/pin/{pin_id}/',
                'error': None
            }
        else:
            error_msg = result.get('message', str(result))
            return {'success': False, 'post_id': None, 'post_url': None, 'error': error_msg}
    except Exception as e:
        return {'success': False, 'post_id': None, 'post_url': None, 'error': str(e)}
