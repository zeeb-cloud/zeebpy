"""
Response utilities.

.. deprecated::
    The exception classes that used to live here (``APIException``,
    ``ValidationError``, ``NotFound``, ``PermissionDenied``,
    ``AuthenticationFailed``, ``MethodNotAllowed``, ``Throttled``) have moved
    to :mod:`zeeb_api.exceptions`, which is now the canonical location.
    Importing them from ``zeeb_api.response`` still works but emits a
    ``DeprecationWarning``.
"""

import warnings
from typing import Any

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


# Backward-compatible re-exports of the exception classes that moved to
# zeeb_api.exceptions. Resolved lazily so a DeprecationWarning is emitted
# at the import site.
_MOVED_EXCEPTIONS = frozenset({
    "APIException",
    "ValidationError",
    "NotFound",
    "PermissionDenied",
    "AuthenticationFailed",
    "MethodNotAllowed",
    "Throttled",
})


def __getattr__(name: str) -> Any:
    if name in _MOVED_EXCEPTIONS:
        warnings.warn(
            f"Importing {name} from zeeb_api.response is deprecated; "
            f"import it from zeeb_api.exceptions instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        from zeeb_api import exceptions

        return getattr(exceptions, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
