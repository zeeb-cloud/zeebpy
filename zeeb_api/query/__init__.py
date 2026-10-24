"""
Query module - Unified query request/response handling.

Provides:
- QueryRequest: Pydantic model for query parameters (filter, order_by, pagination)
- QueryResponse: Generic response model with results and pagination info
- parse_q_filter: Safe AST parser for Q filter expressions
"""

from zeeb_api.query.request import (
    QueryRequest,
    QueryResponse,
    create_query_response_model,
    query_request_model,
)
from zeeb_api.query.parser import (
    parse_q_filter,
    parse_q_filter_with_paths,
    extract_q_fields,
    extract_q_paths,
    QFilterError,
)
from zeeb_api.query.paths import (
    FieldPathError,
    check_field_path,
    rename_q_paths,
    serializer_field_paths,
)

__all__ = [
    "QueryRequest",
    "QueryResponse",
    "create_query_response_model",
    "query_request_model",
    "parse_q_filter",
    "parse_q_filter_with_paths",
    "extract_q_fields",
    "extract_q_paths",
    "QFilterError",
    "check_field_path",
    "FieldPathError",
    "serializer_field_paths",
    "rename_q_paths",
]
