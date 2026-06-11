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
    from zeeb_api.exception_handlers import install_exception_handlers
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
        lifespan = _create_default_lifespan(settings)
    
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
    
    return app


def _create_default_lifespan(settings: "Settings"):
    """
    Create a default lifespan that handles database setup/teardown.
    """
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Try to setup database if configured
        database_config = getattr(settings, 'DATABASE', None)
        db = None
        
        if database_config and database_config.get('url'):
            try:
                from zeeb_orm import setup_database, close_all_connections
                db_url = database_config['url']
                db = await setup_database(db_url)
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


def get_asgi_application(settings_module: str | None = None) -> FastAPI:
    """
    Get the ASGI application.
    
    This is a convenience function that can be used as the ASGI entry point.
    
    Usage in asgi.py:
        from zeeb_api import get_asgi_application
        application = get_asgi_application("myproject.settings")
    """
    return create_app(settings_module)
