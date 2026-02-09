"""Base pagination classes."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING
from urllib.parse import urlencode, urlparse, parse_qs
import base64

from fastapi import Request

if TYPE_CHECKING:
    pass


class BasePagination:
    """
    Base pagination class.
    
    Override paginate_queryset() to implement custom pagination.
    """
    
    async def paginate_queryset(
        self,
        queryset: Any,
        request: Request,
    ) -> tuple[list[Any], dict[str, Any]]:
        """
        Paginate a queryset.
        
        Returns:
            tuple of (items, page_info)
            - items: List of paginated results
            - page_info: Dict with count, next, previous, etc.
        """
        raise NotImplementedError()
    
    def _get_url(self, request: Request, **params: Any) -> str:
        """Build URL with query parameters."""
        url = str(request.url)
        parsed = urlparse(url)
        
        # Merge existing params with new ones
        existing = parse_qs(parsed.query)
        for key, value in params.items():
            if value is not None:
                existing[key] = [str(value)]
            elif key in existing:
                del existing[key]
        
        # Rebuild query string
        query = urlencode({k: v[0] for k, v in existing.items()})
        
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{query}" if query else f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


class PageNumberPagination(BasePagination):
    """
    Page number pagination.
    
    Query params:
    - page: Page number (1-indexed)
    - page_size: Number of items per page (optional)
    
    Usage:
        class MyViewSet(ModelViewSet):
            pagination_class = PageNumberPagination
    
    Configure:
        class MyPagination(PageNumberPagination):
            page_size = 20
            page_size_query_param = "page_size"
            max_page_size = 100
    """
    
    page_size: int = 20
    page_query_param: str = "page"
    page_size_query_param: str | None = "page_size"
    max_page_size: int = 100
    
    async def paginate_queryset(
        self,
        queryset: Any,
        request: Request,
    ) -> tuple[list[Any], dict[str, Any]]:
        # Get total count
        count = await queryset.count()
        
        # Get page number
        page = self._get_page_number(request)
        page_size = self._get_page_size(request)
        
        # Calculate offset
        offset = (page - 1) * page_size
        
        # Get items
        items = await queryset.limit(page_size).offset(offset)
        
        # Calculate pagination info
        total_pages = (count + page_size - 1) // page_size if page_size > 0 else 1
        
        next_url = None
        if page < total_pages:
            next_url = self._get_url(request, **{self.page_query_param: page + 1})
        
        previous_url = None
        if page > 1:
            previous_url = self._get_url(request, **{self.page_query_param: page - 1})
        
        return items, {
            "count": count,
            "next": next_url,
            "previous": previous_url,
            "page": page,
            "total_pages": total_pages,
            "page_size": page_size,
        }
    
    def _get_page_number(self, request: Request) -> int:
        try:
            page = int(request.query_params.get(self.page_query_param, 1))
            return max(1, page)
        except (ValueError, TypeError):
            return 1
    
    def _get_page_size(self, request: Request) -> int:
        if self.page_size_query_param:
            try:
                size = int(request.query_params.get(self.page_size_query_param, self.page_size))
                return min(max(1, size), self.max_page_size)
            except (ValueError, TypeError):
                pass
        return self.page_size


class LimitOffsetPagination(BasePagination):
    """
    Limit/offset pagination.
    
    Query params:
    - limit: Number of items to return
    - offset: Starting position
    
    Usage:
        class MyViewSet(ModelViewSet):
            pagination_class = LimitOffsetPagination
    
    Configure:
        class MyPagination(LimitOffsetPagination):
            default_limit = 20
            max_limit = 100
    """
    
    default_limit: int = 20
    limit_query_param: str = "limit"
    offset_query_param: str = "offset"
    max_limit: int = 100
    
    async def paginate_queryset(
        self,
        queryset: Any,
        request: Request,
    ) -> tuple[list[Any], dict[str, Any]]:
        # Get total count
        count = await queryset.count()
        
        # Get limit and offset
        limit = self._get_limit(request)
        offset = self._get_offset(request)
        
        # Get items
        items = await queryset.limit(limit).offset(offset)
        
        # Calculate next/previous URLs
        next_url = None
        if offset + limit < count:
            next_url = self._get_url(
                request,
                **{self.limit_query_param: limit, self.offset_query_param: offset + limit}
            )
        
        previous_url = None
        if offset > 0:
            prev_offset = max(0, offset - limit)
            previous_url = self._get_url(
                request,
                **{self.limit_query_param: limit, self.offset_query_param: prev_offset}
            )
        
        return items, {
            "count": count,
            "next": next_url,
            "previous": previous_url,
            "limit": limit,
            "offset": offset,
        }
    
    def _get_limit(self, request: Request) -> int:
        try:
            limit = int(request.query_params.get(self.limit_query_param, self.default_limit))
            return min(max(1, limit), self.max_limit)
        except (ValueError, TypeError):
            return self.default_limit
    
    def _get_offset(self, request: Request) -> int:
        try:
            offset = int(request.query_params.get(self.offset_query_param, 0))
            return max(0, offset)
        except (ValueError, TypeError):
            return 0


class CursorPagination(BasePagination):
    """
    Cursor-based pagination for large datasets.
    
    More efficient than offset pagination for large datasets because
    it doesn't require counting all records.
    
    Query params:
    - cursor: Encoded cursor position
    - page_size: Number of items per page (optional)
    
    Usage:
        class MyViewSet(ModelViewSet):
            pagination_class = CursorPagination
    
    Configure:
        class MyPagination(CursorPagination):
            page_size = 20
            ordering = "-created_at"  # Required for cursor pagination
    """
    
    page_size: int = 20
    cursor_query_param: str = "cursor"
    page_size_query_param: str | None = "page_size"
    max_page_size: int = 100
    ordering: str = "-id"  # Field to order by (required)
    
    async def paginate_queryset(
        self,
        queryset: Any,
        request: Request,
    ) -> tuple[list[Any], dict[str, Any]]:
        page_size = self._get_page_size(request)
        cursor = self._get_cursor(request)
        
        # Determine ordering
        reverse = self.ordering.startswith("-")
        order_field = self.ordering.lstrip("-")
        
        # Apply cursor filter
        if cursor:
            cursor_value = self._decode_cursor(cursor)
            if cursor_value:
                if reverse:
                    queryset = queryset.filter(**{f"{order_field}__lt": cursor_value})
                else:
                    queryset = queryset.filter(**{f"{order_field}__gt": cursor_value})
        
        # Apply ordering
        queryset = queryset.order_by(self.ordering)
        
        # Fetch one extra to determine if there's a next page
        items = await queryset.limit(page_size + 1)
        
        has_next = len(items) > page_size
        if has_next:
            items = items[:page_size]
        
        # Build next cursor
        next_url = None
        if has_next and items:
            last_item = items[-1]
            next_cursor = self._encode_cursor(getattr(last_item, order_field))
            next_url = self._get_url(request, **{self.cursor_query_param: next_cursor})
        
        # Note: Previous cursor is more complex to implement
        # For simplicity, we don't implement it here
        
        return items, {
            "next": next_url,
            "previous": None,  # Complex to implement properly
        }
    
    def _get_page_size(self, request: Request) -> int:
        if self.page_size_query_param:
            try:
                size = int(request.query_params.get(self.page_size_query_param, self.page_size))
                return min(max(1, size), self.max_page_size)
            except (ValueError, TypeError):
                pass
        return self.page_size
    
    def _get_cursor(self, request: Request) -> str | None:
        return request.query_params.get(self.cursor_query_param)
    
    def _encode_cursor(self, value: Any) -> str:
        """Encode cursor value to string."""
        return base64.urlsafe_b64encode(str(value).encode()).decode()
    
    def _decode_cursor(self, cursor: str) -> Any:
        """Decode cursor string to value."""
        try:
            return base64.urlsafe_b64decode(cursor.encode()).decode()
        except Exception:
            return None
