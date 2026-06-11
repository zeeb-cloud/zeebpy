"""
Default settings for Zeeb API.

These are the sensible defaults that can be overridden in your project's settings.py.
"""

# Debug mode - set to False in production
DEBUG = False

# Secret key for JWT tokens - MUST be overridden in production!
SECRET_KEY = "INSECURE-DEFAULT-KEY-CHANGE-IN-PRODUCTION"

# Database configuration
DATABASE = {
    "url": "sqlite+aiosqlite:///db.sqlite3",
}

# URL configuration - path to your urls module (like Django's ROOT_URLCONF)
ROOT_URLCONF = None

# Middleware classes (executed in order, top to bottom for requests, bottom to top for responses)
MIDDLEWARE = [
    "zeeb_api.middleware.CORSMiddleware",
    "zeeb_api.middleware.JWTAuthMiddleware",
]

# Installed apps - list your app modules here
INSTALLED_APPS = []

# Custom user model (like Django's AUTH_USER_MODEL)
# Set to your custom User model path, e.g., "apps.accounts.models.CustomUser"
AUTH_USER_MODEL = None  # Uses default zeeb_api.auth.models.User

# JWT Settings
JWT_SECRET_KEY = None  # Falls back to SECRET_KEY if not set
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 60
JWT_REFRESH_TOKEN_EXPIRE_DAYS = 7
JWT_ISSUER = None
JWT_AUDIENCE = None

# Auth settings
AUTH_LOAD_USER_FROM_DB = True  # Load full user from database vs just token claims

# OAuth2 / OIDC settings (requires the "oauth" extra: pip install zeebpy[oauth])
# Provider registry, e.g.
#   OAUTH_PROVIDERS = {
#       "azure": {"tenant": "common", "client_id": "...", "client_secret": "..."},
#       "google": {"client_id": "...", "client_secret": "..."},
#       "custom": {"class": "myapp.oauth.MyProvider", "client_id": "..."},
#   }
# The provider class is inferred for the well-known names azure/google/github;
# any other name requires an explicit "class" dotted path.
OAUTH_PROVIDERS = {}
# Auto-create a local user on first OAuth login (when the provider supplies an email)
OAUTH_AUTO_CREATE_USERS = True
# Link an OAuth identity to an existing local user with the same email address.
# SECURITY: only enable when all configured providers verify email ownership.
OAUTH_LINK_BY_EMAIL = True
# Lifetime of the signed OAuth state token (seconds)
OAUTH_STATE_TTL_SECONDS = 600
# Explicit redirect URI (defaults to the callback route URL derived from the request)
OAUTH_REDIRECT_URI = None
# Where to redirect after a successful browser login (tokens are appended in the
# URL fragment). None returns a JSON TokenResponse instead.
OAUTH_SUCCESS_REDIRECT = None
# Provider names whose externally-issued JWTs (e.g. Azure AD access/ID tokens)
# are accepted as Bearer tokens by JWTAuthMiddleware.
OAUTH_ACCEPT_EXTERNAL_TOKENS = []

# CORS settings
CORS_ALLOW_ORIGINS = []
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_METHODS = ["*"]
CORS_ALLOW_HEADERS = ["*"]

# API settings
API_TITLE = "Zeeb API"
API_DESCRIPTION = ""
API_VERSION = "1.0.0"
API_PREFIX = ""

# Pagination settings
DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# Throttling settings
# Dotted paths to throttle classes applied to all viewsets, e.g.
# ["zeeb_api.throttling.AnonRateThrottle"]
DEFAULT_THROTTLE_CLASSES = []
# Rates per throttle scope, e.g. {"anon": "100/min", "user": "1000/min"}.
# None disables the throttle for that scope.
DEFAULT_THROTTLE_RATES = {
    "anon": None,
    "user": None,
}
# Number of trusted proxies in front of the app. When set, throttles
# identify clients by the first hop of X-Forwarded-For.
THROTTLE_NUM_PROXIES = None

# API versioning settings
# Dotted path to a versioning scheme, e.g. "zeeb_api.versioning.HeaderVersioning"
DEFAULT_VERSIONING_CLASS = None
DEFAULT_VERSION = None
ALLOWED_VERSIONS = None
VERSION_PARAM = "version"
VERSION_HEADER = "X-API-Version"

# Logging configuration
LOGGING = {
    "level": "INFO",
    "json_logs": False,
    "log_file": None,
    "log_rotation": True,
    "log_retention_days": 30,
}

# Exception handlers - set to False to disable default handlers
INSTALL_EXCEPTION_HANDLERS = True
