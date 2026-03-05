"""
post views.

Define your API viewsets here.
"""

from zeeb_api import viewsets, permissions
# from .models import YourModel
# from .serializers import YourModelSerializer


# Example viewset:
# class PostViewSet(viewsets.ModelViewSet):
#     queryset = Post.objects.all()
#     serializer_class = PostSerializer
#     permission_classes = [permissions.IsAuthenticatedOrReadOnly]
#
#     @viewsets.action(detail=True, methods=["post"])
#     async def custom_action(self, request, pk=None):
#         obj = await self.get_object()
#         # Do something
#         return {"status": "success"}
