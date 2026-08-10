"""comment URL configuration."""

from zeeb_api.routers import DefaultRouter

from .views import CommentViewSet

router = DefaultRouter()
router.register("comments", CommentViewSet)
