"""
Middleware loader that reads from settings and installs middleware.
"""

import importlib
import logging
from typing import TYPE_CHECKING

logger = logging.getLogger("zeeb_api.middleware")

if TYPE_CHECKING:
    from fastapi import FastAPI

    from zeeb_api.conf.settings import Settings


def import_string(dotted_path: str):
    """
    Import a class/function from a dotted path string.
    
    Example: "zeeb_api.middleware.JWTAuthMiddleware" -> JWTAuthMiddleware class
    """
    try:
        module_path, class_name = dotted_path.rsplit(".", 1)
    except ValueError as e:
        raise ImportError(f"'{dotted_path}' doesn't look like a module path") from e

    module = importlib.import_module(module_path)

    try:
        return getattr(module, class_name)
    except AttributeError as e:
        raise ImportError(f"Module '{module_path}' does not have attribute '{class_name}'") from e


def install_middleware(app: "FastAPI", settings: "Settings" = None):
    """
    Install middleware from settings.MIDDLEWARE.
    
    Middleware is installed in reverse order so that the first middleware
    in the list is the outermost (processes requests first, responses last).
    
    Args:
        app: FastAPI application
        settings: Settings object (uses global settings if not provided)
    
    Example settings.py:
        MIDDLEWARE = [
            "zeeb_api.middleware.CORSMiddleware",
            "zeeb_api.middleware.JWTAuthMiddleware",
        ]
    """
    if settings is None:
        from zeeb_api.conf import settings

    middleware_list = getattr(settings, 'MIDDLEWARE', [])

    # Install in reverse order (FastAPI adds middleware in LIFO order)
    for middleware_path in reversed(middleware_list):
        middleware_class = import_string(middleware_path)

        # Special handling for CORSMiddleware - skip if nothing is allowed.
        # A regex counts: a preview-deployment frontend is configured through
        # CORS_ALLOW_ORIGIN_REGEX alone, and skipping the middleware there
        # would silently break every browser request against it.
        if middleware_path == "zeeb_api.middleware.CORSMiddleware":
            cors_origins = getattr(settings, 'CORS_ALLOW_ORIGINS', [])
            cors_regex = getattr(settings, 'CORS_ALLOW_ORIGIN_REGEX', None)
            if not cors_origins and not cors_regex:
                logger.warning(
                    "CORSMiddleware is listed in MIDDLEWARE but neither "
                    "CORS_ALLOW_ORIGINS nor CORS_ALLOW_ORIGIN_REGEX is set — "
                    "skipping it (set one, e.g. via configure_cors, to activate it)."
                )
                continue

        app.add_middleware(middleware_class)
