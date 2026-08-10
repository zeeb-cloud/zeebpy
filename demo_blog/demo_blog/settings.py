"""
demo_blog settings.

Every setting below is environment-driven: the literal is the default used when
the variable is absent. Change behavior by editing ``.env`` (gitignored) or by
setting real environment variables — real environment variables always win.
``.env.example`` documents every supported variable.

Value grammar: booleans accept 1/true/yes/on and 0/false/no/off; lists are
comma-separated; an empty value means "explicitly empty", not "use the default".
"""

from pathlib import Path

from zeeb_api.conf.env import (
    env_bool,
    env_int,
    env_list,
    env_oauth_providers,
    env_str,
    load_env,
)

# The directory holding manage.py. Every path below is anchored here, so the
# settings behave identically no matter which directory the process runs from —
# the CLI, the agent tooling and the test suite all load this file by path,
# never by working directory.
BASE_DIR = Path(__file__).resolve().parent.parent

load_env(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

DEBUG = env_bool("DEBUG", True)

# The real signing key lives in .env, generated per project and never committed.
# This fallback is a known-insecure sentinel on purpose: with DEBUG off and no
# SECRET_KEY in the environment, create_app() refuses to start rather than
# silently signing tokens with a guessable key.
SECRET_KEY = env_str("SECRET_KEY", "INSECURE-DEFAULT-KEY-CHANGE-IN-PRODUCTION")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# PostgreSQL: DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/db
# MySQL:      DATABASE_URL=mysql+aiomysql://user:password@localhost:3306/db

DEFAULT_DATABASE_URL = f"sqlite+aiosqlite:///{BASE_DIR / 'db.sqlite3'}"
DATABASE = {"url": env_str("DATABASE_URL", DEFAULT_DATABASE_URL)}

# Refuse to start with unapplied migrations (a warning instead under DEBUG).
ENFORCE_MIGRATIONS = env_bool("ENFORCE_MIGRATIONS", True)

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
# 'zeeb startapp <name>' appends to this list for you.

INSTALLED_APPS = [
    "apps.accounts",
    "apps.post",
    "apps.comment",
]

# This project's own user model, in apps/accounts/models.py. Everything that
# resolves a user goes through it: the auth endpoints, createsuperuser, and
# every ForeignKey pointing at "accounts.User".
AUTH_USER_MODEL = "accounts.User"
AUTH_LOAD_USER_FROM_DB = env_bool("AUTH_LOAD_USER_FROM_DB", True)

# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------

ROOT_URLCONF = "demo_blog.urls"
API_TITLE = env_str("API_TITLE", "demo_blog API")
API_DESCRIPTION = env_str("API_DESCRIPTION", "")
API_VERSION = env_str("API_VERSION", "1.0.0")
API_PREFIX = env_str("API_PREFIX", "/api/v1")

# /health (liveness) and /ready (readiness, checks the database) are registered
# by create_app(); the standard error envelope is installed the same way.
INSTALL_HEALTH_ROUTES = True
INSTALL_EXCEPTION_HANDLERS = True

DEFAULT_LIMIT = env_int("DEFAULT_LIMIT", 20)
MAX_LIMIT = env_int("MAX_LIMIT", 100)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
# The first entry is the outermost. CORS wraps everything so even a rejected
# request answers with CORS headers instead of an opaque browser error;
# versioning resolves the requested API version before any view runs;
# JWTAuthMiddleware resolves the Bearer token and sets request.state.user, which
# every ViewSet permission class reads. CORSMiddleware is skipped automatically
# while CORS_ALLOW_ORIGINS is empty.

MIDDLEWARE = [
    "zeeb_api.middleware.CORSMiddleware",
    "zeeb_api.versioning.VersioningMiddleware",
    "zeeb_api.middleware.JWTAuthMiddleware",
]

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

CORS_ALLOW_ORIGINS = env_list("CORS_ALLOW_ORIGINS", ["http://localhost:3000", "http://localhost:5173"])
CORS_ALLOW_CREDENTIALS = env_bool("CORS_ALLOW_CREDENTIALS", True)
CORS_ALLOW_METHODS = env_list("CORS_ALLOW_METHODS", ["*"])
CORS_ALLOW_HEADERS = env_list("CORS_ALLOW_HEADERS", ["*"])
CORS_EXPOSE_HEADERS = env_list("CORS_EXPOSE_HEADERS", [])
CORS_MAX_AGE = env_int("CORS_MAX_AGE", 600)

# ---------------------------------------------------------------------------
# Authentication (JWT)
# ---------------------------------------------------------------------------
# The endpoints are mounted in urls.py: POST <prefix>/login, /register,
# /refresh, /logout and GET <prefix>/me.

JWT_SECRET_KEY = env_str("JWT_SECRET_KEY", SECRET_KEY)
JWT_ALGORITHM = env_str("JWT_ALGORITHM", "HS256")
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = env_int("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", 60)
JWT_REFRESH_TOKEN_EXPIRE_DAYS = env_int("JWT_REFRESH_TOKEN_EXPIRE_DAYS", 7)
JWT_ISSUER = env_str("JWT_ISSUER", None)
JWT_AUDIENCE = env_str("JWT_AUDIENCE", None)

AUTH_URL_PREFIX = env_str("AUTH_URL_PREFIX", "/auth")
AUTH_ENABLE_REGISTRATION = env_bool("AUTH_ENABLE_REGISTRATION", True)
# Per-client limit on /login and /register — the credential-stuffing brake.
# Set it empty to turn it off.
AUTH_LOGIN_THROTTLE_RATE = env_str("AUTH_LOGIN_THROTTLE_RATE", "10/min")

# ---------------------------------------------------------------------------
# OAuth2 / OIDC   (pip install "zeebpy[oauth]")
# ---------------------------------------------------------------------------
# A provider activates the moment its client id is in the environment:
#   GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
#   GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET
#   AZURE_CLIENT_ID  / AZURE_CLIENT_SECRET / AZURE_TENANT_ID (default "common")
# With none set this stays empty and urls.py mounts no OAuth route at all.

OAUTH_PROVIDERS = env_oauth_providers()
OAUTH_AUTO_CREATE_USERS = env_bool("OAUTH_AUTO_CREATE_USERS", True)
OAUTH_LINK_BY_EMAIL = env_bool("OAUTH_LINK_BY_EMAIL", True)
OAUTH_REQUIRE_VERIFIED_EMAIL = env_bool("OAUTH_REQUIRE_VERIFIED_EMAIL", True)
OAUTH_STATE_TTL_SECONDS = env_int("OAUTH_STATE_TTL_SECONDS", 600)
OAUTH_REDIRECT_URI = env_str("OAUTH_REDIRECT_URI", None)
OAUTH_SUCCESS_REDIRECT = env_str("OAUTH_SUCCESS_REDIRECT", None)
OAUTH_ALLOWED_REDIRECT_HOSTS = env_list("OAUTH_ALLOWED_REDIRECT_HOSTS", [])
OAUTH_ACCEPT_EXTERNAL_TOKENS = env_list("OAUTH_ACCEPT_EXTERNAL_TOKENS", [])

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
# Applies to every ViewSet that does not set its own throttle_classes. Rates are
# "<count>/<period>" with period s/sec, m/min, h/hour, d/day. Set the classes
# empty to turn throttling off entirely.
#
# The default cache is per process: with several workers the effective limit
# multiplies by the worker count. Configure a shared BaseThrottleCache via
# zeeb_api.throttling.set_throttle_cache() before deploying behind more than one.

DEFAULT_THROTTLE_CLASSES = env_list("DEFAULT_THROTTLE_CLASSES", ["zeeb_api.throttling.AnonRateThrottle", "zeeb_api.throttling.UserRateThrottle"])
DEFAULT_THROTTLE_RATES = {"anon": env_str("THROTTLE_ANON_RATE", "120/min"), "user": env_str("THROTTLE_USER_RATE", "1200/min")}
# Trusted reverse proxies in front of the app; 0 means the client IP is used
# directly rather than read out of X-Forwarded-For.
THROTTLE_NUM_PROXIES = env_int("THROTTLE_NUM_PROXIES", 0) or None

# ---------------------------------------------------------------------------
# API versioning
# ---------------------------------------------------------------------------
# Clients send "X-API-Version: 1.0". A request without the header gets
# DEFAULT_VERSION; a version outside ALLOWED_VERSIONS is rejected with a 400
# API_VERSION_INVALID envelope. Set ALLOWED_VERSIONS empty to accept anything,
# or DEFAULT_VERSIONING_CLASS empty to switch versioning off.

DEFAULT_VERSIONING_CLASS = env_str("DEFAULT_VERSIONING_CLASS", "zeeb_api.versioning.HeaderVersioning")
DEFAULT_VERSION = env_str("DEFAULT_VERSION", "1.0")
ALLOWED_VERSIONS = env_list("ALLOWED_VERSIONS", ["1.0"])
VERSION_PARAM = env_str("VERSION_PARAM", "version")
VERSION_HEADER = env_str("VERSION_HEADER", "X-API-Version")

# ---------------------------------------------------------------------------
# Logging — daily rotation into logs/
# ---------------------------------------------------------------------------
# create_app() applies this; do not call configure_logging() yourself, a second
# call resets the handlers this one installs.

LOGGING = {
    "level": env_str("LOG_LEVEL", "DEBUG" if DEBUG else "INFO"),
    "json_logs": env_bool("JSON_LOGS", not DEBUG),
    "log_file": env_str("LOG_FILE", str(BASE_DIR / "logs" / "app.log")),
    "log_rotation": env_bool("LOG_ROTATION", True),
    "log_retention_days": env_int("LOG_RETENTION_DAYS", 30),
}
