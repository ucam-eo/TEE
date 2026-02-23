import secrets

from lib.config import DATA_DIR

# Secret key: persistent file so sessions survive restarts
_secret_key_file = DATA_DIR / '.django_secret_key'
if _secret_key_file.exists():
    SECRET_KEY = _secret_key_file.read_text().strip()
else:
    SECRET_KEY = secrets.token_hex(32)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _secret_key_file.write_text(SECRET_KEY)
    _secret_key_file.chmod(0o600)

ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.sessions',
    'django.contrib.contenttypes',
    'corsheaders',
    'api',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'api.middleware.DemoModeMiddleware',
]

ROOT_URLCONF = 'tee_project.urls'

WSGI_APPLICATION = 'tee_project.wsgi.application'

# No database needed - file-based auth + file-based sessions
DATABASES = {}

# File-based sessions (no DB needed)
SESSION_ENGINE = 'django.contrib.sessions.backends.file'
_session_dir = DATA_DIR / '.django_sessions'
_session_dir.mkdir(parents=True, exist_ok=True)
SESSION_FILE_PATH = str(_session_dir)
SESSION_COOKIE_NAME = 'tee_session'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'

STATIC_URL = '/static/'

# CORS - matches Flask-CORS(app, supports_credentials=True)
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

# TEE-specific settings
import os
TEE_TILE_SERVER_URL = os.environ.get('TILE_SERVER_URL')

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
}
