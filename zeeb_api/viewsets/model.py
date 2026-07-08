"""Model ViewSet classes."""

from __future__ import annotations

from zeeb_api.viewsets.base import GenericViewSet
from zeeb_api.viewsets.mixins import (
    CreateModelMixin,
    QueryModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
    DestroyModelMixin,
)


class ModelViewSet(
    CreateModelMixin,
    QueryModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
    DestroyModelMixin,
    GenericViewSet,
):
    """
    ViewSet with full CRUD operations.
    
    Provides:
    - query (POST /items/query/) - Query with Q filters, ordering, pagination
    - list (GET /items/) - filterable, paginated collection
    - create (POST /items/)
    - retrieve (GET /items/{id}/)
    - update (PUT /items/{id}/)
    - partial_update (PATCH /items/{id}/)
    - destroy (DELETE /items/{id}/)
    
    Usage:
        class UserViewSet(ModelViewSet):
            queryset = User.objects
            serializer_class = UserSerializer
            permission_classes = [IsAuthenticated]
    """
    pass


class ReadOnlyModelViewSet(
    QueryModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    GenericViewSet,
):
    """
    ViewSet with read-only operations.
    
    Provides:
    - query (POST /items/query/) - Query with Q filters, ordering, pagination
    - list (GET /items/) - filterable, paginated collection
    - retrieve (GET /items/{id}/)
    
    Usage:
        class PublicUserViewSet(ReadOnlyModelViewSet):
            queryset = User.objects
            serializer_class = UserSerializer
    """
    pass
