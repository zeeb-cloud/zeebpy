# Settings

Configure your Zeeb project through `settings.py`. Zeeb reads module-level settings constants with sensible defaults.

## Quick Start

`zeeb startproject` writes a complete, environment-driven `settings.py` for you —
authentication, CORS, rate limiting, API versioning and logging are configured
and on. You normally change behavior by editing `.env`, not `settings.py`.

Every setting follows the same shape: the literal is the default, the environment
variable overrides it.

```python
# myproject/settings.py
from pathlib import Path

from zeeb_api.conf.env import env_bool, env_int, env_list, env_str, load_env

BASE_DIR = Path(__file__).resolve().parent.parent
load_env(BASE_DIR / ".env")

DEBUG = env_bool("DEBUG", True)
SECRET_KEY = env_str("SECRET_KEY", "INSECURE-DEFAULT-KEY-CHANGE-IN-PRODUCTION")

DATABASE = {"url": env_str("DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR / 'db.sqlite3'}")}

ROOT_URLCONF = "myproject.urls"

MIDDLEWARE = [
    "zeeb_api.middleware.CORSMiddleware",
    "zeeb_api.versioning.VersioningMiddleware",
    "zeeb_api.middleware.JWTAuthMiddleware",
]
```

Then in your `asgi.py`:

```python
from zeeb_api import create_app

app = create_app("myproject.settings")
```

## Environment configuration

`load_env()` reads a `.env` file into a private layer that the typed getters
consult. Precedence is always:

**real environment variable > `.env` file > the default in `settings.py`**

so a container or CI runner overrides anything a checked-out `.env` says.
`load_env` does not write to `os.environ`, which keeps one project's
configuration from leaking into another loaded in the same process.

`startproject` generates two files:

- **`.env`** — gitignored, holds `DEBUG` and a signing key generated for this
  project. That generated key is why a fresh project runs with `DEBUG=false`
  out of the box.
- **`.env.example`** — committed, documents every supported variable.

### Typed getters

```python
from zeeb_api.conf.env import env_bool, env_int, env_list, env_str, load_env

load_env(BASE_DIR / ".env")   # returns the path read, or None

env_str("JWT_ISSUER", None)                       # "" when set but empty
env_bool("DEBUG", True)                           # 1/true/yes/on vs 0/false/no/off
env_int("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", 60)
env_list("CORS_ALLOW_ORIGINS", ["http://localhost:3000"])   # comma-separated
```

An **empty value is a value**: `CORS_ALLOW_ORIGINS=` yields `[]`, not the
default. That is how a variable is switched off from the environment. An
unparsable boolean or integer warns and falls back to the default rather than
raising — a `settings.py` that raises at import time makes the CLI and the
agent tooling fall back to defaults silently.

Pass the path explicitly, as the generated template does. `settings.py` is
executed with an arbitrary working directory by the migration tooling,
`zeeb check`, `createsuperuser` and the agent layer, so a relative lookup would
find the wrong project.

### Environment variables

Every variable read by the generated `settings.py`:

