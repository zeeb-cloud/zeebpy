"""
Query module - Unified query request/response handling.

Provides:
- QueryRequest: Pydantic model for query parameters (filter, order_by, pagination)
- QueryResponse: Generic response model with results and pagination info
- parse_q_filter: Safe AST parser for Q filter expressions
"""

from zeeb_api.query.request import QueryRequest, QueryResponse, create_query_response_model
from zeeb_api.query.parser import parse_q_filter, extract_q_fields, extract_q_paths, QFilterError
from zeeb_api.query.paths import FieldPathError, check_field_path

__all__ = [
    "QueryRequest",
    "QueryResponse",
    "create_query_response_model",
    "parse_q_filter",
    "extract_q_fields",
    "extract_q_paths",
    "QFilterError",
    "check_field_path",
    "FieldPathError",
]
