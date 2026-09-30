"""
Query request and response models.

Provides Pydantic models for unified query interface:
- QueryRequest: Input model with filter, order_by, limit/offset pagination
- QueryResponse: Output model with results and pagination info
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")


class QueryRequest(BaseModel):
    """
    Unified query request for list/filter operations.
    
    Example:
        {
            "filter": "Q(title__icontains='ring') | Q(author_id=1)",
            "order_by": ["-published_year", "title"],
            "limit": 20,
            "offset": 0
        }
    """
    
    filter: str | None = Field(
        default=None,
        description="Q filter expression, e.g. \"Q(name__icontains='john') | Q(active=True)\""
    )
    order_by: list[str] | None = Field(
        default=None,
        description="Fields to order by, e.g. [\"-created_at\", \"name\"]"
    )
    limit: int = Field(
        default=20,
        ge=1,
        description="Maximum number of items to return (capped at settings.MAX_LIMIT)"
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of items to skip"
    )
    
    @field_validator("order_by", mode="before")
    @classmethod
    def validate_order_by(cls, v: Any) -> list[str] | None:
        """Accept string or list for order_by."""
        if v is None:
            return None
        if isinstance(v, str):
            return [v]
        return v


_query_request_models: dict[tuple[int, int], type[QueryRequest]] = {}


def query_request_model() -> type[QueryRequest]:
    """``QueryRequest`` bounded by the ``DEFAULT_LIMIT``/``MAX_LIMIT`` settings.

    The router types ``POST /query`` bodies with this, so ``limit`` defaults to
    ``DEFAULT_LIMIT`` and a value above ``MAX_LIMIT`` is a 422 that OpenAPI
    documents. (``QueryRequest`` itself used to hard-code ``le=100``, so a
    larger ``MAX_LIMIT`` never took effect.)
    """
    from pydantic import create_model

    from zeeb_api.conf import settings

    default_limit = int(getattr(settings, "DEFAULT_LIMIT", 20))
    max_limit = int(getattr(settings, "MAX_LIMIT", 100))
    key = (default_limit, max_limit)
    model = _query_request_models.get(key)
    if model is None:
        model = create_model(
            "QueryRequest",
            __base__=QueryRequest,
            limit=(
                int,
                Field(
                    default=min(default_limit, max_limit),
                    ge=1,
                    le=max_limit,
                    description=f"Maximum number of items to return (max {max_limit})",
                ),
            ),
        )
        _query_request_models[key] = model
    return model


class QueryResponse(BaseModel, Generic[T]):
    """
    Unified query response with pagination info.
    
    Example:
        {
            "count": 100,
            "limit": 20,
            "offset": 0,
            "results": [...]
        }
    """
    
    count: int = Field(description="Total number of items matching the query")
    limit: int = Field(description="Maximum number of items returned")
    offset: int = Field(description="Number of items skipped")
    results: list[T] = Field(description="List of items")


def create_query_response_model(
    item_schema: type[BaseModel],
    name: str | None = None,
) -> type[BaseModel]:
    """
    Create a QueryResponse model with a specific item type.
    
    Args:
        item_schema: Pydantic model for individual items
        name: Optional name for the response model
    
    Returns:
        Pydantic model class for the query response
    
    Example:
        >>> BookQueryResponse = create_query_response_model(BookResponse)
        >>> # BookQueryResponse has results: list[BookResponse]
    """
    schema_name = name or f"{item_schema.__name__}QueryResponse"
    
    return type(
        schema_name,
        (BaseModel,),
        {
            "__annotations__": {
                "count": int,
                "limit": int,
                "offset": int,
                "results": list[item_schema],
            },
            "count": Field(description="Total number of items matching the query"),
            "limit": Field(description="Maximum number of items returned"),
            "offset": Field(description="Number of items skipped"),
            "results": Field(description="List of items"),
        },
    )
