import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
# Load local config without evaluating shell code; exported variables take priority.
if (BASE_DIR / ".env").exists():
    for line in (BASE_DIR / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def flag(name, default=False):
    return os.getenv(name, str(default)).lower() in ("true", "1", "yes")


DEBUG = flag("DEBUG", True)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
DATA_KEY = os.getenv("DATA_ENCRYPTION_KEY", "")
if not SECRET_KEY or not DATA_KEY:
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY and DATA_ENCRYPTION_KEY in .env")
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
CSRF_TRUSTED_ORIGINS = list(filter(None, os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")))
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.SessionGuardMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.PrivateResponseMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context.settings_context",
            ]
        },
    }
]
db = os.getenv("DATABASE_URL", "")
if db:
    parsed = urlparse(db)
    if parsed.scheme not in ("postgres", "postgresql"):
        raise ImproperlyConfigured("DATABASE_URL must use PostgreSQL")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname,
            "PORT": parsed.port or 5432,
            "CONN_MAX_AGE": 60,
        }
    }
else:
    (BASE_DIR / "data").mkdir(exist_ok=True, mode=0o700)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "data/hope.sqlite3",
            "OPTIONS": {"timeout": 30},
        }
    }
AUTH_USER_MODEL = "core.User"
AUTHENTICATION_BACKENDS = ["core.auth.NormalizedBackend"]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True
SITE_URL = os.getenv("SITE_URL", "http://127.0.0.1:8000").rstrip("/")
site = urlparse(SITE_URL)
site_path = site.path.rstrip("/")
if (
    site.scheme not in ("http", "https")
    or not site.hostname
    or site.username
    or site.password
    or site.query
    or site.fragment
    or (site_path and not re.fullmatch(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", site_path))
):
    raise ImproperlyConfigured("SITE_URL must be an absolute HTTP(S) URL with a simple optional path")
FORCE_SCRIPT_NAME = site_path or None
STATIC_URL = site_path + "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
SESSION_COOKIE_NAME = "hope_session"
CSRF_COOKIE_NAME = "hope_csrf"
SESSION_COOKIE_PATH = CSRF_COOKIE_PATH = site_path + "/"
SESSION_COOKIE_AGE = 30 * 86400
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = flag("SECURE_SSL_REDIRECT", not DEBUG)
# Enable only behind a trusted loopback proxy which overwrites this header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if flag("TRUST_PROXY_HTTPS") else None
SECURE_HSTS_SECONDS = 0 if DEBUG else 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
BOT_USERNAME = os.getenv("BOT_USERNAME", "hopevpn").lstrip("@")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PANEL_MODE = os.getenv("PANEL_MODE", "demo")
PANEL_URL = os.getenv("PANEL_URL", "").rstrip("/") + "/"
PANEL_TOKEN = os.getenv("PANEL_API_TOKEN", "")
PANEL_SUB_URL = os.getenv("PANEL_SUB_URL", "").rstrip("/") + "/"
PANEL_WRITES_ENABLED = flag("PANEL_WRITES_ENABLED", False)
PANEL_INBOUND_IDS = [
    int(x) for x in os.getenv("PANEL_INBOUND_IDS", "3,4,5,6,7,8,10,11,12,13").split(",") if x
]
PANEL_EXCLUDED_CLIENTS = set(filter(None, os.getenv("PANEL_EXCLUDED_CLIENTS", "").split(",")))
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "Обратитесь через форму поддержки в кабинете.")
RENEWAL_INSTRUCTIONS = os.getenv(
    "RENEWAL_INSTRUCTIONS", "Согласуйте стоимость и перевод с администратором. Продление выполняется вручную."
)
TARIFFS_APPROVED = flag("TARIFFS_APPROVED", False)
TARIFF_PRICES = {
    code: int(os.getenv("TARIFF_" + code.upper() + "_PRICE", str(default)))
    for code, default in [("week", 99), ("month", 299), ("quarter", 799), ("halfyear", 1499), ("year", 2799)]
}
if any(price <= 0 for price in TARIFF_PRICES.values()):
    raise ImproperlyConfigured("Tariff prices must be positive integer ruble amounts")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")
ADMIN_GROUP_CHAT_ID = os.getenv("ADMIN_GROUP_CHAT_ID", "")
try:
    ADMIN_TELEGRAM_USER_IDS = {
        int(value.strip()) for value in os.getenv("ADMIN_TELEGRAM_USER_IDS", "").split(",") if value.strip()
    }
except ValueError:
    raise ImproperlyConfigured("ADMIN_TELEGRAM_USER_IDS must contain numeric user IDs") from None
if any(value <= 0 for value in ADMIN_TELEGRAM_USER_IDS):
    raise ImproperlyConfigured("ADMIN_TELEGRAM_USER_IDS must contain positive user IDs")
ADMIN_TELEGRAM_LOGIN = os.getenv("ADMIN_TELEGRAM_LOGIN", "admin").lower().strip()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"private_access": {"()": "core.logging.PrivateAccessFilter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "filters": ["private_access"]}},
    "loggers": {
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "httpx": {"level": "WARNING"},
        "aiogram": {"level": "WARNING"},
    },
}
if not DEBUG and (not db or PANEL_MODE == "demo" or not SITE_URL.startswith("https://")):
    raise ImproperlyConfigured("Production needs PostgreSQL, real panel mode and HTTPS SITE_URL")
