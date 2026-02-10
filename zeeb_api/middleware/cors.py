"""
CORS middleware wrapper for settings-based configuration.
"""

from typing import Any
from starlette.middleware.cors import CORSMiddleware as StarletteCORSMiddleware


class CORSMiddleware(StarletteCORSMiddleware):
    """
    CORS middleware that can be configured via settings.
    
    Configuration via settings.py:
        MIDDLEWARE = [
            "zeeb_api.middleware.CORSMiddleware",
        ]
        CORS_ALLOW_ORIGINS = ["http://localhost:3000"]
        CORS_ALLOW_CREDENTIALS = True
        CORS_ALLOW_METHODS = ["*"]
        CORS_ALLOW_HEADERS = ["*"]
    
    Direct usage:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:3000"],
        )
    """
    
    def __init__(
        self,
        app: Any,
        allow_origins: list[str] | None = None,
        allow_credentials: bool | None = None,
        allow_methods: list[str] | None = None,
        allow_headers: list[str] | None = None,
        expose_headers: list[str] | None = None,
        max_age: int = 600,
    ):
        """
        Initialize CORS middleware.
        
        If parameters are None, reads from settings.
        """
        from zeeb_api.conf import settings
        
        # Get values from settings if not provided
        if allow_origins is None:
            allow_origins = getattr(settings, 'CORS_ALLOW_ORIGINS', [])
        if allow_credentials is None:
            allow_credentials = getattr(settings, 'CORS_ALLOW_CREDENTIALS', True)
        if allow_methods is None:
            allow_methods = getattr(settings, 'CORS_ALLOW_METHODS', ["*"])
        if allow_headers is None:
            allow_headers = getattr(settings, 'CORS_ALLOW_HEADERS', ["*"])
        if expose_headers is None:
            expose_headers = getattr(settings, 'CORS_EXPOSE_HEADERS', [])
        
        super().__init__(
            app,
            allow_origins=allow_origins,
            allow_credentials=allow_credentials,
            allow_methods=allow_methods,
            allow_headers=allow_headers,
            expose_headers=expose_headers,
            max_age=max_age,
        )