| Variable | Default | Setting |
|----------|---------|---------|
| `DEBUG` | `true` | `DEBUG` |
| `SECRET_KEY` | generated into `.env` | `SECRET_KEY` |
| `DATABASE_URL` | `sqlite+aiosqlite:///db.sqlite3` | `DATABASE["url"]` |
| `ENFORCE_MIGRATIONS` | `true` | `ENFORCE_MIGRATIONS` |
| `API_TITLE` / `API_DESCRIPTION` / `API_VERSION` / `API_PREFIX` | `<project> API` / `` / `1.0.0` / `/api/v1` | the matching `API_*` |
| `DEFAULT_LIMIT` / `MAX_LIMIT` | `20` / `100` | pagination bounds |
| `CORS_ALLOW_ORIGINS` | `http://localhost:3000,http://localhost:5173` | `CORS_ALLOW_ORIGINS` |
| `CORS_ALLOW_ORIGIN_REGEX` | `None` | `CORS_ALLOW_ORIGIN_REGEX` |
| `CORS_ALLOW_CREDENTIALS` / `CORS_ALLOW_METHODS` / `CORS_ALLOW_HEADERS` / `CORS_EXPOSE_HEADERS` / `CORS_MAX_AGE` | `true` / `*` / `*` / `` / `600` | the matching `CORS_*` |
| `JWT_SECRET_KEY` | `SECRET_KEY` | `JWT_SECRET_KEY` |
| `JWT_ALGORITHM` | `HS256` | `JWT_ALGORITHM` |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `7` | `JWT_REFRESH_TOKEN_EXPIRE_DAYS` |
| `JWT_ISSUER` / `JWT_AUDIENCE` | unset | `JWT_ISSUER` / `JWT_AUDIENCE` |
| `AUTH_URL_PREFIX` | `/auth` | where the auth router mounts |
| `AUTH_ENABLE_REGISTRATION` | `true` | whether `/register` exists |
| `AUTH_LOGIN_THROTTLE_RATE` | `10/min` | per-client limit on `/login` and `/register` |
| `AUTH_LOAD_USER_FROM_DB` | `true` | `AUTH_LOAD_USER_FROM_DB` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | unset | adds `google` to `OAUTH_PROVIDERS` |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | unset | adds `github` to `OAUTH_PROVIDERS` |
| `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` / `AZURE_TENANT_ID` | unset / `common` | adds `azure` to `OAUTH_PROVIDERS` |
| `OAUTH_AUTO_CREATE_USERS` / `OAUTH_LINK_BY_EMAIL` / `OAUTH_REQUIRE_VERIFIED_EMAIL` | `true` | the matching `OAUTH_*` |
| `OAUTH_STATE_TTL_SECONDS` | `600` | `OAUTH_STATE_TTL_SECONDS` |
| `OAUTH_REDIRECT_URI` / `OAUTH_SUCCESS_REDIRECT` | unset | the matching `OAUTH_*` |
| `OAUTH_ALLOWED_REDIRECT_HOSTS` / `OAUTH_ACCEPT_EXTERNAL_TOKENS` | empty | the matching `OAUTH_*` |
| `THROTTLE_ANON_RATE` / `THROTTLE_USER_RATE` | `120/min` / `1200/min` | `DEFAULT_THROTTLE_RATES` |
| `DEFAULT_THROTTLE_CLASSES` | anon + user | `DEFAULT_THROTTLE_CLASSES` |
| `THROTTLE_NUM_PROXIES` | `0` | `THROTTLE_NUM_PROXIES` |
| `DEFAULT_VERSIONING_CLASS` | `zeeb_api.versioning.HeaderVersioning` | `DEFAULT_VERSIONING_CLASS` |
| `DEFAULT_VERSION` / `ALLOWED_VERSIONS` | `1.0` / `1.0` | `DEFAULT_VERSION` / `ALLOWED_VERSIONS` |
| `VERSION_PARAM` / `VERSION_HEADER` | `version` / `X-API-Version` | the matching setting |
| `LOG_LEVEL` / `JSON_LOGS` / `LOG_FILE` / `LOG_ROTATION` / `LOG_RETENTION_DAYS` | see below | `LOGGING` |

Turning a battery off is a single variable:

```bash
DEFAULT_THROTTLE_CLASSES=      # no rate limiting
ALLOWED_VERSIONS=              # accept any API version
DEFAULT_VERSIONING_CLASS=      # no versioning at all
AUTH_LOGIN_THROTTLE_RATE=      # no login throttle
CORS_ALLOW_ORIGINS=            # no CORS
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

### Building the app by hand

`create_app()` composes five public steps. Call them directly when you need a
FastAPI app the factory would not produce — mounting Zeeb into an existing
application, say:

```python
from fastapi import FastAPI
from zeeb_api import (
    configure_settings,
    install_error_response_schema,
    install_middleware,
    load_urlconf,
)

configure_settings("myproject.settings")   # or configure_settings(DEBUG=False, ...)

app = FastAPI()
install_middleware(app)                    # from settings.MIDDLEWARE
for router in load_urlconf("myproject.urls"):
    app.include_router(router)
