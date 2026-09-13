"""
CORS middleware wrapper for settings-based configuration.
"""

import re
from typing import Any

from starlette.middleware.cors import CORSMiddleware as StarletteCORSMiddleware

#: Regexes that match every possible origin. Combined with credentials these
#: are the wildcard hole wearing a different hat, so they are refused the same
#: way. Compared against the pattern with whitespace and anchors stripped.
_CATCH_ALL_PATTERNS = frozenset({".*", ".+", "^.*$", "^.+$", "(.*)", "(.+)"})


def _matches_everything(pattern: str) -> bool:
    """Whether *pattern* would admit any origin at all."""
    return pattern.strip() in _CATCH_ALL_PATTERNS


class CORSMiddleware(StarletteCORSMiddleware):
    """
    CORS middleware that can be configured via settings.

    Configuration via settings.py:
        MIDDLEWARE = [
            "zeeb_api.middleware.CORSMiddleware",
        ]
        CORS_ALLOW_ORIGINS = ["http://localhost:3000"]
        CORS_ALLOW_ORIGIN_REGEX = r"https://.*\\.vercel\\.app$"
        CORS_ALLOW_CREDENTIALS = True
        CORS_ALLOW_METHODS = ["*"]
        CORS_ALLOW_HEADERS = ["*"]

    ``CORS_ALLOW_ORIGIN_REGEX`` exists for frontends whose hostname changes per
    build — preview deployments on Lovable, Vercel or Netlify, where an exact
    origin list would have to be edited on every deploy. Anchor it (``$``) and
    escape the dots, or ``https://evil-vercel.app.attacker.com`` matches too.

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
        max_age: int | None = None,
        allow_origin_regex: str | None = None,
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
        if max_age is None:
            max_age = getattr(settings, 'CORS_MAX_AGE', 600)
        if allow_origin_regex is None:
            allow_origin_regex = getattr(settings, 'CORS_ALLOW_ORIGIN_REGEX', None) or None

        # A pattern that does not compile would raise deep inside Starlette on
        # the first preflight, i.e. in production traffic rather than at boot.
        if allow_origin_regex is not None:
            try:
                re.compile(allow_origin_regex)
            except re.error as exc:
                from zeeb_api.exceptions import ImproperlyConfigured

                raise ImproperlyConfigured(
                    f"CORS_ALLOW_ORIGIN_REGEX is not a valid regular expression: {exc}"
                ) from exc

        # SECURITY: `allow_origins=["*"]` together with `allow_credentials=True`
        # makes Starlette reflect the request Origin AND return
        # Access-Control-Allow-Credentials: true, i.e. *any* site can make
        # credentialed cross-origin requests. Refuse this combination outside
        # DEBUG (mirrors the SECRET_KEY fail-fast in create_app); warn in DEBUG.
        # A catch-all regex is the same hole, so it is checked here too.
        wildcard_origin = "*" in allow_origins
        wildcard_regex = allow_origin_regex is not None and _matches_everything(
            allow_origin_regex
        )
        if allow_credentials and (wildcard_origin or wildcard_regex):
            source = "a wildcard origin ('*')" if wildcard_origin else (
                f"a catch-all CORS_ALLOW_ORIGIN_REGEX ({allow_origin_regex!r})"
            )
            message = (
                f"CORS is configured with allow_credentials=True and {source}. "
                "This lets any origin send credentialed requests. List explicit "
                "origins in CORS_ALLOW_ORIGINS, anchor CORS_ALLOW_ORIGIN_REGEX to "
                "the hosts you trust, or set CORS_ALLOW_CREDENTIALS=False."
            )
            if getattr(settings, "DEBUG", False):
                import logging
                logging.getLogger(__name__).warning("Insecure CORS: %s", message)
            else:
                from zeeb_api.exceptions import ImproperlyConfigured
                raise ImproperlyConfigured(message)

        super().__init__(
            app,
            allow_origins=allow_origins,
            allow_credentials=allow_credentials,
            allow_methods=allow_methods,
            allow_headers=allow_headers,
            expose_headers=expose_headers,
            max_age=max_age,
            allow_origin_regex=allow_origin_regex,
        )
