"""ViewSet mixins for CRUD operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import Request

from zeeb_api.exceptions import ValidationError


async def _is_valid(serializer: Any) -> bool:
    """Validate with async hooks when the serializer supports them."""
    ais_valid = getattr(serializer, "ais_valid", None)
    if ais_valid is not None:
        return await ais_valid()
    return serializer.is_valid()


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

        if not await _is_valid(serializer):
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

    #: Whether ``POST /query`` filters may use ``regex``/``iregex`` lookups.
    #: Off by default: on SQLite they run Python's ``re`` in the database, so a
    #: crafted pattern (catastrophic backtracking) can pin a worker.
    query_allow_regex: bool = False

    if TYPE_CHECKING:
        # Provided by GenericViewSet, which every viewset using this mixin is.
        def filter_queryset(self, queryset: Any) -> Any: ...

    def _allowed_query_fields(self) -> set[str] | dict[str, str] | None:
        """Field paths a client may filter or order by.

        An explicit ``query_fields`` attribute on the viewset wins; it may list
        paths through relations (``"author__name"``). Otherwise the
        serializer's output decides (``serializer_field_paths``): a client
        names the fields the API returns, by the names it returns them under,
        and each is translated to the column behind it. A column the API does
        not surface (a password hash, a value a ``SerializerMethodField``
        masks) cannot be filtered or ordered by, nor can one on a related
        model. Returns None when the set cannot be determined, leaving the
        query unrestricted (backwards-compatible for untyped viewsets).
        """
        from zeeb_api.query import serializer_field_paths

        explicit = getattr(self, "query_fields", None)
        if explicit is not None:
            return dict(explicit) if isinstance(explicit, dict) else set(explicit)

        get_serializer_class = getattr(self, "get_serializer_class", None)
        if get_serializer_class is None:
            return None
        try:
            serializer_class = get_serializer_class()
        except Exception:
            return None
        if serializer_class is None:
            return None

        return serializer_field_paths(serializer_class)

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

        Every field path in ``filter`` and ``order_by`` is checked in full
        against the allow-list (see ``zeeb_api.query.paths``).
        """
        from zeeb_api.query import (
            FieldPathError,
            QFilterError,
            check_field_path,
            parse_q_filter_with_paths,
            rename_q_paths,
        )

        # Get query params from request body
        if hasattr(self, "_request_body"):
            query_params = self._request_body
        else:
            query_params = await request.json()

        # Start with base queryset - ensure it's a QuerySet not Manager. The
        # filter backends apply here as on the list: one that scopes rows must
        # scope /query as well. The body's order_by replaces their ordering.
        queryset = self.filter_queryset(self.get_queryset())
        if hasattr(queryset, 'all'):
            queryset = queryset.all()
        model = getattr(queryset, "model", None)

        # Field paths a client is allowed to filter / order by (None = unrestricted).
        allowed_fields = self._allowed_query_fields()
        allow_regex = bool(getattr(self, "query_allow_regex", False))

        # Apply Q filter
        filter_expr = query_params.get("filter")
        if filter_expr:
            # Parsed once: the paths are checked before the Q is applied.
            try:
                q_filter, referenced = parse_q_filter_with_paths(filter_expr)
            except QFilterError as e:
                raise ValidationError({"filter": [str(e)]})
            refused = []
            renames: dict[str, str] = {}
            for path in sorted(referenced):
                try:
                    used = check_field_path(
                        model, path, allowed_fields, allow_regex=allow_regex
                    )
                except FieldPathError as e:
                    refused.append(str(e))
                    continue
                if used != path:
                    renames[path] = used
            if refused:
                raise ValidationError({"filter": refused})
            # Output names (``nick``) become the columns behind them (``nickname``).
            queryset = queryset.filter(rename_q_paths(q_filter, renames))

        # Apply ordering
        order_by = query_params.get("order_by")
        if order_by:
            if isinstance(order_by, str):
                order_by = [order_by]
            terms = []
            for field in order_by:
                try:
                    terms.append(
                        check_field_path(model, field, allowed_fields, allow_regex=False)
                    )
                except FieldPathError as e:
                    raise ValidationError(
                        {"order_by": [f"Cannot order by '{field}': {e}"]}
                    )
            queryset = queryset.order_by(*terms)

        # Get pagination params (limit/offset). Defaults/caps come from the
        # DEFAULT_LIMIT / MAX_LIMIT settings (falling back to 20 / 100).
        from zeeb_api.conf import settings

        default_limit = int(getattr(settings, "DEFAULT_LIMIT", 20))
        max_limit = int(getattr(settings, "MAX_LIMIT", 100))
        limit = query_params.get("limit", default_limit)
        offset = query_params.get("offset", 0)

        # Clamp limit
        limit = max(1, min(limit, max_limit))
        offset = max(0, offset)

        # Get total count
        count = await queryset.count()

        # Get paginated items using Django-style slicing. An offset past the
        # end is an empty page without a query: a huge one (the router caps it
        # at MAX_OFFSET; a hand-made body may not) would overflow the driver.
        items = [] if offset >= count else await queryset[offset:offset + limit]

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
            # count is None when the paginator does not provide a total (e.g.
            # cursor pagination) — do not fabricate one from the page length.
            return {
                "count": page_info.get("count"),
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

        if not await _is_valid(serializer):
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

        if not await _is_valid(serializer):
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