install_error_response_schema(app)         # documents the error envelope in OpenAPI
```

`get_asgi_application()` is the entry point a generated `asgi.py` uses; it
returns the configured `FastAPI` instance for the given settings module.

### Reading the loaded environment

Beyond the typed getters, `zeeb_api.conf.env` exposes:

```python
from zeeb_api.conf.env import clear_env_cache, loaded_env_path, parse_env

parse_env(path)      # parse a .env file to a dict without touching os.environ
loaded_env_path()    # which .env load_env() actually read, or None
clear_env_cache()    # forget it — mainly for tests switching between projects
```

`load_env()` also accepts `override=True` (write into `os.environ`, which it
otherwise never does) and `encoding=`. Called with a directory — or with
nothing — it walks up from the current directory looking for a `.env`, stopping
at the nearest `manage.py` so it cannot escape into a parent project.

Only `clear_env_cache` is re-exported from `zeeb_api.conf`; import
`parse_env` and `loaded_env_path` from `zeeb_api.conf.env`.

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

Known insecure defaults (such as the startproject template value above) are
actively refused outside of debug mode:

- With `DEBUG = False`, `create_app()` raises
  `zeeb_api.exceptions.ImproperlyConfigured`, and JWT token
  creation/verification raises `zeeb_api.exceptions.InsecureSecretError`.
- With `DEBUG = True`, the insecure default is tolerated for local
  development, but a warning is logged once per process.

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

## Settings Layering

zeebpy has three settings layers with a defined hand-off:

1. **`zeeb_api.conf.settings` (LazySettings)** — the canonical in-process reader
   of your project's `settings.py`. `create_app()` consumes it.
2. **`zeeb_orm.conf.settings`** — the low-level library sink
   (`zeeb_orm.configure()` / `get_settings()`). `create_app()`'s default
   lifespan calls `zeeb_api.conf.orm.apply_orm_settings()` to feed it from
   layer 1 (including the full `DATABASE` dict: `echo`, `pool_size`, …).
   Library-only users (no project) configure it directly or via
   `DATABASE_*` environment variables.
3. **`zeeb_agents`** — reads a *target* project's settings off disk by path
   (`load_project_settings`); intentionally independent of the current process.

If you build your own lifespan, call `apply_orm_settings()` yourself:

```python
from zeeb_api.conf import apply_orm_settings

@asynccontextmanager
async def lifespan(app):
    apply_orm_settings()
    db = await setup_database(...)
    yield
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

`JWT_SECRET_KEY` falls back to `SECRET_KEY` when unset. Insecure default
values are refused when `DEBUG = False` (token operations raise
`InsecureSecretError` and `create_app()` raises `ImproperlyConfigured`);
with `DEBUG = True` they only trigger a one-time warning.

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

### Preview deployments

A frontend built by Lovable, Vercel or Netlify gets a new hostname on every
deploy, so an exact origin list would need editing each time. Match the host
pattern instead:

```python
CORS_ALLOW_ORIGIN_REGEX = r"https://.*\.(lovable\.app|lovableproject\.com)$"
```

Anchor it with `$` and escape the dots. An unanchored `.vercel.app` also matches
`https://evil-vercel.app.attacker.com`. A pattern that matches everything
(`.*`) together with `CORS_ALLOW_CREDENTIALS = True` is refused outside `DEBUG`,
exactly like a `"*"` origin — it is the same hole.

**Note:** `CORSMiddleware` is active as soon as either `CORS_ALLOW_ORIGINS` or
`CORS_ALLOW_ORIGIN_REGEX` is set; with both empty it is skipped.

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

## Rate Limiting

On by default in a generated project. The classes apply to every ViewSet that
does not set its own `throttle_classes`.

```python
DEFAULT_THROTTLE_CLASSES = [
    "zeeb_api.throttling.AnonRateThrottle",
    "zeeb_api.throttling.UserRateThrottle",
]
DEFAULT_THROTTLE_RATES = {"anon": "120/min", "user": "1200/min"}
THROTTLE_NUM_PROXIES = None   # trusted reverse proxies in front of the app
```

