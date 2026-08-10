"""
Authentication URL patterns.

Like Django's auth URLs, these can be included in your urls.py.

Usage:
    # urls.py
    from zeeb_api.routers import DefaultRouter, include
    from zeeb_api.auth.urls import auth_patterns
    
    router = DefaultRouter()
    router.include(auth_patterns, prefix="/auth")
"""

from zeeb_api.auth.router import create_auth_router


def get_auth_patterns(
    enable_registration: bool = True,
    use_database: bool = True,
    prefix: str = "",
    tags: list[str] | None = None,
    login_throttle: str | None = None,
):
    """
    Get auth URL patterns with custom configuration.

    Args:
        enable_registration: Include /register endpoint
        use_database: Use database-backed authentication
        prefix: URL prefix (usually set via include())
        tags: OpenAPI tags
        login_throttle: Rate limit for /login and /register, e.g. "10/min"

    Returns:
        APIRouter with auth endpoints
    """
    return create_auth_router(
        prefix=prefix,
        tags=tags,
        enable_registration=enable_registration,
        use_database=use_database,
        login_throttle=login_throttle,
    )


# Default auth patterns (database-backed, with registration)
# This is the most common use case - just include these directly
auth_patterns = get_auth_patterns()
