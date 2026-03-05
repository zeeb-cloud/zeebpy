"""
comment views.

Define your API viewsets here.
"""

from zeeb_api import viewsets, permissions
# from .models import YourModel
# from .serializers import YourModelSerializer


# Example viewset:
# class CommentViewSet(viewsets.ModelViewSet):
#     queryset = Comment.objects.all()
#     serializer_class = CommentSerializer
#     permission_classes = [permissions.IsAuthenticatedOrReadOnly]
#
#     @viewsets.action(detail=True, methods=["post"])
#     async def custom_action(self, request, pk=None):
#         obj = await self.get_object()
#         # Do something
#         return {"status": "success"}
