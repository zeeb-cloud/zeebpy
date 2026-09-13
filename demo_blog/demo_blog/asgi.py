"""
demo_blog ASGI application.

Thin entry point. ``zeeb_api.create_app()`` builds everything from settings.py:
logging (LOGGING), middleware (MIDDLEWARE), the standard error envelope, JWT,
the routes from ROOT_URLCONF, the /health and /ready probes
(INSTALL_HEALTH_ROUTES), and a migration-aware startup lifespan. Configure
behavior in settings.py, not here.
"""

from zeeb_api import create_app

app = create_app("demo_blog.settings")
