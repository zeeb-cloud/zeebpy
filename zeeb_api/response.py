"""Response utilities and exceptions."""

from typing import Any
from fastapi import HTTPException
from fastapi.responses import JSONResponse


class Response(JSONResponse):
    """
    DRF-style Response wrapper.
    
    Usage:
        return Response({"id": 1, "name": "Alice"})
        return Response({"id": 1}, status_code=201)
    """
    
    def __init__(
        self,
        data: Any = None,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(content=data, status_code=status_code, headers=headers, **kwargs)


class APIException(HTTPException):
    """
    Base API exception.
    
    Usage:
        raise APIException(detail="Something went wrong", status_code=400)
    """
    
    def __init__(
        self,
        detail: str | dict[str, Any] = "An error occurred",
        status_code: int = 500,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)


class ValidationError(APIException):
    """
    Validation error exception.
    
    Usage:
        raise ValidationError({"email": ["Invalid email format"]})
    """
    
    def __init__(
        self,
        detail: str | dict[str, Any] = "Validation error",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail=detail, status_code=400, headers=headers)


class NotFound(APIException):
    """Object not found exception."""
    
    def __init__(
        self,
        detail: str = "Not found",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail=detail, status_code=404, headers=headers)


class PermissionDenied(APIException):
    """Permission denied exception."""
    
    def __init__(
        self,
        detail: str = "Permission denied",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail=detail, status_code=403, headers=headers)


class AuthenticationFailed(APIException):
    """Authentication failed exception."""
    
    def __init__(
        self,
        detail: str = "Authentication credentials were not provided",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail=detail, status_code=401, headers=headers)


class MethodNotAllowed(APIException):
    """Method not allowed exception."""
    
    def __init__(
        self,
        detail: str = "Method not allowed",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail=detail, status_code=405, headers=headers)


class Throttled(APIException):
    """Rate limit exceeded exception."""
    
    def __init__(
        self,
        detail: str = "Request was throttled",
        wait: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        if wait:
            detail = f"{detail}. Expected available in {wait} seconds."
        super().__init__(detail=detail, status_code=429, headers=headers)
