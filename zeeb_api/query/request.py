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
        le=100,
        description="Maximum number of items to return (max 100)"
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
