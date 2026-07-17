"""ViewSet mixins for CRUD operations."""

from __future__ import annotations

from typing import Any
from fastapi import Request

from zeeb_api.exceptions import ValidationError
from zeeb_api.response import Response


class CreateModelMixin:
    """Mixin for create (POST) operation."""
    
    async def create(self, request: Request, **kwargs: Any) -> dict[str, Any]:
        """Create a new model instance."""
        # Check add permission if object permissions are enabled
        if hasattr(self, "check_add_permission"):
            await self.check_add_permission()
        
        # Get data from pre-validated body or parse from request
        if hasattr(self, "_request_body"):
            data = self._request_body
        else:
            data = await request.json()
        
        serializer = self.get_serializer(data=data)
        
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)
        
        # Call perform_create hook to allow modification of validated_data
        # (or to save the instance itself, e.g. serializer.save(author_id=...)).
        await self.perform_create(serializer)

        instance = await self._save_from_hook(serializer)

        # Return serialized response
        output_serializer = self.get_serializer(instance=instance)
        return await output_serializer.adata()

    async def _save_from_hook(self, serializer: Any) -> Any:
        """Save the serializer unless a perform_* hook already saved it.

        A hook may either mutate ``serializer.validated_data`` and leave saving
        to the framework, or call ``await serializer.save(...)`` itself. The
        latter sets ``serializer._saved``; honoring it here avoids a second
        insert/update. ``getattr`` default keeps non-``Serializer`` classes on
        the previous always-save behavior.
        """
        if getattr(serializer, "_saved", False):
            return serializer.instance
        return await serializer.save()

    async def perform_create(self, serializer: Any) -> None:
        """
        Hook called before saving a new instance.

        Inject server-owned values (e.g. the authenticated user) here rather
        than trusting the request body. Two equivalent styles:

            # Preferred: mutate validated_data, let the framework save.
            async def perform_create(self, serializer):
                serializer.validated_data["author_id"] = self.request.state.user.id

            # Also supported: save yourself; the framework will not save again.
            async def perform_create(self, serializer):
                await serializer.save(author_id=self.request.state.user.id)
        """
        pass


class QueryModelMixin:
    """Mixin for query (POST /query/) operation with Q filters."""
    
    async def query(self, request: Request, **kwargs: Any) -> dict[str, Any]:
        """
        Query model instances with Q filter, ordering, and pagination.
        
        Request body:
            {
                "filter": "Q(name__icontains='john') | Q(active=True)",
                "order_by": ["-created_at", "name"],
                "limit": 20,
                "offset": 0
            }
        
        Response:
            {
                "count": 100,
                "limit": 20,
                "offset": 0,
                "results": [...]
            }
        """
        from zeeb_api.query import parse_q_filter, QFilterError
        
        # Get query params from request body
        if hasattr(self, "_request_body"):
            query_params = self._request_body
        else:
            query_params = await request.json()
        
        # Start with base queryset - ensure it's a QuerySet not Manager
        queryset = self.get_queryset()
        if hasattr(queryset, 'all'):
            queryset = queryset.all()
        
        # Apply Q filter
        filter_expr = query_params.get("filter")
        if filter_expr:
            try:
                q_filter = parse_q_filter(filter_expr)
                queryset = queryset.filter(q_filter)
            except QFilterError as e:
                raise ValidationError({"filter": [str(e)]})
        
        # Apply ordering
        order_by = query_params.get("order_by")
        if order_by:
            if isinstance(order_by, str):
                order_by = [order_by]
            queryset = queryset.order_by(*order_by)
        
        # Get pagination params (limit/offset)
        limit = query_params.get("limit", 20)
        offset = query_params.get("offset", 0)
        
        # Clamp limit
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        
        # Get total count
        count = await queryset.count()
        
        # Get paginated items using Django-style slicing
        items = await queryset[offset:offset + limit]
        
        # Serialize
        serializer = self.get_serializer(instance=items, many=True)

        return {
            "count": count,
            "limit": limit,
            "offset": offset,
            "results": await serializer.adata(),
        }


class ListModelMixin:
    """Mixin for list (GET collection) operation."""
    
    async def list(self, request: Request, **kwargs: Any) -> dict[str, Any]:
        """List all model instances."""
        queryset = self.get_queryset()
        queryset = self.filter_queryset(queryset)
        
        # Paginate
        items, page_info = await self.paginate_queryset(queryset)
        
        # Serialize
        serializer = self.get_serializer(instance=items, many=True)

        if page_info:
            return {
                "count": page_info.get("count", len(items)),
                "next": page_info.get("next"),
                "previous": page_info.get("previous"),
                "results": await serializer.adata(),
            }

        return await serializer.adata()


class RetrieveModelMixin:
    """Mixin for retrieve (GET single) operation."""
    
    async def retrieve(self, request: Request, **kwargs: Any) -> dict[str, Any]:
        """Retrieve a single model instance."""
        instance = await self.get_object()
        serializer = self.get_serializer(instance=instance)
        return await serializer.adata()


class UpdateModelMixin:
    """Mixin for update (PUT/PATCH) operations."""
    
    async def update(self, request: Request, **kwargs: Any) -> dict[str, Any]:
        """Update a model instance (full update)."""
        instance = await self.get_object()
        
        # Get data from pre-validated body or parse from request
        if hasattr(self, "_request_body"):
            data = self._request_body
        else:
            data = await request.json()
        
        serializer = self.get_serializer(
            instance=instance,
            data=data,
            partial=False,
        )

        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        await self.perform_update(serializer)
        instance = await self._save_from_hook(serializer)
        return await self.get_serializer(instance=instance).adata()

    async def partial_update(self, request: Request, **kwargs: Any) -> dict[str, Any]:
        """Partially update a model instance."""
        instance = await self.get_object()
        
        # Get data from pre-validated body or parse from request
        if hasattr(self, "_request_body"):
            data = self._request_body
        else:
            data = await request.json()
        
        serializer = self.get_serializer(
            instance=instance,
            data=data,
            partial=True,
        )

        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        await self.perform_update(serializer)
        instance = await self._save_from_hook(serializer)
        return await self.get_serializer(instance=instance).adata()

    async def _save_from_hook(self, serializer: Any) -> Any:
        """Save unless perform_update already saved (see CreateModelMixin)."""
        if getattr(serializer, "_saved", False):
            return serializer.instance
        return await serializer.save()

    async def perform_update(self, serializer: Any) -> None:
        """
        Hook called before saving an updated instance.

        Mutate ``serializer.validated_data``, or call
        ``await serializer.save(...)`` yourself — the framework will not save a
        second time. Example:

            async def perform_update(self, serializer):
                serializer.validated_data["updated_by_id"] = self.request.state.user.id
        """
        pass


class DestroyModelMixin:
    """Mixin for destroy (DELETE) operation."""
    
    async def destroy(self, request: Request, **kwargs: Any) -> None:
        """Delete a model instance."""
        instance = await self.get_object()
        await self.perform_destroy(instance)
        return None
    
    async def perform_destroy(self, instance: Any) -> None:
        """Perform the delete operation."""
        await instance.delete()
