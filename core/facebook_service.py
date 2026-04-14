"""
Facebook API Service for Webinar Posting
Requires: pip install facebook-sdk
"""

import requests
import logging
import random

logger = logging.getLogger(__name__)

FACEBOOK_API_VERSION = 'v18.0'

def post_to_facebook_page(page_id, access_token, message, link=None, image_url=None):
    """
    Post to a Facebook Page.
    Returns: {'success': bool, 'post_id': str, 'post_url': str, 'error': str}
    """
    try:
        url = f'https://graph.facebook.com/{FACEBOOK_API_VERSION}/{page_id}/feed'
        
        params = {
            'access_token': access_token,
            'message': message,
        }
        
        if link:
            params['link'] = link
        
        response = requests.post(url, data=params, timeout=30)
        result = response.json()
        
        if response.status_code == 200 and 'id' in result:
            post_id = result['id']
            return {
                'success': True,
                'post_id': post_id,
                'post_url': f'https://facebook.com/{post_id}',
                'error': None
            }
        else:
            error_msg = result.get('error', {}).get('message', 'Unknown error')
            return {
                'success': False,
                'post_id': None,
                'post_url': None,
                'error': error_msg
            }
    except Exception as e:
        logger.error(f"Facebook API error: {str(e)}")
        return {
            'success': False,
            'post_id': None,
            'post_url': None,
            'error': str(e)
        }


def post_to_facebook_group(group_id, access_token, message, link=None):
    """
    Post to a Facebook Group.
    Returns: {'success': bool, 'post_id': str, 'post_url': str, 'error': str}
    """
    try:
        url = f'https://graph.facebook.com/{FACEBOOK_API_VERSION}/{group_id}/feed'
        
        params = {
            'access_token': access_token,
            'message': message,
        }
        
        if link:
            params['link'] = link
        
        response = requests.post(url, data=params, timeout=30)
        result = response.json()
        
        if response.status_code == 200 and 'id' in result:
            post_id = result['id']
            return {
                'success': True,
                'post_id': post_id,
                'post_url': f'https://facebook.com/groups/{post_id}',
                'error': None
            }
        else:
            error_msg = result.get('error', {}).get('message', 'Unknown error')
            return {
                'success': False,
                'post_id': None,
                'post_url': None,
                'error': error_msg
            }
    except Exception as e:
        logger.error(f"Facebook Group API error: {str(e)}")
        return {
            'success': False,
            'post_id': None,
            'post_url': None,
            'error': str(e)
        }


def post_photo_to_facebook_page(page_id, access_token, message, image_url):
    """
    Post a photo to Facebook Page.
    Returns: {'success': bool, 'post_id': str, 'post_url': str, 'error': str}
    """
    try:
        url = f'https://graph.facebook.com/{FACEBOOK_API_VERSION}/{page_id}/photos'
        
        params = {
            'access_token': access_token,
            'url': image_url,
            'caption': message,
        }
        
        response = requests.post(url, data=params, timeout=60)
        result = response.json()
        
        if response.status_code == 200 and 'post_id' in result:
            return {
                'success': True,
                'post_id': result['post_id'],
                'post_url': f'https://facebook.com/{result["post_id"]}',
                'error': None
            }
        else:
            error_msg = result.get('error', {}).get('message', 'Unknown error')
            return {
                'success': False,
                'post_id': None,
                'post_url': None,
                'error': error_msg
            }
    except Exception as e:
        logger.error(f"Facebook Photo API error: {str(e)}")
        return {
            'success': False,
            'post_id': None,
            'post_url': None,
            'error': str(e)
        }


def get_spin_variation(text):
    """
    Process spin text variations.
    Format: {option1|option2|option3} text
    Returns: Text with one random option selected
    """
    if '{' not in text or '}' not in text:
        return text
    
    import re
    pattern = r'\{([^}]+)\}'
    
    def replace_spin(match):
        options = match.group(1).split('|')
        return random.choice(options)
    
    return re.sub(pattern, replace_spin, text)


def validate_access_token(access_token):
    """
    Validate Facebook access token.
    Returns: {'valid': bool, 'user_id': str, 'error': str}
    """
    try:
        url = f'https://graph.facebook.com/{FACEBOOK_API_VERSION}/me'
        params = {'access_token': access_token}
        
        response = requests.get(url, params=params, timeout=10)
        result = response.json()
        
        if response.status_code == 200 and 'id' in result:
            return {
                'valid': True,
                'user_id': result.get('id'),
                'name': result.get('name'),
                'error': None
            }
        else:
            return {
                'valid': False,
                'user_id': None,
                'name': None,
                'error': result.get('error', {}).get('message', 'Invalid token')
            }
    except Exception as e:
        return {
            'valid': False,
            'user_id': None,
            'name': None,
            'error': str(e)
        }


def get_page_info(page_id, access_token):
    """
    Get Facebook Page info.
    Returns: {'success': bool, 'name': str, 'followers': int, 'error': str}
    """
    try:
        url = f'https://graph.facebook.com/{FACEBOOK_API_VERSION}/{page_id}'
        params = {
            'access_token': access_token,
            'fields': 'name,followers_count'
        }
        
        response = requests.get(url, params=params, timeout=10)
        result = response.json()
        
        if response.status_code == 200 and 'name' in result:
            return {
                'success': True,
                'name': result.get('name'),
                'followers': result.get('followers_count', 0),
                'error': None
            }
        else:
            return {
                'success': False,
                'name': None,
                'followers': 0,
                'error': result.get('error', {}).get('message', 'Unknown error')
            }
    except Exception as e:
        return {
            'success': False,
            'name': None,
            'followers': 0,
            'error': str(e)
        }
