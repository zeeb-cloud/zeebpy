"""Project URL configuration.

``create_app()`` loads this module (settings.ROOT_URLCONF) and mounts every
router below under settings.API_PREFIX.

Authentication is always mounted: POST <prefix>/login, /register, /refresh,
/logout and GET <prefix>/me. The OAuth2/OIDC routes are mounted only once
settings.OAUTH_PROVIDERS is non-empty — that is, once a provider client id is
present in the environment — so half-configured endpoints never reach the
schema.

App routers are appended below by ``zeeb startapp``. Keep the module-level
``router`` symbol and the ``get_routes()`` function so that stays automatic.
"""

from zeeb_api.auth import create_auth_router
from zeeb_api.auth.oauth import create_oauth_router
from zeeb_api.conf import settings
from zeeb_api.routers import DefaultRouter
from apps.accounts.urls import router as accounts_router
from apps.post.urls import router as post_router
from apps.comment.urls import router as comment_router

_AUTH_PREFIX = getattr(settings, "AUTH_URL_PREFIX", "/auth")
_REGISTRATION = getattr(settings, "AUTH_ENABLE_REGISTRATION", True)
_LOGIN_THROTTLE = getattr(settings, "AUTH_LOGIN_THROTTLE_RATE", None)

# Main router
router = DefaultRouter()

router.include(create_auth_router(prefix=_AUTH_PREFIX, enable_registration=_REGISTRATION, login_throttle=_LOGIN_THROTTLE))

if getattr(settings, "OAUTH_PROVIDERS", None):
    router.include(create_oauth_router(prefix=_AUTH_PREFIX))

# App routers are included above this line.
router.include(accounts_router)
router.include(post_router)
router.include(comment_router)


def get_routes():
    """Return all routes for the FastAPI app."""
    return router.routes
