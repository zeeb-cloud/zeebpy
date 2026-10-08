"""
ViewSets - DRF-like viewsets for FastAPI.

Provides ViewSet and ModelViewSet classes for CRUD operations.
"""

from zeeb_api.viewsets.base import ViewSet, GenericViewSet
from zeeb_api.viewsets.model import ModelViewSet, ReadOnlyModelViewSet
from zeeb_api.viewsets.decorators import action, extend_schema
from zeeb_api.viewsets.mixins import (
    CreateModelMixin,
    QueryModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
    DestroyModelMixin,
)

__all__ = [
    "ViewSet",
    "GenericViewSet",
    "ModelViewSet",
    "ReadOnlyModelViewSet",
    "action",
    "extend_schema",
    "CreateModelMixin",
    "QueryModelMixin",
    "ListModelMixin",
    "RetrieveModelMixin",
    "UpdateModelMixin",
    "DestroyModelMixin",
]
