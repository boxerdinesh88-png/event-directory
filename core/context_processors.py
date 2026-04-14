def user_profile_context(request):
    """Add user profile photo to template context"""
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
            photo_url = profile.photo.url if profile.photo else None
        except:
            photo_url = None
        return {
            'user_profile_photo': photo_url,
        }
    return {'user_profile_photo': None}
