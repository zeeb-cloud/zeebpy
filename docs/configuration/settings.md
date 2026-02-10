# Settings

Configure your Zeeb project through `settings.py`. Zeeb uses Django-style settings with sensible defaults.

## Quick Start

Create a minimal `settings.py`:

```python
# myproject/settings.py
import os

DEBUG = True
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")

DATABASE = {
    "url": "sqlite+aiosqlite:///db.sqlite3",
}

ROOT_URLCONF = "myproject.urls"

MIDDLEWARE = [
    "zeeb_api.middleware.CORSMiddleware",
    "zeeb_api.middleware.JWTAuthMiddleware",
]
```

Then in your `asgi.py`:

```python
from zeeb_api import create_app

app = create_app("myproject.settings")
```

## Application Factory

The `create_app()` function automatically:
- Loads settings from your settings module
- Configures JWT authentication
- Installs middleware from `MIDDLEWARE`
- Loads URLs from `ROOT_URLCONF`
- Sets up exception handlers

```python
from zeeb_api import create_app

# Auto-detect settings
app = create_app()

# Explicit settings module
app = create_app("myproject.settings")

# With custom lifespan
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    # Startup
    yield
    # Shutdown

app = create_app("myproject.settings", lifespan=lifespan)
```

## Core Settings

### DEBUG

```python
DEBUG = True  # Enable debug mode (disable in production!)
```

### SECRET_KEY

```python
SECRET_KEY = "your-secret-key-change-in-production"
```

**Important:** Always use a strong, random secret key in production.

### ROOT_URLCONF

Path to your URL configuration module:

```python
ROOT_URLCONF = "myproject.urls"
```

The URL module should define a `router`:

```python
# myproject/urls.py
from zeeb_api.routers import DefaultRouter
from zeeb_api.auth.urls import auth_patterns

router = DefaultRouter()

# Include auth URLs
router.include(auth_patterns, prefix="/auth")

# Include app routers
from apps.blog.urls import router as blog_router
router.include(blog_router)
```

## Middleware

Configure middleware via the `MIDDLEWARE` setting:

```python
MIDDLEWARE = [
    "zeeb_api.middleware.CORSMiddleware",
    "zeeb_api.middleware.JWTAuthMiddleware",
]
```

Middleware is executed in order for requests (top to bottom) and reverse order for responses.

### Built-in Middleware

| Middleware | Description |
|------------|-------------|
| `zeeb_api.middleware.CORSMiddleware` | Handle CORS headers (reads from CORS_* settings) |
| `zeeb_api.middleware.JWTAuthMiddleware` | Extract and validate JWT tokens |

### Custom Middleware

```python
# myproject/middleware.py
from starlette.middleware.base import BaseHTTPMiddleware

class RequestTimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        import time
        start = time.time()
        response = await call_next(request)
        response.headers["X-Process-Time"] = str(time.time() - start)
        return response
```

```python
# settings.py
MIDDLEWARE = [
    "myproject.middleware.RequestTimingMiddleware",
    "zeeb_api.middleware.JWTAuthMiddleware",
]
```

## Database

### SQLite (Development)

```python
DATABASE = {
    "url": "sqlite+aiosqlite:///./db.sqlite3",
}
```

### PostgreSQL (Production)

```python
DATABASE = {
    "url": "postgresql+asyncpg://user:password@localhost:5432/mydb",
    "pool_size": 20,
    "max_overflow": 10,
    "pool_timeout": 30,
}
```

### MySQL

```python
DATABASE = {
    "url": "mysql+aiomysql://user:password@localhost:3306/mydb",
}
```

### Database Options

| Option | Default | Description |
|--------|---------|-------------|
| `url` | Required | Database connection URL |
| `pool_size` | 5 | Connection pool size |
| `max_overflow` | 10 | Extra connections beyond pool |
| `pool_timeout` | 30 | Seconds to wait for connection |
| `pool_recycle` | 3600 | Recycle connections after (seconds) |
| `echo` | False | Log SQL queries |

## Authentication

### AUTH_USER_MODEL

```python
# Use default User model
AUTH_USER_MODEL = None

# Use custom User model
AUTH_USER_MODEL = "apps.accounts.CustomUser"
```

### AUTH_LOAD_USER_FROM_DB

```python
# Load full user from database (default)
AUTH_LOAD_USER_FROM_DB = True

# Use only token claims (faster, no DB query)
AUTH_LOAD_USER_FROM_DB = False
```

### JWT Settings

```python
JWT_SECRET_KEY = SECRET_KEY  # Or use separate key
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 60
JWT_REFRESH_TOKEN_EXPIRE_DAYS = 7
JWT_ISSUER = None  # Optional: token issuer
JWT_AUDIENCE = None  # Optional: token audience
```

