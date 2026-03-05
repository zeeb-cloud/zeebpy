"""
demo_blog settings.

Configure your database, installed apps, and other settings here.
"""

import os

# Debug mode - set to False in production
DEBUG = os.getenv("DEBUG", "true").lower() == "true"

# Secret key for JWT tokens - CHANGE IN PRODUCTION!
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")

# Database configuration
DATABASE = {
    "url": os.getenv(
        "DATABASE_URL",
        "sqlite+aiosqlite:///db.sqlite3"
    ),
    # PostgreSQL example:
    # "url": "postgresql+asyncpg://user:password@localhost:5432/dbname",
    # MySQL example:
    # "url": "mysql+aiomysql://user:password@localhost:3306/dbname",
}

# Installed apps - list your app modules here
INSTALLED_APPS = [
    # "apps.myapp",
    "apps.post",
    "apps.comment",
]

# Custom user model (like Django's AUTH_USER_MODEL)
# Set to your custom user model path, e.g., "apps.accounts.CustomUser"
# If not set, uses the default zeeb_api.auth.models.User
AUTH_USER_MODEL = None  # Example: "apps.accounts.CustomUser"

# Logging configuration
# Logs are automatically written to the logs/ directory with daily rotation
LOGGING = {
    "level": os.getenv("LOG_LEVEL", "DEBUG" if DEBUG else "INFO"),
    "json_logs": os.getenv("JSON_LOGS", "false" if DEBUG else "true").lower() == "true",
    "log_file": os.getenv("LOG_FILE", "logs/app.log"),
    "log_rotation": True,           # Rotate logs at midnight
    "log_retention_days": 30,       # Keep logs for 30 days
}

# Middleware (for FastAPI)
MIDDLEWARE = [
    # "zeeb_api.middleware.CORSMiddleware",
]

# CORS settings
CORS_ALLOW_ORIGINS = [
    "http://localhost:3000",
]

# API settings
API_TITLE = "demo_blog API"
API_VERSION = "1.0.0"
API_PREFIX = "/api/v1"

# Pagination (limit/offset)
DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# Migration enforcement
# When True, server will fail to start if there are unapplied migrations
# Automatically disabled in DEBUG mode for development convenience
ENFORCE_MIGRATIONS = os.getenv("ENFORCE_MIGRATIONS", "true").lower() == "true"

# JWT settings
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 15
JWT_REFRESH_TOKEN_EXPIRE_DAYS = 7
