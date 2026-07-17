"""
Zeeb API Application Factory.

Provides Django-style application creation that auto-configures from settings.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Callable, Awaitable

from fastapi import FastAPI

if TYPE_CHECKING:
    from zeeb_api.conf.settings import Settings


async def default_lifespan(app: FastAPI):
    """Default lifespan that does nothing."""
    yield


def create_app(
    settings_module: str | None = None,
    lifespan: Callable[[FastAPI], Any] | None = None,
    **extra_settings,
) -> FastAPI:
    """
    Create and configure a FastAPI application from settings.
    
    This is the main entry point for creating a Zeeb API application.
    It reads configuration from your project's settings module and
    automatically configures:
    - Middleware (from MIDDLEWARE setting)
    - Exception handlers
    - JWT authentication
    - URL routing (from ROOT_URLCONF setting)
    - Signal receivers (imports ``<app>.signals`` for every INSTALLED_APPS
      entry, so receivers scaffolded into ``apps/<app>/signals.py`` load at
      startup)
    
    Args:
        settings_module: Dotted path to settings module (e.g., "myproject.settings").
                        If not provided, uses ZEEB_SETTINGS_MODULE env var or auto-detects.
        lifespan: Custom lifespan context manager for startup/shutdown.
                 If not provided, uses default (database setup if configured).
        **extra_settings: Override settings programmatically.
    
    Returns:
        Configured FastAPI application.
    
    Usage:
        # Simple usage (auto-detect settings)
        from zeeb_api import create_app
        app = create_app()
        
        # Explicit settings module
        app = create_app("myproject.settings")
        
        # With custom lifespan
        @asynccontextmanager
        async def lifespan(app):
            await setup_database()
            yield
            await cleanup()
        
        app = create_app(lifespan=lifespan)
    """
    from zeeb_api.auth.jwt import INSECURE_SECRETS, configure_jwt
    from zeeb_api.conf import configure_settings, settings
    from zeeb_api.exception_handlers import (
        install_error_response_schema,
        install_exception_handlers,
    )
    from zeeb_api.exceptions import ImproperlyConfigured
    from zeeb_api.middleware import install_middleware
    from zeeb_api.routers import load_urlconf
    
    # Configure settings
    if settings_module:
        configure_settings(settings_module)
    
    # Apply any extra settings
    for key, value in extra_settings.items():
        setattr(settings, key, value)
    
    # Ensure settings are loaded
    if not settings.is_configured():
        settings._setup()

    # Fail fast on insecure default secrets outside of DEBUG mode
    if not getattr(settings, 'DEBUG', False) and settings.get_jwt_secret_key() in INSECURE_SECRETS:
        raise ImproperlyConfigured(
            "create_app(): SECRET_KEY/JWT_SECRET_KEY is an insecure default. "
            "Set a strong unique secret or enable DEBUG for local development."
        )

    # Create lifespan if not provided
    if lifespan is None:
        lifespan = _create_default_lifespan(settings, check_migrations=True)
    
    # Create FastAPI app
    app = FastAPI(
        title=getattr(settings, 'API_TITLE', 'Zeeb API'),
        description=getattr(settings, 'API_DESCRIPTION', ''),
        version=getattr(settings, 'API_VERSION', '1.0.0'),
        debug=getattr(settings, 'DEBUG', False),
        lifespan=lifespan,
    )
    
    # Configure JWT from settings
    configure_jwt(
        secret_key=settings.get_jwt_secret_key(),
        algorithm=getattr(settings, 'JWT_ALGORITHM', 'HS256'),
        access_token_expire_minutes=getattr(settings, 'JWT_ACCESS_TOKEN_EXPIRE_MINUTES', 60),
        refresh_token_expire_days=getattr(settings, 'JWT_REFRESH_TOKEN_EXPIRE_DAYS', 7),
        issuer=getattr(settings, 'JWT_ISSUER', None),
        audience=getattr(settings, 'JWT_AUDIENCE', None),
    )
    
    # Install middleware from settings
    install_middleware(app, settings)
    
    # Install exception handlers
    if getattr(settings, 'INSTALL_EXCEPTION_HANDLERS', True):
        install_exception_handlers(app)
        install_error_response_schema(app)
    
    # Load and include URL routes
    root_urlconf = getattr(settings, 'ROOT_URLCONF', None)
    api_prefix = getattr(settings, 'API_PREFIX', '')
    
    if root_urlconf:
        routes = load_urlconf(root_urlconf)
        for route in routes:
            if api_prefix:
                app.include_router(route, prefix=api_prefix)
            else:
                app.include_router(route)

    # Load signal receivers: nothing else imports apps/<app>/signals.py, so
    # without this the receivers scaffolded there never connect.
    _autodiscover_signal_modules(settings)

    # Standard liveness/readiness probes (opt-in via INSTALL_HEALTH_ROUTES so
    # services that define their own /health are never shadowed).
    if getattr(settings, 'INSTALL_HEALTH_ROUTES', False):
        _install_health_routes(app)

    return app


def _autodiscover_signal_modules(settings: "Settings") -> None:
    """Import ``<app>.signals`` for every ``INSTALLED_APPS`` entry.

    Only the signals module itself being absent is skipped (most apps have
    none) — a ``ModuleNotFoundError`` raised by an import *inside* an existing
    ``signals.py`` propagates, so a broken receiver module fails startup loudly
    instead of silently never firing. Mirrors the module-missing discrimination
    of zeeb_orm's model autodiscovery.
    """
    import importlib

    for entry in getattr(settings, 'INSTALLED_APPS', []) or []:
        if not isinstance(entry, str):
            continue
        module_path = f"{entry}.signals"
        try:
            importlib.import_module(module_path)
        except ModuleNotFoundError as e:
            if e.name is not None and module_path.startswith(e.name):
                continue
            raise


def _create_default_lifespan(settings: "Settings", check_migrations: bool = False):
    """
    Create a default lifespan that handles database setup/teardown.

    When ``check_migrations`` is True the lifespan verifies migrations are
    applied before startup (fatal in production, a warning under DEBUG) — the
    guarantee generated projects used to wire into their own asgi.py. Callers
    that compose this lifespan themselves keep the default (False), so their
    startup behavior is unchanged.
    """
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if check_migrations:
            _check_migrations_on_startup(settings)

        # Try to setup database if configured
        database_config = getattr(settings, 'DATABASE', None)
        db = None

        if database_config and database_config.get('url'):
            try:
                from zeeb_orm import setup_database

                from zeeb_api.conf.orm import apply_orm_settings, database_kwargs

                # Hand the project settings down to the ORM layer so
                # zeeb_orm.get_settings() reflects them (migrations etc.).
                apply_orm_settings(settings)
                db = await setup_database(
                    database_config['url'], **database_kwargs(database_config)
                )
            except ImportError:
                # zeeb_orm not installed, skip database setup
                pass
        
        yield
        
        # Cleanup database connections
        if db is not None:
            try:
                from zeeb_orm import close_all_connections
                await close_all_connections()
            except ImportError:
                pass

    return lifespan


def _check_migrations_on_startup(settings: "Settings") -> None:
    """
    Refuse to start (or warn) when migrations are unapplied.

    Fatal when ``ENFORCE_MIGRATIONS`` and not ``DEBUG`` (raises ``SystemExit``),
    otherwise a warning. No-op when ``zeeb_orm`` is not installed.
    """
    try:
        from zeeb_orm import MigrationError, check_migrations_applied
    except ImportError:
        return

    from zeeb_api.logging import get_logger

    logger = get_logger(__name__)
    enforce = getattr(settings, 'ENFORCE_MIGRATIONS', True)
    debug = getattr(settings, 'DEBUG', False)

    try:
        check_migrations_applied(raise_on_pending=True)
    except MigrationError as e:
        separator = "=" * 60
        commands = (
            "\nRun the following commands:\n"
            "  python manage.py init            # If not already done\n"
            "  python manage.py makemigrations  # Create migrations\n"
            "  python manage.py migrate         # Apply migrations"
        )
        if enforce and not debug:
            logger.error("Unapplied migrations detected", error=str(e))
            print(f"\n{separator}\nERROR: Unapplied migrations detected!\n{e}{commands}\n{separator}\n")
            raise SystemExit(1)
        logger.warning("Unapplied migrations detected", error=str(e))
        print(f"\n{separator}\nWARNING: Unapplied migrations detected!\n{e}{commands}\n{separator}\n")


def _install_health_routes(app: FastAPI) -> None:
    """Register root liveness/readiness probes (``/health``, ``/ready``)."""

    @app.get("/health", include_in_schema=False)
    async def _health() -> dict:
        return {"status": "ok"}

    @app.get("/ready", include_in_schema=False)
    async def _ready():
        from fastapi.responses import JSONResponse
        from sqlalchemy import text
        from zeeb_orm.db import get_database

        db = get_database()
        if db is None:
            return JSONResponse(
                {"status": "not_ready", "db": "not configured"}, status_code=503
            )
        try:
            async with db.session() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse(
                {"status": "not_ready", "db": "unreachable"}, status_code=503
            )
        return {"status": "ready", "db": "ok"}


def get_asgi_application(settings_module: str | None = None) -> FastAPI:
    """
    Get the ASGI application.
    
    This is a convenience function that can be used as the ASGI entry point.
    
    Usage in asgi.py:
        from zeeb_api import get_asgi_application
        application = get_asgi_application("myproject.settings")
    """
    return create_app(settings_module)
