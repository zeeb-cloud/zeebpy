"""post URL configuration."""

from zeeb_api.routers import DefaultRouter

from .views import PostViewSet

router = DefaultRouter()
router.register("posts", PostViewSet)
