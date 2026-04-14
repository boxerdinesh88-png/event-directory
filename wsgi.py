"""
WSGI config for Event Directory and Logistic project.
Production configuration with WhiteNoise for static files.
"""
import os
from django.core.wsgi import get_wsgi_application
from whitenoise import WhiteNoise

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')

application = get_wsgi_application()
application = WhiteNoise(application, root='staticfiles', prefix='static/')

from django.conf import settings
if not settings.DEBUG:
    application = WhiteNoise(application, root=settings.STATIC_ROOT, prefix='static/', immutable=True)

application = WhiteNoise(application)