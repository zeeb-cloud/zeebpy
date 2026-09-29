"""Base filter classes."""

from __future__ import annotations

from typing import Any, ClassVar, TYPE_CHECKING
from fastapi import Request

if TYPE_CHECKING:
    from zeeb_api.viewsets.base import ViewSet


class BaseFilter:
    """
    Base filter class.
    
    Override filter_queryset() to implement custom filtering.
    """
    
    def filter_queryset(
        self,
        request: Request,
        queryset: Any,
        view: ViewSet,
    ) -> Any:
        """
        Filter the queryset based on request parameters.
        
        Returns the filtered queryset.
        """
        raise NotImplementedError()


class FilterSet(BaseFilter):
    """
    Declarative filter set for query parameters.
    
    Usage:
        class ProductFilter(FilterSet):
            class Meta:
                model = Product
                fields = {
                    "category": ["exact", "in"],
                    "price": ["gte", "lte"],
                    "name": ["contains", "icontains"],
                }
        
        class ProductViewSet(ModelViewSet):
            filter_backends = [ProductFilter]
    """
    
    class Meta:
        model: type | None = None
        fields: dict[str, list[str]] = {}
    
    # Lookup mapping
    LOOKUP_MAP = {
        "exact": "exact",
        "iexact": "iexact",
        "contains": "contains",
        "icontains": "icontains",
        "in": "in",
        "gt": "gt",
        "gte": "gte",
        "lt": "lt",
        "lte": "lte",
        "startswith": "startswith",
        "istartswith": "istartswith",
        "endswith": "endswith",
        "iendswith": "iendswith",
        "isnull": "isnull",
    }
    
    def filter_queryset(
        self,
        request: Request,
        queryset: Any,
        view: ViewSet,
    ) -> Any:
        meta = getattr(self, "Meta", None)
        if meta is None:
            return queryset
        
        fields = getattr(meta, "fields", {})
        
        for field_name, lookups in fields.items():
            for lookup in lookups:
                # Build parameter name (e.g., "price__gte")
                if lookup == "exact":
                    param_name = field_name
                else:
                    param_name = f"{field_name}__{lookup}"
                
                # Check if parameter is in request
                value = request.query_params.get(param_name)
                if value is None:
                    # Try alternate format without double underscore
                    alt_param = f"{field_name}_{lookup}"
                    value = request.query_params.get(alt_param)
                
                if value is not None:
                    # Convert value if needed
                    value = self._convert_value(value, lookup)
                    
                    # Apply filter
                    filter_key = f"{field_name}__{lookup}" if lookup != "exact" else field_name
                    queryset = queryset.filter(**{filter_key: value})
        
        return queryset
    
    def _convert_value(self, value: str, lookup: str) -> Any:
        """Convert string value to appropriate type."""
        if lookup == "in":
            return value.split(",")
        if lookup == "isnull":
            return value.lower() in ("true", "1", "yes")
        
        # Try to convert to int/float for comparison lookups
        if lookup in ("gt", "gte", "lt", "lte"):
            try:
                if "." in value:
                    return float(value)
                return int(value)
            except ValueError:
                pass
        
        return value


class SearchFilter(BaseFilter):
    """
    Full-text search filter.
    
    Query param: ?search=term
    
    Usage:
        class ProductViewSet(ModelViewSet):
            filter_backends = [SearchFilter]
            search_fields = ["name", "description"]
    """
    
    search_param: str = "search"
    
    def filter_queryset(
        self,
        request: Request,
        queryset: Any,
        view: ViewSet,
    ) -> Any:
        search_term = request.query_params.get(self.search_param)
        if not search_term:
            return queryset
        
        search_fields = getattr(view, "search_fields", [])
        if not search_fields:
            return queryset
        
        # Build OR query across all search fields
        from zeeb_orm import Q
        
        q_objects = None
        for field in search_fields:
            # Determine lookup based on field prefix
            if field.startswith("^"):
                # Starts with
                lookup = f"{field[1:]}__istartswith"
            elif field.startswith("="):
                # Exact match
                lookup = f"{field[1:]}__iexact"
            elif field.startswith("@"):
                # Full-text search (simplified to icontains)
                lookup = f"{field[1:]}__icontains"
            else:
                # Default: contains
                lookup = f"{field}__icontains"
            
            q = Q(**{lookup: search_term})
            if q_objects is None:
                q_objects = q
            else:
                q_objects = q_objects | q
        
        if q_objects:
            queryset = queryset.filter(q_objects)
        
        return queryset


class OrderingFilter(BaseFilter):
    """
    Ordering filter.
    
    Query param: ?ordering=field,-other_field
    
    Usage:
        class ProductViewSet(ModelViewSet):
            filter_backends = [OrderingFilter]
            ordering_fields = ["name", "price", "created_at"]
            ordering = ["-created_at"]  # Default ordering
    """
    
    ordering_param: str = "ordering"
    
    def filter_queryset(
        self,
        request: Request,
        queryset: Any,
        view: ViewSet,
    ) -> Any:
        ordering = self._get_ordering(request, view, model=getattr(queryset, "model", None))
        
        if ordering:
            queryset = queryset.order_by(*ordering)
        
        return queryset
    
    def _get_ordering(
        self, request: Request, view: ViewSet, model: Any = None
    ) -> list[str] | None:
        # Get ordering from request
        ordering_param = request.query_params.get(self.ordering_param)

        if not ordering_param:
            # The view's own default ordering is developer-written, not client
            # input: it is applied as declared.
            default = getattr(view, "ordering", None)
            if default:
                return list(default) if isinstance(default, (list, tuple)) else [default]
            return None

        fields = [f.strip() for f in ordering_param.split(",") if f.strip()]

        # Validate fields
        allowed_fields = getattr(view, "ordering_fields", None)
        if allowed_fields is None:
            # Default to the serializer's exposed fields (mirrors DRF) so a
            # client cannot order by a column the API does not surface. Only
            # when those cannot be resolved do we fall back to permissive.
            allowed_fields = self._default_valid_fields(view)
            if allowed_fields is None:
                return fields

        # Keep only allowed fields. The whole path is checked, not its root:
        # with "author" allowed, "author__password" must not sort the rows by
        # the related user's password hash. "__all__" admits the model's own
        # fields, never a path through a relation.
        from zeeb_api.query.paths import FieldPathError, check_field_path

        valid_fields = []
        for field in fields:
            try:
                check_field_path(model, field, allowed_fields, allow_regex=False)
            except FieldPathError:
                continue
            valid_fields.append(field)

        return valid_fields if valid_fields else None

    def _default_valid_fields(self, view: ViewSet) -> set[str] | None:
        """Serializer-exposed field names, or None if not resolvable."""
        get_serializer_class = getattr(view, "get_serializer_class", None)
        if get_serializer_class is None:
            return None
        try:
            serializer_class = get_serializer_class()
        except Exception:
            return None
        schema = getattr(serializer_class, "ResponseSchema", None) or getattr(
            serializer_class, "Schema", None
        )
        model_fields = getattr(schema, "model_fields", None)
        if model_fields:
            return set(model_fields.keys())
        return None
