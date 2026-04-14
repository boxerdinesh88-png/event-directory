import logging
import os

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone

from core.models import SocialPost, SocialPlatformConfig
from core.social_service import (
    post_to_facebook, post_to_facebook_photo,
    post_to_twitter, post_to_linkedin, post_to_instagram,
    post_to_threads, post_to_pinterest, upload_to_cloudinary
)


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send scheduled SocialPost records whose scheduled_at time has passed."

    def handle(self, *args, **options):
        now = timezone.now()

        posts = SocialPost.objects.filter(
            scheduled_at__isnull=False,
            scheduled_at__lte=now,
            status='scheduled',
        )

        if not posts.exists():
            self.stdout.write(self.style.NOTICE("No scheduled social posts ready to send."))
            return

        for post in posts:
            self.stdout.write(f"Sending social post #{post.id} to {post.platform}...")

            post.status = 'sending'
            post.save(update_fields=['status'])

            try:
                result = self._send_post(post)
            except Exception as e:
                result = {'success': False, 'error': str(e)}
                logger.exception(f"Post #{post.id} encountered an unexpected error.")

            if result['success']:
                post.status = 'posted'
                post.posted_at = timezone.now()
                post.post_url = result.get('post_url')
                post.scheduled_at = None
                post.save(update_fields=['status', 'posted_at', 'post_url', 'scheduled_at'])
                self.stdout.write(self.style.SUCCESS(f"Post #{post.id} sent successfully"))
            else:
                post.status = 'failed'
                post.error_message = result.get('error', 'Unknown error')
                post.save(update_fields=['status', 'error_message'])
                self.stdout.write(self.style.ERROR(f"Post #{post.id} failed: {post.error_message}"))

    def _send_post(self, post):
        config = SocialPlatformConfig.objects.filter(platform=post.platform, is_connected=True).first()
        if not config or not config.access_token:
            return {'success': False, 'error': f'{post.platform.title()} not connected'}

        # Build local file path from media_url if it's a local upload
        local_file_path = None
        if post.media_url and '/media/' in post.media_url:
            try:
                rel_path = post.media_url.split('/media/')[-1].split('?')[0]
                local_file_path = os.path.join(settings.MEDIA_ROOT, rel_path)
                if not os.path.isfile(local_file_path):
                    local_file_path = None
            except Exception:
                local_file_path = None

        # Use explicit link_url if provided; otherwise no link to avoid unintended previews.
        link = post.link_url or None

        if post.platform == 'facebook':
            if not config.extra_field:
                return {'success': False, 'error': 'Facebook Page ID not configured'}
            if post.media_type == 'image' and (post.media_url or local_file_path):
                return post_to_facebook_photo(
                    config.extra_field, config.access_token, post.caption, post.media_url, file_path=local_file_path)
            if post.media_type == 'video' and (post.media_url or local_file_path):
                return post_to_facebook_video(
                    config.extra_field, config.access_token, post.caption, post.media_url, file_path=local_file_path)
            return post_to_facebook(config.extra_field, config.access_token, post.caption, link)

        elif post.platform == 'twitter':
            if not config.extra_field:
                return {'success': False, 'error': 'Twitter Access Token Secret not configured'}
            return post_to_twitter(
                config.app_id, config.app_secret, config.access_token, config.extra_field,
                post.caption, file_path=local_file_path)

        elif post.platform == 'linkedin':
            if not config.extra_field:
                return {'success': False, 'error': 'LinkedIn Organization URN not configured'}
            return post_to_linkedin(config.access_token, config.extra_field, post.caption, link)

        elif post.platform == 'instagram':
            if not config.extra_field:
                return {'success': False, 'error': 'Instagram Business Account ID not configured'}
            
            ig_url = post.media_url
            local_file = local_file_path
            
            # Upload to Cloudinary first for reliable permanent URLs
            if local_file_path:
                cloud_url, cloud_err = upload_to_cloudinary(local_file_path)
                if cloud_url:
                    ig_url = cloud_url
                    local_file = None  # Clear local path after Cloudinary upload
                    self.stdout.write(f"  Cloudinary OK: {ig_url}")
                else:
                    self.stdout.write(self.style.WARNING(f"  Cloudinary failed: {cloud_err}, using fallback URL"))
            
            if post.media_type == 'image' and ig_url:
                return post_to_instagram(
                    config.access_token, config.extra_field, post.caption,
                    image_url=ig_url, file_path=local_file)
            elif post.media_type == 'video' and ig_url:
                return post_to_instagram(
                    config.access_token, config.extra_field, post.caption,
                    video_url=ig_url, file_path=local_file)
            return {'success': False, 'error': 'Instagram requires an image or video URL'}

        elif post.platform == 'threads':
            return post_to_threads(config.access_token, config.app_id, post.caption)

        elif post.platform == 'pinterest':
            if not config.extra_field:
                return {'success': False, 'error': 'Pinterest Board ID not configured'}
            return post_to_pinterest(
                config.access_token, config.extra_field, post.caption,
                image_url=post.media_url if post.media_type == 'image' else None,
                link=post.link_url or None)

        return {'success': False, 'error': f'Platform {post.platform} not supported'}
