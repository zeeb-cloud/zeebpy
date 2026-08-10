"""
Standardized exception classes and error response models.

Provides consistent error response structure for i18n-friendly frontends.
All errors follow the same format with machine-readable codes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    """
    Standard error codes for i18n support.
    
    Frontends should translate these codes to localized messages.
    Format: CATEGORY_SPECIFIC_ERROR
    """

    # Authentication errors
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
    AUTH_TOKEN_MISSING = "AUTH_TOKEN_MISSING"
    AUTH_SESSION_EXPIRED = "AUTH_SESSION_EXPIRED"
    AUTH_INSECURE_CONFIG = "AUTH_INSECURE_CONFIG"

    # OAuth2 / OIDC errors
    AUTH_OAUTH_STATE_INVALID = "AUTH_OAUTH_STATE_INVALID"
    AUTH_OAUTH_EXCHANGE_FAILED = "AUTH_OAUTH_EXCHANGE_FAILED"
    AUTH_OAUTH_ID_TOKEN_INVALID = "AUTH_OAUTH_ID_TOKEN_INVALID"
    AUTH_OAUTH_PROVIDER_NOT_FOUND = "AUTH_OAUTH_PROVIDER_NOT_FOUND"
    AUTH_OAUTH_USER_NOT_PROVISIONED = "AUTH_OAUTH_USER_NOT_PROVISIONED"
    AUTH_OAUTH_EMAIL_UNVERIFIED = "AUTH_OAUTH_EMAIL_UNVERIFIED"

    # Permission errors
    PERM_DENIED = "PERM_DENIED"
    PERM_INSUFFICIENT_ROLE = "PERM_INSUFFICIENT_ROLE"
    PERM_RESOURCE_FORBIDDEN = "PERM_RESOURCE_FORBIDDEN"

    # Validation errors (top-level)
    VALIDATION_ERROR = "VALIDATION_ERROR"
    VALIDATION_FAILED = "VALIDATION_FAILED"

    # Field-specific errors
    FIELD_REQUIRED = "FIELD_REQUIRED"
    FIELD_INVALID_TYPE = "FIELD_INVALID_TYPE"
    FIELD_INVALID_FORMAT = "FIELD_INVALID_FORMAT"
    FIELD_INVALID_VALUE = "FIELD_INVALID_VALUE"
    FIELD_INVALID_CHOICE = "FIELD_INVALID_CHOICE"
    FIELD_TOO_SHORT = "FIELD_TOO_SHORT"
    FIELD_TOO_LONG = "FIELD_TOO_LONG"
    FIELD_MIN_VALUE = "FIELD_MIN_VALUE"
    FIELD_MAX_VALUE = "FIELD_MAX_VALUE"
    FIELD_MIN_LENGTH = "FIELD_MIN_LENGTH"
    FIELD_MAX_LENGTH = "FIELD_MAX_LENGTH"
    FIELD_UNIQUE_CONSTRAINT = "FIELD_UNIQUE_CONSTRAINT"
    FIELD_REGEX_MISMATCH = "FIELD_REGEX_MISMATCH"
    FIELD_INVALID_EMAIL = "FIELD_INVALID_EMAIL"
    FIELD_INVALID_URL = "FIELD_INVALID_URL"
    FIELD_INVALID_UUID = "FIELD_INVALID_UUID"
    FIELD_INVALID_DATE = "FIELD_INVALID_DATE"
    FIELD_INVALID_DATETIME = "FIELD_INVALID_DATETIME"
    FIELD_INVALID_JSON = "FIELD_INVALID_JSON"

    # Serializer errors
    SERIALIZER_INVALID_DATA = "SERIALIZER_INVALID_DATA"
    SERIALIZER_MISSING_FIELDS = "SERIALIZER_MISSING_FIELDS"
    SERIALIZER_READ_ONLY_FIELD = "SERIALIZER_READ_ONLY_FIELD"

    # Resource errors
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RESOURCE_ALREADY_EXISTS = "RESOURCE_ALREADY_EXISTS"
    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
    RESOURCE_GONE = "RESOURCE_GONE"

    # Query/Filter errors
    QUERY_INVALID_FILTER = "QUERY_INVALID_FILTER"
    QUERY_INVALID_FIELD = "QUERY_INVALID_FIELD"
    QUERY_SYNTAX_ERROR = "QUERY_SYNTAX_ERROR"
    QUERY_INVALID_OPERATOR = "QUERY_INVALID_OPERATOR"
    QUERY_INVALID_VALUE = "QUERY_INVALID_VALUE"

    # Rate limiting
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    RATE_LIMIT_QUOTA_EXCEEDED = "RATE_LIMIT_QUOTA_EXCEEDED"

    # API versioning
    API_VERSION_INVALID = "API_VERSION_INVALID"

    # Server errors
    SERVER_ERROR = "SERVER_ERROR"
    SERVER_MAINTENANCE = "SERVER_MAINTENANCE"
    SERVER_UNAVAILABLE = "SERVER_UNAVAILABLE"
    SERVER_TIMEOUT = "SERVER_TIMEOUT"

    # Method errors
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"


class ErrorDetail(BaseModel):
    """
    Individual field-level error detail.
    
    Example:
        {
            "code": "FIELD_REQUIRED",
            "field": "email",
            "message": "This field is required",
            "meta": {"input": null}
        }
    """

    code: str = Field(
        ...,
        description="Error code for this specific field error (e.g., FIELD_REQUIRED)"
    )
    field: str | None = Field(
        None,
        description="Field name that caused the error (null for non-field errors)"
    )
    message: str = Field(
        ...,
        description="Human-readable error message (default English, for debugging)"
    )
    meta: dict[str, Any] | None = Field(
        None,
        description="Additional metadata (e.g., min_length, max_length, expected_format)"
    )


class ErrorMeta(BaseModel):
    """
    Error metadata for debugging and tracking.
    """

    request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique request identifier for debugging"
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 timestamp when error occurred"
    )
    path: str | None = Field(
        None,
        description="Request path that caused the error"
    )
    method: str | None = Field(
        None,
        description="HTTP method of the request"
    )


class ErrorBody(BaseModel):
    """
    Main error object structure.
    """

    code: str = Field(
        ...,
        description="Primary error code (e.g., VALIDATION_ERROR, RESOURCE_NOT_FOUND)"
    )
    message: str = Field(
        ...,
        description="Human-readable error message (default English, for debugging/logging)"
    )
    details: list[ErrorDetail] = Field(
        default_factory=list,
        description="List of specific error details (e.g., field-level validation errors)"
    )
    meta: ErrorMeta = Field(
        default_factory=ErrorMeta,
        description="Error metadata for debugging"
    )


class ErrorResponse(BaseModel):
    """
    Standardized error response structure.
    
    ALL errors in the API use this format for consistency.
    
    Example:
        {
            "success": false,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Validation failed",
                "details": [
                    {
                        "code": "FIELD_REQUIRED",
                        "field": "email",
                        "message": "This field is required"
                    }
                ],
                "meta": {
                    "request_id": "abc-123",
                    "timestamp": "2024-01-15T10:30:00Z"
                }
            }
        }
    """

    success: bool = Field(
        default=False,
        description="Always false for error responses"
    )
    error: ErrorBody = Field(
        ...,
        description="Error details"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "success": False,
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Validation failed",
                        "details": [
                            {
                                "code": "FIELD_REQUIRED",
                                "field": "email",
                                "message": "This field is required"
                            }
                        ],
                        "meta": {
                            "request_id": "550e8400-e29b-41d4-a716-446655440000",
                            "timestamp": "2024-01-15T10:30:00Z"
                        }
                    }
                }
            ]
        }
    }


class ZeebException(Exception):
    """
    Base exception class for all Zeeb API errors.
    
    Provides consistent error structure with i18n-friendly codes.
    
    Usage:
        raise ZeebException(
            code=ErrorCode.VALIDATION_ERROR,
            message="Validation failed",
            details=[
                ErrorDetail(code="FIELD_REQUIRED", field="email", message="Required")
            ],
            status_code=400
        )
    """

    def __init__(
        self,
        code: str | ErrorCode = ErrorCode.SERVER_ERROR,
        message: str = "An error occurred",
        details: list[ErrorDetail] | None = None,
        status_code: int = 500,
        headers: dict[str, str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.code = code.value if isinstance(code, ErrorCode) else code
        self.message = message
        self.details = details or []
        self.status_code = status_code
        self.headers = headers
        self.extra_meta = meta or {}
        # NOTE: Call Exception.__init__ directly (not super()) so that the
        # DRF-compatible subclasses below, which also inherit from
        # fastapi.HTTPException, don't accidentally invoke
        # HTTPException.__init__(message) via the cooperative MRO chain.
        Exception.__init__(self, message)

    def to_response(
        self,
        request_id: str | None = None,
        path: str | None = None,
        method: str | None = None,
    ) -> ErrorResponse:
        """Convert exception to ErrorResponse model."""
        meta = ErrorMeta(
            request_id=request_id or str(uuid.uuid4()),
            path=path,
            method=method,
        )
        # Add any extra meta
        if self.extra_meta:
            meta_dict = meta.model_dump()
            meta_dict.update(self.extra_meta)
            meta = ErrorMeta(**{k: v for k, v in meta_dict.items() if k in ErrorMeta.model_fields})

        return ErrorResponse(
            success=False,
            error=ErrorBody(
                code=self.code,
                message=self.message,
                details=self.details,
                meta=meta,
            )
        )


# Specific exception classes for common error types

class ValidationException(ZeebException):
    """Validation error exception."""

    def __init__(
        self,
        message: str = "Validation failed",
        details: list[ErrorDetail] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.VALIDATION_ERROR,
            message=message,
            details=details,
            status_code=400,
            headers=headers,
        )


class AuthenticationException(ZeebException):
    """Authentication failed exception."""

    def __init__(
        self,
        code: str | ErrorCode = ErrorCode.AUTH_TOKEN_MISSING,
        message: str = "Authentication credentials were not provided",
        details: list[ErrorDetail] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            details=details,
            status_code=401,
            headers=headers,
        )


class PermissionException(ZeebException):
    """Permission denied exception."""

    def __init__(
        self,
        code: str | ErrorCode = ErrorCode.PERM_DENIED,
        message: str = "Permission denied",
        details: list[ErrorDetail] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            details=details,
            status_code=403,
            headers=headers,
        )


class ResourceNotFoundException(ZeebException):
    """Resource not found exception."""

    def __init__(
        self,
        message: str = "Resource not found",
        resource_type: str | None = None,
        resource_id: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        details = []
        meta = {}
        if resource_type:
            meta["resource_type"] = resource_type
        if resource_id is not None:
            meta["resource_id"] = str(resource_id)

        super().__init__(
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message=message,
            details=details,
            status_code=404,
            headers=headers,
            meta=meta if meta else None,
        )


class ResourceConflictException(ZeebException):
    """Resource conflict exception (e.g., duplicate)."""

    def __init__(
        self,
        code: str | ErrorCode = ErrorCode.RESOURCE_CONFLICT,
        message: str = "Resource conflict",
        details: list[ErrorDetail] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            details=details,
            status_code=409,
            headers=headers,
        )


class QueryException(ZeebException):
    """Query/filter parsing exception."""

    def __init__(
        self,
        code: str | ErrorCode = ErrorCode.QUERY_SYNTAX_ERROR,
        message: str = "Invalid query",
        details: list[ErrorDetail] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            details=details,
            status_code=400,
            headers=headers,
        )


class RateLimitException(ZeebException):
    """Rate limit exceeded exception."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        headers = headers or {}
        meta = {}
        if retry_after:
            headers["Retry-After"] = str(retry_after)
            meta["retry_after"] = retry_after

        super().__init__(
            code=ErrorCode.RATE_LIMIT_EXCEEDED,
            message=message,
            status_code=429,
            headers=headers,
            meta=meta if meta else None,
        )


