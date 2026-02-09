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
        ordering = self._get_ordering(request, view)
        
        if ordering:
            queryset = queryset.order_by(*ordering)
        
        return queryset
    
    def _get_ordering(self, request: Request, view: ViewSet) -> list[str] | None:
        # Get ordering from request
        ordering_param = request.query_params.get(self.ordering_param)
        
        if ordering_param:
            fields = [f.strip() for f in ordering_param.split(",")]
        else:
            # Use default ordering
            default = getattr(view, "ordering", None)
            if default:
                fields = list(default) if isinstance(default, (list, tuple)) else [default]
            else:
                return None
        
        # Validate fields
        allowed_fields = getattr(view, "ordering_fields", None)
        if allowed_fields is None:
            # Allow all fields by default
            return fields
        
        if allowed_fields == "__all__":
            return fields
        
        # Filter to only allowed fields
        valid_fields = []
        for field in fields:
            field_name = field.lstrip("-")
            if field_name in allowed_fields:
                valid_fields.append(field)
        
        return valid_fields if valid_fields else None