Rates are `"<count>/<period>"` with period `s`/`sec`, `m`/`min`, `h`/`hour`,
`d`/`day`. Set `DEFAULT_THROTTLE_CLASSES` empty to turn throttling off.

`/login` and `/register` carry their own per-client limit, set through
`AUTH_LOGIN_THROTTLE_RATE` (default `10/min`), because they are the two routes
an attacker can drive without a token.

The default cache is per process: behind several workers the effective limit
multiplies by the worker count. Install a shared cache with
`zeeb_api.throttling.set_throttle_cache()` before deploying more than one.

## API Versioning

On by default. Clients send `X-API-Version: 1.0`; a request without the header
gets `DEFAULT_VERSION`, and a version outside `ALLOWED_VERSIONS` is rejected
with a 400 `API_VERSION_INVALID` envelope.

```python
DEFAULT_VERSIONING_CLASS = "zeeb_api.versioning.HeaderVersioning"
DEFAULT_VERSION = "1.0"
ALLOWED_VERSIONS = ["1.0"]
VERSION_PARAM = "version"        # QueryParameterVersioning / URLPathVersioning
VERSION_HEADER = "X-API-Version"  # HeaderVersioning
```

This needs `"zeeb_api.versioning.VersioningMiddleware"` in `MIDDLEWARE` — the
generated project lists it. Keep `CORSMiddleware` ahead of it: an invalid
version is answered by the middleware itself, and without CORS on the outside
a browser sees an opaque CORS failure instead of the error.

Set `ALLOWED_VERSIONS` empty to accept any version, or
`DEFAULT_VERSIONING_CLASS` empty to switch versioning off.

## OAuth2 / OIDC

Requires the extra: `pip install "zeebpy[oauth]"`.

A provider activates as soon as its client id is in the environment, and the
generated `urls.py` mounts the OAuth routes only while `OAUTH_PROVIDERS` is
non-empty — so half-configured endpoints never reach the schema.

```python
from zeeb_api.conf.env import env_oauth_providers

OAUTH_PROVIDERS = env_oauth_providers()   # google / github / azure, from the env
OAUTH_AUTO_CREATE_USERS = True
OAUTH_LINK_BY_EMAIL = True
OAUTH_REQUIRE_VERIFIED_EMAIL = True
OAUTH_STATE_TTL_SECONDS = 600
OAUTH_REDIRECT_URI = None
OAUTH_SUCCESS_REDIRECT = None
OAUTH_ALLOWED_REDIRECT_HOSTS = []
OAUTH_ACCEPT_EXTERNAL_TOKENS = []   # e.g. ["azure"] to accept its bearer tokens
```

Or write the dict yourself:

```python
OAUTH_PROVIDERS = {
    "google": {"client_id": "...", "client_secret": "..."},
    "azure": {"client_id": "...", "client_secret": "...", "tenant": "common"},
}
```

The `auth_external_identities` table ships in the initial migration whether or
not a provider is configured, so enabling one later needs no schema change.

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