class ServerException(ZeebException):
    """Internal server error exception."""

    def __init__(
        self,
        code: str | ErrorCode = ErrorCode.SERVER_ERROR,
        message: str = "Internal server error",
        details: list[ErrorDetail] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            details=details,
            status_code=500,
            headers=headers,
        )


class MethodNotAllowedException(ZeebException):
    """Method not allowed exception."""

    def __init__(
        self,
        message: str = "Method not allowed",
        allowed_methods: list[str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        headers = headers or {}
        if allowed_methods:
            headers["Allow"] = ", ".join(allowed_methods)

        super().__init__(
            code=ErrorCode.METHOD_NOT_ALLOWED,
            message=message,
            status_code=405,
            headers=headers,
        )


# Mapping of HTTP status codes to standardized error codes.
# Shared by the DRF-compatible exceptions below and the HTTPException handler.
STATUS_CODE_TO_ERROR_CODE: dict[int, str] = {
    400: ErrorCode.VALIDATION_ERROR.value,
    401: ErrorCode.AUTH_TOKEN_MISSING.value,
    403: ErrorCode.PERM_DENIED.value,
    404: ErrorCode.RESOURCE_NOT_FOUND.value,
    405: ErrorCode.METHOD_NOT_ALLOWED.value,
    409: ErrorCode.RESOURCE_CONFLICT.value,
    422: ErrorCode.VALIDATION_ERROR.value,
    429: ErrorCode.RATE_LIMIT_EXCEEDED.value,
    500: ErrorCode.SERVER_ERROR.value,
    503: ErrorCode.SERVER_UNAVAILABLE.value,
}


def details_from_field_errors(detail: Any) -> list[ErrorDetail]:
    """
    Flatten a DRF-style ``{field: [messages]}`` mapping into ErrorDetail list.

    Non-dict details produce an empty list (the message is carried at the
    top level of the error envelope instead).
    """
    details: list[ErrorDetail] = []
    if not isinstance(detail, dict):
        return details

    for field, msgs in detail.items():
        if isinstance(msgs, (list, tuple)):
            for msg in msgs:
                details.append(ErrorDetail(
                    code=ErrorCode.FIELD_INVALID_VALUE.value,
                    field=field,
                    message=str(msg),
                ))
        else:
            details.append(ErrorDetail(
                code=ErrorCode.FIELD_INVALID_VALUE.value,
                field=field,
                message=str(msgs),
            ))
    return details


# DRF-compatible exception classes (canonical home for the names previously
# defined in zeeb_api.response).
#
# These inherit from BOTH the ZeebException hierarchy and
# fastapi.HTTPException:
#
# - With install_exception_handlers() the ZeebException handler wins (it
#   appears before HTTPException in the MRO) and produces the standardized
#   ErrorResponse envelope.
# - Without handlers installed, Starlette's built-in HTTPException handling
#   applies and the original status codes / ``detail`` payloads are preserved.

class APIException(ZeebException, HTTPException):
    """
    Base API exception (DRF-compatible).

    Usage:
        raise APIException(detail="Something went wrong", status_code=400)
    """

    def __init__(
        self,
        detail: str | dict[str, Any] = "An error occurred",
        status_code: int = 500,
        headers: dict[str, str] | None = None,
        *,
        code: str | ErrorCode | None = None,
    ) -> None:
        message = detail if isinstance(detail, str) else "Validation failed"
        # Call ZeebException.__init__ explicitly: the cooperative super()
        # chain would otherwise pass our kwargs to HTTPException.__init__.
        ZeebException.__init__(
            self,
            code=code or STATUS_CODE_TO_ERROR_CODE.get(status_code, ErrorCode.SERVER_ERROR.value),
            message=message,
            details=details_from_field_errors(detail),
            status_code=status_code,
            headers=headers,
        )
        # Preserve the legacy HTTPException attribute (str or dict).
        self.detail = detail


class ValidationError(ValidationException, APIException):
    """
    Validation error exception (DRF-compatible).

    Usage:
        raise ValidationError({"email": ["Invalid email format"]})
    """

    def __init__(
        self,
        detail: str | dict[str, Any] = "Validation error",
        headers: dict[str, str] | None = None,
    ) -> None:
        APIException.__init__(
            self,
            detail=detail,
            status_code=400,
            headers=headers,
            code=ErrorCode.VALIDATION_ERROR,
        )


class NotFound(ResourceNotFoundException, APIException):
    """Object not found exception (DRF-compatible)."""

    def __init__(
        self,
        detail: str = "Not found",
        headers: dict[str, str] | None = None,
    ) -> None:
        APIException.__init__(
            self,
            detail=detail,
            status_code=404,
            headers=headers,
            code=ErrorCode.RESOURCE_NOT_FOUND,
        )


class PermissionDenied(PermissionException, APIException):
    """Permission denied exception (DRF-compatible)."""

    def __init__(
        self,
        detail: str = "Permission denied",
        headers: dict[str, str] | None = None,
    ) -> None:
        APIException.__init__(
            self,
            detail=detail,
            status_code=403,
            headers=headers,
            code=ErrorCode.PERM_DENIED,
        )


class AuthenticationFailed(AuthenticationException, APIException):
    """Authentication failed exception (DRF-compatible)."""

    def __init__(
        self,
        detail: str = "Authentication credentials were not provided",
        headers: dict[str, str] | None = None,
    ) -> None:
        APIException.__init__(
            self,
            detail=detail,
            status_code=401,
            headers=headers,
            code=ErrorCode.AUTH_TOKEN_MISSING,
        )


class MethodNotAllowed(MethodNotAllowedException, APIException):
    """Method not allowed exception (DRF-compatible)."""

    def __init__(
        self,
        detail: str = "Method not allowed",
        headers: dict[str, str] | None = None,
    ) -> None:
        APIException.__init__(
            self,
            detail=detail,
            status_code=405,
            headers=headers,
            code=ErrorCode.METHOD_NOT_ALLOWED,
        )


class Throttled(RateLimitException, APIException):
    """Rate limit exceeded exception (DRF-compatible)."""

    def __init__(
        self,
        detail: str = "Request was throttled",
        wait: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        if wait:
            detail = f"{detail}. Expected available in {wait} seconds."
        APIException.__init__(
            self,
            detail=detail,
            status_code=429,
            headers=headers,
            code=ErrorCode.RATE_LIMIT_EXCEEDED,
        )


# Configuration errors

class ImproperlyConfigured(Exception):
    """The application is somehow improperly configured (Django-style)."""


class InsecureSecretError(ZeebException):
    """Raised when an insecure default secret key is used outside DEBUG mode."""

    def __init__(
        self,
        message: str = (
            "JWT secret key is set to an insecure default value. "
            "Set SECRET_KEY (or JWT_SECRET_KEY) to a strong, unique secret "
            "before running with DEBUG=False."
        ),
    ) -> None:
        super().__init__(
            code=ErrorCode.AUTH_INSECURE_CONFIG,
            message=message,
            status_code=500,
        )


# Helper functions

def field_error(
    field: str,
    code: str | ErrorCode,
    message: str,
    meta: dict[str, Any] | None = None,
) -> ErrorDetail:
    """
    Create a field error detail.
    
    Usage:
        field_error("email", ErrorCode.FIELD_REQUIRED, "This field is required")
    """
    return ErrorDetail(
        code=code.value if isinstance(code, ErrorCode) else code,
        field=field,
        message=message,
        meta=meta,
    )


def validation_error(
    field: str,
    code: str | ErrorCode,
    message: str,
    meta: dict[str, Any] | None = None,
) -> ValidationException:
    """
    Create a validation exception for a single field.
    
    Usage:
        raise validation_error("email", ErrorCode.FIELD_REQUIRED, "Required")
    """
    return ValidationException(
        message=f"Validation failed for field: {field}",
        details=[field_error(field, code, message, meta)],
    )
