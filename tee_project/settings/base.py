import os
import secrets

from lib.config import DATA_DIR, APP_DIR

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
    'django.contrib.auth',
    'django.contrib.admin',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',
    'api',
]

MIDDLEWARE = [
    'api.middleware.TileShortcircuitMiddleware',   # first — skip everything else for tiles
    'django.middleware.gzip.GZipMiddleware',       # compress API/vector responses (tiles already short-circuited)
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'api.middleware.DemoModeMiddleware',
]

ROOT_URLCONF = 'tee_project.urls'

WSGI_APPLICATION = 'tee_project.wsgi.application'

# SQLite database for Django auth
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': str(DATA_DIR / 'db.sqlite3'),
    }
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# File-based sessions (no DB needed for sessions)
SESSION_ENGINE = 'django.contrib.sessions.backends.file'
_session_dir = DATA_DIR / '.django_sessions'
_session_dir.mkdir(parents=True, exist_ok=True)
SESSION_FILE_PATH = str(_session_dir)
SESSION_COOKIE_NAME = 'tee_session'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'

# Templates (required by Django admin)
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

STATIC_URL = '/static/'
STATIC_ROOT = str(APP_DIR / 'public' / 'static')

# Password hashers: PBKDF2 (default) + BCrypt (for migrated passwd hashes)
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.BCryptPasswordHasher',
]

# CORS — locked down by default; desktop.py opens it up for local dev
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = False

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

# ── Tessera VQ bolt-on ──────────────────────────────────────────────────────
# The standard embeddings client for every viewport (see api/embeddings_provider.py
# and process_viewport.py::_get_provider). The bolt-on (systemd unit
# `tessera-vq.service`) runs on michael/tee.cl.cam.ac.uk, bound to 127.0.0.1:8010,
# and is fronted by nginx on :8000 for public, per-IP rate-limited access (see
# deploy/README.md). Default here is that public gateway -- correct for local dev
# and any self-hosted deployment not co-located with the bolt-on. The co-located
# production TEE container overrides this to the unthrottled direct backdoor
# (http://127.0.0.1:8010) via scripts/manage.sh -- no SSH tunnel is needed in
# either case. All values overridable via env vars for production. Defaults are
# the study-recommended RVQ config: t=512, k1=20, k2=256, L2 (~1.77 B/px = 72x
# int8, downstream-lossless; see the tessera-vq tech note). Larger k1 or t=1024
# cost downstream accuracy for little gain.
TESSERA_VQ_URL = os.environ.get("TESSERA_VQ_URL", "http://tee.cl.cam.ac.uk:8000")
TESSERA_VQ_DEFAULTS = {
    "t":  int(os.environ.get("TESSERA_VQ_DEFAULT_T",  "512")),
    "k":  int(os.environ.get("TESSERA_VQ_DEFAULT_K",  "20")),
    "k2": int(os.environ.get("TESSERA_VQ_DEFAULT_K2", "256")),
    "m":  os.environ.get("TESSERA_VQ_DEFAULT_M", "euclidean"),
}
TESSERA_VQ_TIMEOUT_SECONDS = float(os.environ.get("TESSERA_VQ_TIMEOUT_SECONDS", "300"))
