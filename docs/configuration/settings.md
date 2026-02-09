# Settings

Configure your Zeeb project through `settings.py`.

## Basic Configuration

```python
# myproject/settings.py

# Project name
PROJECT_NAME = "myproject"

# Debug mode (disable in production)
DEBUG = True

# Database configuration
DATABASE = {
    "url": "sqlite+aiosqlite:///./db.sqlite3",
}

# Installed applications
INSTALLED_APPS = [
    "apps.blog",
    "apps.users",
]

# Authentication
AUTH_USER_MODEL = None  # Use default User, or "apps.accounts.CustomUser"

# Logging
LOGGING = {
    "level": "INFO",
    "json_logs": False,
    "log_file": "logs/app.log",
    "log_rotation": True,
    "log_retention_days": 30,
}
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

### Multiple Databases

```python
DATABASES = {
    "default": {
        "url": "postgresql+asyncpg://localhost/mydb",
    },
    "replica": {
        "url": "postgresql+asyncpg://localhost/mydb_replica",
    },
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

## Installed Apps

Register your applications:

```python
INSTALLED_APPS = [
    # Your apps
    "apps.blog",
    "apps.users",
    "apps.orders",
    
    # Third-party apps
    "some_package.app",
]
```

The `zeeb_auth` app is automatically included for User/Permission models.

## Authentication

### Default User Model

```python
AUTH_USER_MODEL = None  # Uses zeeb_api.auth.models.User
```

### Custom User Model

```python
# Use custom user model
AUTH_USER_MODEL = "apps.accounts.CustomUser"
```

### Password Hashing

```python
PASSWORD_HASHERS = [
    "zeeb_api.auth.hashers.PBKDF2PasswordHasher",  # Default
    "zeeb_api.auth.hashers.Argon2PasswordHasher",
    "zeeb_api.auth.hashers.BCryptPasswordHasher",
]
```

### Password Validation

```python
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "zeeb_api.auth.validators.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {
        "NAME": "zeeb_api.auth.validators.CommonPasswordValidator",
    },
]
```

## JWT Authentication

```python
# JWT settings
JWT_SECRET_KEY = "your-secret-key-change-in-production"
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24
JWT_REFRESH_EXPIRATION_DAYS = 7
```

## Logging

### Basic Logging

```python
LOGGING = {
    "level": "INFO",  # DEBUG, INFO, WARNING, ERROR, CRITICAL
}
```

### File Logging

```python
LOGGING = {
    "level": "INFO",
    "log_file": "logs/app.log",
    "log_rotation": True,
    "log_retention_days": 30,
}
```

### JSON Logging (Production)

```python
LOGGING = {
    "level": "INFO",
    "json_logs": True,
    "log_file": "logs/app.log",
}
```

### Logging Options

| Option | Default | Description |
|--------|---------|-------------|
| `level` | `"INFO"` | Minimum log level |
| `json_logs` | `False` | Output JSON format |
| `log_file` | `None` | File path for logs |
| `log_rotation` | `True` | Rotate logs daily |
| `log_retention_days` | `30` | Days to keep logs |

## API Settings

### CORS

```python
CORS = {
    "allow_origins": ["http://localhost:3000"],
    "allow_methods": ["*"],
    "allow_headers": ["*"],
    "allow_credentials": True,
}
```

### Pagination

```python
PAGINATION = {
    "default_page_size": 20,
    "max_page_size": 100,
    "page_query_param": "page",
    "page_size_query_param": "page_size",
}
```

### Throttling

```python
THROTTLE = {
    "default_rate": "100/hour",
    "burst_rate": "10/minute",
}
```

## Security Settings

### Production Checklist

```python
# Disable debug
DEBUG = False

# Set allowed hosts
ALLOWED_HOSTS = ["example.com", "www.example.com"]

# Use strong secret key
SECRET_KEY = "your-very-long-and-random-secret-key"

# HTTPS settings
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Cookie security
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
```

## Middleware

```python
MIDDLEWARE = [
    "zeeb_api.middleware.CORSMiddleware",
    "zeeb_api.middleware.AuthenticationMiddleware",
    "zeeb_api.middleware.RequestLoggingMiddleware",
]
```

## Static Files

```python
STATIC_URL = "/static/"
STATIC_ROOT = "staticfiles/"

MEDIA_URL = "/media/"
MEDIA_ROOT = "media/"
```

## Time Zone

```python
TIME_ZONE = "UTC"
USE_TZ = True
```

## Environment Variables

Use environment variables for sensitive settings:

```python
import os

DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

DATABASE = {
    "url": os.environ.get(
        "DATABASE_URL",
        "sqlite+aiosqlite:///./db.sqlite3"
    ),
}

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
```

### Using python-dotenv

```python
# Load .env file
from dotenv import load_dotenv
load_dotenv()

# Now os.environ has .env values
DATABASE = {
    "url": os.environ["DATABASE_URL"],
}
```

`.env` file:
```
DEBUG=true
DATABASE_URL=postgresql+asyncpg://localhost/mydb
SECRET_KEY=your-secret-key
```

## Development vs Production

### Development Settings

```python
# settings.py
DEBUG = True

DATABASE = {
    "url": "sqlite+aiosqlite:///./db.sqlite3",
    "echo": True,  # Log SQL
}

LOGGING = {
    "level": "DEBUG",
    "json_logs": False,
}
```

### Production Settings

```python
# settings_production.py
from myproject.settings import *

DEBUG = False

DATABASE = {
    "url": os.environ["DATABASE_URL"],
    "pool_size": 20,
}

LOGGING = {
    "level": "INFO",
    "json_logs": True,
    "log_file": "/var/log/myproject/app.log",
}

ALLOWED_HOSTS = ["example.com"]
```

Use with:
```bash
ZEEB_SETTINGS_MODULE=myproject.settings_production python manage.py runserver
```

## Complete Example

```python
# myproject/settings.py
import os

# Project
PROJECT_NAME = "myproject"
DEBUG = os.environ.get("DEBUG", "true").lower() == "true"
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

# Database
DATABASE = {
    "url": os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./db.sqlite3"),
    "pool_size": 5 if DEBUG else 20,
    "echo": DEBUG,
}

# Apps
INSTALLED_APPS = [
    "apps.blog",
    "apps.users",
]

# Auth
AUTH_USER_MODEL = "apps.users.CustomUser"
JWT_SECRET_KEY = SECRET_KEY
JWT_EXPIRATION_HOURS = 24

# Logging
LOGGING = {
    "level": "DEBUG" if DEBUG else "INFO",
    "json_logs": not DEBUG,
    "log_file": "logs/app.log",
    "log_rotation": True,
    "log_retention_days": 30,
}

# CORS (development)
if DEBUG:
    CORS = {
        "allow_origins": ["*"],
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
else:
    CORS = {
        "allow_origins": ["https://example.com"],
        "allow_methods": ["GET", "POST", "PUT", "DELETE"],
        "allow_headers": ["Authorization", "Content-Type"],
    }

# Security (production)
if not DEBUG:
    ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
```

## Next Steps

- [Logging](logging.md) - Logging configuration
- [Database](database.md) - Database setup
- [Installation](../installation.md) - Getting started