Every setting the framework reads, with the default it falls back to. A
generated project sets most of them from the environment — see
[Environment configuration](#environment-configuration).

| Setting | Default | Description |
|---------|---------|-------------|
| `DEBUG` | `False` | Debug mode |
| `SECRET_KEY` | (unsafe default; refused when `DEBUG=False`) | Secret key for JWT |
| `DATABASE` | SQLite | Database configuration |
| `ROOT_URLCONF` | `None` | URL configuration module |
| `MIDDLEWARE` | CORS + JWT | Middleware classes |
| `INSTALLED_APPS` | `[]` | Installed applications |
| `MIGRATIONS_DIR` | `None` | Migration files directory (None = framework default) |
| `ENFORCE_MIGRATIONS` | `True` | Refuse to start with unapplied migrations (outside `DEBUG`) |
| `AUTH_USER_MODEL` | `None` | Custom user model (the scaffold sets `"accounts.User"`) |
| `AUTH_LOAD_USER_FROM_DB` | `True` | Load user from database |
| `JWT_SECRET_KEY` | `SECRET_KEY` | JWT signing key (insecure defaults refused when `DEBUG=False`) |
| `JWT_ALGORITHM` | `"HS256"` | JWT algorithm |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access token lifetime |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime |
| `JWT_ISSUER` | `None` | Expected `iss` claim (verified when set) |
| `JWT_AUDIENCE` | `None` | Expected `aud` claim (verified when set) |
| `OAUTH_PROVIDERS` | `{}` | Provider configs; non-empty mounts the OAuth routes |
| `OAUTH_AUTO_CREATE_USERS` | `True` | Provision a user on first OAuth login |
| `OAUTH_LINK_BY_EMAIL` | `True` | Link an external identity to an existing account by email |
| `OAUTH_REQUIRE_VERIFIED_EMAIL` | `True` | Require `email_verified` before linking/provisioning by email |
| `OAUTH_STATE_TTL_SECONDS` | `600` | Lifetime of the signed OAuth state |
| `OAUTH_REDIRECT_URI` | `None` | Override the callback URL sent to the provider |
| `OAUTH_SUCCESS_REDIRECT` | `None` | Where to send the browser after a successful login |
| `OAUTH_ALLOWED_REDIRECT_HOSTS` | `[]` | Hosts a caller may request a redirect to |
| `OAUTH_ACCEPT_EXTERNAL_TOKENS` | `[]` | Providers whose bearer tokens are accepted directly |
| `CORS_ALLOW_ORIGINS` | `[]` | Allowed CORS origins (the middleware is skipped when this and the regex are both empty) |
| `CORS_ALLOW_ORIGIN_REGEX` | `None` | Origin pattern for preview deployments whose hostname changes per build |
| `CORS_ALLOW_CREDENTIALS` | `True` | Allow credentials (refused with a `"*"` origin outside `DEBUG`) |
| `CORS_ALLOW_METHODS` | `["*"]` | Allowed methods |
| `CORS_ALLOW_HEADERS` | `["*"]` | Allowed headers |
| `CORS_EXPOSE_HEADERS` | `[]` | Response headers exposed to the browser |
| `CORS_MAX_AGE` | `600` | Preflight cache lifetime (seconds) |
| `API_TITLE` | `"Zeeb API"` | API title |
| `API_DESCRIPTION` | `""` | API description |
| `API_VERSION` | `"1.0.0"` | API version |
| `API_PREFIX` | `""` | URL prefix |
| `DEFAULT_LIMIT` | `20` | Default pagination limit (query endpoint + LimitOffsetPagination) |
| `MAX_LIMIT` | `100` | Maximum pagination limit (query endpoint + LimitOffsetPagination) |
| `DEFAULT_THROTTLE_CLASSES` | `[]` | Throttle classes for ViewSets that set none |
| `DEFAULT_THROTTLE_RATES` | `{"anon": None, "user": None}` | Rate per throttle scope |
| `THROTTLE_NUM_PROXIES` | `None` | Trusted reverse proxies when reading `X-Forwarded-For` |
| `DEFAULT_VERSIONING_CLASS` | `None` | Versioning scheme (needs `VersioningMiddleware`) |
| `DEFAULT_VERSION` | `None` | Version assumed when the client sends none |
| `ALLOWED_VERSIONS` | `None` | Accepted versions (empty/None accepts any) |
| `VERSION_PARAM` | `"version"` | Query/path parameter carrying the version |
| `VERSION_HEADER` | `"X-API-Version"` | Header carrying the version |
| `LOGGING` | level `INFO`, no file | Logging configuration, applied by `create_app()` |
| `INSTALL_HEALTH_ROUTES` | `False` | Install default `/health` and `/ready` routes |
| `INSTALL_EXCEPTION_HANDLERS` | `True` | Install default handlers |

## Next Steps

- [Logging](logging.md) - Logging configuration
- [Database](database.md) - Database setup
- [ViewSets](../api/viewsets.md) - Creating API endpoints
