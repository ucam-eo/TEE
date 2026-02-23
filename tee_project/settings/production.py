from .base import *
import os

DEBUG = False
TEE_USE_CELERY = os.environ.get('TEE_USE_CELERY', '0') in ('1', 'true')
ALLOWED_HOSTS = os.environ.get('TEE_ALLOWED_HOSTS', '*').split(',')
SESSION_COOKIE_SECURE = os.environ.get('TEE_HTTPS', '') in ('1', 'true')
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
