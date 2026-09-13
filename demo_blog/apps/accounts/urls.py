"""accounts URL configuration."""

from zeeb_api.routers import DefaultRouter

from .views import UserViewSet

router = DefaultRouter()
router.register("users", UserViewSet)
