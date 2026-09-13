"""post views."""

from zeeb_api import permissions, viewsets

from .models import Post
from .serializers import PostSerializer


class PostViewSet(viewsets.ModelViewSet):
    """Anyone may read; writing needs a token, and the author is the caller."""

    queryset = Post.objects.all()
    serializer_class = PostSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    async def perform_create(self, serializer):
        await serializer.save(author_id=self.request.state.user.id)
