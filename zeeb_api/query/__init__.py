"""
Query module - Unified query request/response handling.

Provides:
- QueryRequest: Pydantic model for query parameters (filter, order_by, pagination)
- QueryResponse: Generic response model with results and pagination info
- parse_q_filter: Safe AST parser for Q filter expressions
"""

from zeeb_api.query.request import QueryRequest, QueryResponse, create_query_response_model
from zeeb_api.query.parser import parse_q_filter, QFilterError

__all__ = [
    "QueryRequest",
    "QueryResponse",
    "create_query_response_model",
    "parse_q_filter",
    "QFilterError",
]
