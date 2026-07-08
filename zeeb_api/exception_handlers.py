"""
Exception handlers for FastAPI integration.

Converts all exceptions to standardized ErrorResponse format.
"""

from __future__ import annotations

import logging
import traceback
import uuid
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from zeeb_api.exceptions import (
    STATUS_CODE_TO_ERROR_CODE,
    ErrorBody,
    ErrorCode,
    ErrorDetail,
    ErrorMeta,
    ErrorResponse,
    ZeebException,
    details_from_field_errors,
)

logger = logging.getLogger(__name__)


def _get_request_id(request: Request) -> str:
    """Get or generate request ID."""
    # Check common headers for request ID
    for header in ("X-Request-ID", "X-Correlation-ID", "Request-ID"):
        if request_id := request.headers.get(header):
            return request_id
    return str(uuid.uuid4())


def _create_error_response(
    code: str,
    message: str,
    status_code: int,
    request: Request,
    details: list[ErrorDetail] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Create standardized error JSONResponse."""
    error_response = ErrorResponse(
        success=False,
        error=ErrorBody(
            code=code,
            message=message,
            details=details or [],
            meta=ErrorMeta(
                request_id=_get_request_id(request),
                path=str(request.url.path),
                method=request.method,
            ),
        ),
    )
    
    return JSONResponse(
        status_code=status_code,
        content=error_response.model_dump(),
        headers=headers,
    )


# Pydantic error type to ErrorCode mapping
PYDANTIC_ERROR_CODE_MAP: dict[str, str] = {
    # Missing/required
    "missing": ErrorCode.FIELD_REQUIRED.value,
    "value_error.missing": ErrorCode.FIELD_REQUIRED.value,
    
    # Type errors
    "type_error": ErrorCode.FIELD_INVALID_TYPE.value,
    "type_error.integer": ErrorCode.FIELD_INVALID_TYPE.value,
    "type_error.float": ErrorCode.FIELD_INVALID_TYPE.value,
    "type_error.string": ErrorCode.FIELD_INVALID_TYPE.value,
    "type_error.bool": ErrorCode.FIELD_INVALID_TYPE.value,
    "type_error.list": ErrorCode.FIELD_INVALID_TYPE.value,
    "type_error.dict": ErrorCode.FIELD_INVALID_TYPE.value,
    "int_parsing": ErrorCode.FIELD_INVALID_TYPE.value,
    "float_parsing": ErrorCode.FIELD_INVALID_TYPE.value,
    "bool_parsing": ErrorCode.FIELD_INVALID_TYPE.value,
    "string_type": ErrorCode.FIELD_INVALID_TYPE.value,
    "int_type": ErrorCode.FIELD_INVALID_TYPE.value,
    "float_type": ErrorCode.FIELD_INVALID_TYPE.value,
    "bool_type": ErrorCode.FIELD_INVALID_TYPE.value,
    "list_type": ErrorCode.FIELD_INVALID_TYPE.value,
    "dict_type": ErrorCode.FIELD_INVALID_TYPE.value,
    
    # String constraints
    "string_too_short": ErrorCode.FIELD_TOO_SHORT.value,
    "string_too_long": ErrorCode.FIELD_TOO_LONG.value,
    "value_error.any_str.min_length": ErrorCode.FIELD_MIN_LENGTH.value,
    "value_error.any_str.max_length": ErrorCode.FIELD_MAX_LENGTH.value,
    "string_pattern_mismatch": ErrorCode.FIELD_REGEX_MISMATCH.value,
    
    # Numeric constraints
    "greater_than": ErrorCode.FIELD_MIN_VALUE.value,
    "greater_than_equal": ErrorCode.FIELD_MIN_VALUE.value,
    "less_than": ErrorCode.FIELD_MAX_VALUE.value,
    "less_than_equal": ErrorCode.FIELD_MAX_VALUE.value,
    "value_error.number.not_gt": ErrorCode.FIELD_MIN_VALUE.value,
    "value_error.number.not_ge": ErrorCode.FIELD_MIN_VALUE.value,
    "value_error.number.not_lt": ErrorCode.FIELD_MAX_VALUE.value,
    "value_error.number.not_le": ErrorCode.FIELD_MAX_VALUE.value,
    
    # Format errors
    "value_error.email": ErrorCode.FIELD_INVALID_EMAIL.value,
    "value_error.url": ErrorCode.FIELD_INVALID_URL.value,
    "value_error.uuid": ErrorCode.FIELD_INVALID_UUID.value,
    "uuid_parsing": ErrorCode.FIELD_INVALID_UUID.value,
    "url_parsing": ErrorCode.FIELD_INVALID_URL.value,
    "date_parsing": ErrorCode.FIELD_INVALID_DATE.value,
    "datetime_parsing": ErrorCode.FIELD_INVALID_DATETIME.value,
    "time_parsing": ErrorCode.FIELD_INVALID_FORMAT.value,
    "json_invalid": ErrorCode.FIELD_INVALID_JSON.value,
    
    # Enum/choices
    "enum": ErrorCode.FIELD_INVALID_CHOICE.value,
    "literal_error": ErrorCode.FIELD_INVALID_CHOICE.value,
    
    # General value errors
    "value_error": ErrorCode.FIELD_INVALID_VALUE.value,
}


def _pydantic_error_to_code(error_type: str) -> str:
    """Map Pydantic error type to ErrorCode."""
    # Direct match
    if error_type in PYDANTIC_ERROR_CODE_MAP:
        return PYDANTIC_ERROR_CODE_MAP[error_type]
    
    # Prefix match
    for prefix, code in PYDANTIC_ERROR_CODE_MAP.items():
        if error_type.startswith(prefix):
            return code
    
    # Default
    return ErrorCode.FIELD_INVALID_VALUE.value


def _parse_pydantic_errors(errors: list[dict[str, Any]]) -> list[ErrorDetail]:
    """Convert Pydantic validation errors to ErrorDetails."""
    details = []
    
    for error in errors:
        # Get field path (e.g., ["body", "email"] -> "email")
        loc = error.get("loc", [])
        
        # Skip "body" prefix from FastAPI
        if loc and loc[0] in ("body", "query", "path", "header", "cookie"):
            loc = loc[1:]
        
        field = ".".join(str(x) for x in loc) if loc else None
        
        # Map error type to code
        error_type = error.get("type", "value_error")
        code = _pydantic_error_to_code(error_type)
        
        # Build meta with context
        meta: dict[str, Any] = {}
        ctx = error.get("ctx", {})
        if ctx:
            # Include relevant context (limits, expected values, etc.)
            for key in ("min_length", "max_length", "gt", "ge", "lt", "le", 
                       "expected", "pattern", "limit_value"):
                if key in ctx:
                    meta[key] = ctx[key]
        
        # Include input value if present (for debugging)
        if "input" in error:
            input_val = error["input"]
            # Truncate long values
            if isinstance(input_val, str) and len(input_val) > 100:
                input_val = input_val[:100] + "..."
            meta["input"] = input_val
        
        details.append(ErrorDetail(
            code=code,
            field=field,
            message=error.get("msg", "Invalid value"),
            meta=meta if meta else None,
        ))
    
    return details


async def zeeb_exception_handler(
    request: Request,
    exc: ZeebException,
) -> JSONResponse:
    """Handle ZeebException and subclasses."""
    response = exc.to_response(
        request_id=_get_request_id(request),
        path=str(request.url.path),
        method=request.method,
    )
    
    return JSONResponse(
        status_code=exc.status_code,
        content=response.model_dump(),
        headers=exc.headers,
    )


async def pydantic_validation_exception_handler(
    request: Request,
    exc: PydanticValidationError,
) -> JSONResponse:
    """Handle Pydantic ValidationError."""
    details = _parse_pydantic_errors(exc.errors())
    
    return _create_error_response(
        code=ErrorCode.VALIDATION_ERROR.value,
        message="Validation failed",
        status_code=422,
        request=request,
        details=details,
    )


async def request_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Handle FastAPI RequestValidationError (path, query, body validation)."""
    details = _parse_pydantic_errors(exc.errors())
    
    return _create_error_response(
        code=ErrorCode.VALIDATION_ERROR.value,
        message="Request validation failed",
        status_code=422,
        request=request,
        details=details,
    )


async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    """Handle Starlette/FastAPI HTTPException."""
    code = STATUS_CODE_TO_ERROR_CODE.get(exc.status_code, ErrorCode.SERVER_ERROR.value)
    message = exc.detail if isinstance(exc.detail, str) else "An error occurred"

    # Parse detail if it's a dict with field errors
    details = details_from_field_errors(exc.detail)
    if isinstance(exc.detail, dict):
        message = "Validation failed"

    headers = getattr(exc, "headers", None)
    
    return _create_error_response(
        code=code,
        message=message,
        status_code=exc.status_code,
        request=request,
        details=details,
        headers=headers,
    )


async def generic_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Handle all unhandled exceptions."""
    # Log the full traceback for debugging
    logger.error(
        f"Unhandled exception: {type(exc).__name__}: {exc}\n"
        f"Path: {request.url.path}\n"
        f"Method: {request.method}\n"
        f"Traceback:\n{traceback.format_exc()}"
    )
    
    return _create_error_response(
        code=ErrorCode.SERVER_ERROR.value,
        message="Internal server error",
        status_code=500,
        request=request,
    )


# ORM Exception handlers

def create_orm_exception_handlers() -> dict[type[Exception], Callable]:
    """
    Create handlers for ORM exceptions if zeeb_orm is available.
    
    Returns dict mapping exception types to handler functions.
    """
    handlers = {}
    
    try:
        from zeeb_orm import DoesNotExist, MultipleObjectsReturned
        
        async def does_not_exist_handler(
            request: Request,
            exc: DoesNotExist,
        ) -> JSONResponse:
            """Handle ORM DoesNotExist exception."""
            return _create_error_response(
                code=ErrorCode.RESOURCE_NOT_FOUND.value,
                message=str(exc) or "Resource not found",
                status_code=404,
                request=request,
            )
        
        async def multiple_objects_handler(
            request: Request,
            exc: MultipleObjectsReturned,
        ) -> JSONResponse:
            """Handle ORM MultipleObjectsReturned exception."""
            return _create_error_response(
                code=ErrorCode.RESOURCE_CONFLICT.value,
                message=str(exc) or "Multiple objects returned",
                status_code=409,
                request=request,
            )
        
        handlers[DoesNotExist] = does_not_exist_handler
        handlers[MultipleObjectsReturned] = multiple_objects_handler

    except ImportError:
        pass

    try:
        from zeeb_orm.exceptions import ValidationError as ORMValidationError

        async def orm_validation_handler(
            request: Request,
            exc: ORMValidationError,
        ) -> JSONResponse:
            """Handle ORM model validation errors (full_clean) as 400s.

            Without this handler an ORM ValidationError raised inside
            create/update falls through to the generic 500 handler even
            though it is an ordinary client error.
            """
            details = [
                ErrorDetail(
                    code=ErrorCode.FIELD_INVALID_VALUE.value,
                    field=None if field == "__all__" else field,
                    message=message,
                )
                for field, messages in exc.message_dict.items()
                for message in messages
            ]
            return _create_error_response(
                code=ErrorCode.VALIDATION_ERROR.value,
                message="Validation failed",
                status_code=400,
                request=request,
                details=details,
            )

        handlers[ORMValidationError] = orm_validation_handler

    except ImportError:
        pass

    return handlers


def install_exception_handlers(app: FastAPI) -> None:
    """
    Install all exception handlers on a FastAPI app.
    
    Usage:
        from fastapi import FastAPI
        from zeeb_api.exception_handlers import install_exception_handlers
        
        app = FastAPI()
        install_exception_handlers(app)
    """
    # Zeeb exceptions
    app.add_exception_handler(ZeebException, zeeb_exception_handler)
    
    # Pydantic/FastAPI validation
    app.add_exception_handler(PydanticValidationError, pydantic_validation_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
    
    # HTTP exceptions
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    
    # ORM exceptions
    for exc_type, handler in create_orm_exception_handlers().items():
        app.add_exception_handler(exc_type, handler)
    
    # Catch-all (must be last)
    app.add_exception_handler(Exception, generic_exception_handler)


# HTTP status codes that return the standardized ErrorResponse envelope.
# Used to rewrite the generated OpenAPI so the documented schema matches the
# runtime body produced by the exception handlers above.
_ERROR_STATUS_CODES = frozenset({"400", "401", "403", "404", "409", "422", "429", "500"})


def install_error_response_schema(app: FastAPI) -> None:
    """
    Override ``app.openapi`` so error responses document the ErrorResponse envelope.

    The runtime handlers installed by :func:`install_exception_handlers` return the
    standardized ``ErrorResponse`` body for 4xx/5xx errors, but FastAPI still
    auto-generates a ``HTTPValidationError`` schema for 422 responses. This makes
    the published ``openapi.json`` advertise a shape the server never returns.

    This function patches the OpenAPI generation to:
    - inject ``ErrorResponse`` (and its nested models) into ``components.schemas``;
    - point every error-status response (400/401/403/404/409/422/429/500) at
      ``#/components/schemas/ErrorResponse``;
    - drop the now-unreferenced ``HTTPValidationError`` / ``ValidationError`` schemas.

    Idempotent and lazy: the schema is built (and cached on ``app.openapi_schema``)
    the first time ``openapi.json`` is requested, after all routes are registered.

    Usage:
        install_exception_handlers(app)
        install_error_response_schema(app)
    """
    from fastapi.openapi.utils import get_openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version=app.openapi_version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
            servers=app.servers,
        )

        # Inject ErrorResponse and its nested models into components.schemas.
        error_schema = ErrorResponse.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        nested_defs = error_schema.pop("$defs", {})
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        components["ErrorResponse"] = error_schema
        for name, definition in nested_defs.items():
            components.setdefault(name, definition)

        # Point every error-status response at ErrorResponse.
        error_ref = {"$ref": "#/components/schemas/ErrorResponse"}
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                for status_code, response in operation.get("responses", {}).items():
                    if str(status_code) not in _ERROR_STATUS_CODES:
                        continue
                    for media_type in response.get("content", {}).values():
                        media_type["schema"] = error_ref

        # Drop the default validation schemas now that nothing references them.
        components.pop("HTTPValidationError", None)
        components.pop("ValidationError", None)

        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi


def get_error_responses() -> dict[int | str, dict[str, Any]]:
    """
    Get OpenAPI response schemas for common error codes.

    Usage in router:
        @app.get("/items/{id}", responses=get_error_responses())
        async def get_item(id: int): ...
    """
    return {
        400: {
            "model": ErrorResponse,
            "description": "Bad Request - Validation error",
        },
        401: {
            "model": ErrorResponse,
            "description": "Unauthorized - Authentication required",
        },
        403: {
            "model": ErrorResponse,
            "description": "Forbidden - Permission denied",
        },
        404: {
            "model": ErrorResponse,
            "description": "Not Found - Resource not found",
        },
        422: {
            "model": ErrorResponse,
            "description": "Unprocessable Entity - Validation error",
        },
        429: {
            "model": ErrorResponse,
            "description": "Too Many Requests - Rate limit exceeded",
        },
        500: {
            "model": ErrorResponse,
            "description": "Internal Server Error",
        },
    }