## CORS

Configure CORS for your frontend:

```python
CORS_ALLOW_ORIGINS = [
    "http://localhost:3000",
    "https://myapp.com",
]
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_METHODS = ["*"]
CORS_ALLOW_HEADERS = ["*"]
```

**Note:** `CORSMiddleware` is only active if `CORS_ALLOW_ORIGINS` is non-empty.

## API Settings

```python
API_TITLE = "My API"
API_DESCRIPTION = "API documentation"
API_VERSION = "1.0.0"
API_PREFIX = "/api"  # Prefix for all routes
```

## Pagination

```python
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
```

## Logging

```python
LOGGING = {
    "level": "INFO",  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    "json_logs": False,  # True for structured logs
    "log_file": "logs/app.log",
    "log_rotation": True,
    "log_retention_days": 30,
}
```

## Exception Handlers

```python
# Enable default exception handlers (default: True)
INSTALL_EXCEPTION_HANDLERS = True
```

## Installed Apps

```python
INSTALLED_APPS = [
    "apps.blog",
    "apps.users",
]
```

## Environment Variables

Use environment variables for sensitive settings:

```python
import os

DEBUG = os.getenv("DEBUG", "false").lower() == "true"
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")

DATABASE = {
    "url": os.getenv("DATABASE_URL", "sqlite+aiosqlite:///db.sqlite3"),
}
```

### ZEEB_SETTINGS_MODULE

Override the settings module via environment variable:

```bash
export ZEEB_SETTINGS_MODULE=myproject.settings_production
```

## Complete Example

```python
# myproject/settings.py
import os

# Core
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")

# Database
DATABASE = {
    "url": os.getenv("DATABASE_URL", "sqlite+aiosqlite:///db.sqlite3"),
}

# URLs
ROOT_URLCONF = "myproject.urls"

# Middleware
MIDDLEWARE = [
    "zeeb_api.middleware.CORSMiddleware",
    "zeeb_api.middleware.JWTAuthMiddleware",
]

# Apps
INSTALLED_APPS = [
    "apps.blog",
]

# Auth
AUTH_USER_MODEL = None
AUTH_LOAD_USER_FROM_DB = True
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 60
JWT_REFRESH_TOKEN_EXPIRE_DAYS = 7

# CORS
CORS_ALLOW_ORIGINS = ["http://localhost:5173"] if DEBUG else []

# API
API_TITLE = "My API"
API_VERSION = "1.0.0"
API_PREFIX = "/api"

# Logging
LOGGING = {
    "level": "DEBUG" if DEBUG else "INFO",
    "json_logs": not DEBUG,
    "log_file": "logs/app.log",
}
```

## Default Settings Reference

All available settings with their defaults:

| Setting | Default | Description |
|---------|---------|-------------|
| `DEBUG` | `False` | Debug mode |
| `SECRET_KEY` | (unsafe default) | Secret key for JWT |
| `DATABASE` | SQLite | Database configuration |
| `ROOT_URLCONF` | `None` | URL configuration module |
| `MIDDLEWARE` | JWT + CORS | Middleware classes |
| `INSTALLED_APPS` | `[]` | Installed applications |
| `AUTH_USER_MODEL` | `None` | Custom user model |
| `AUTH_LOAD_USER_FROM_DB` | `True` | Load user from database |
| `JWT_SECRET_KEY` | `SECRET_KEY` | JWT signing key |
| `JWT_ALGORITHM` | `"HS256"` | JWT algorithm |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access token lifetime |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime |
| `CORS_ALLOW_ORIGINS` | `[]` | Allowed CORS origins |
| `CORS_ALLOW_CREDENTIALS` | `True` | Allow credentials |
| `CORS_ALLOW_METHODS` | `["*"]` | Allowed methods |
| `CORS_ALLOW_HEADERS` | `["*"]` | Allowed headers |
| `API_TITLE` | `"Zeeb API"` | API title |
| `API_DESCRIPTION` | `""` | API description |
| `API_VERSION` | `"1.0.0"` | API version |
| `API_PREFIX` | `""` | URL prefix |
| `DEFAULT_LIMIT` | `20` | Default pagination limit |
| `MAX_LIMIT` | `100` | Maximum pagination limit |
| `INSTALL_EXCEPTION_HANDLERS` | `True` | Install default handlers |

## Next Steps

- [Logging](logging.md) - Logging configuration
- [Database](database.md) - Database setup
- [ViewSets](../api/viewsets.md) - Creating API endpoints
