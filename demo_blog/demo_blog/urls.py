"""
demo_blog URL configuration.

Register your app routers here.
"""

from zeeb_api.routers import DefaultRouter

# Main router
router = DefaultRouter()

# Include app routers
# from apps.myapp.urls import router as myapp_router
# router.include(myapp_router, prefix="myapp")


def get_routes():
    """Return all routes for the FastAPI app."""
    return router.routes
