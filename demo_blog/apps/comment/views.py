"""comment views."""

from zeeb_api import permissions, viewsets

from .models import Comment
from .serializers import CommentSerializer


class CommentViewSet(viewsets.ModelViewSet):
    queryset = Comment.objects.all()
    serializer_class = CommentSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    async def perform_create(self, serializer):
        await serializer.save(author_id=self.request.state.user.id)
