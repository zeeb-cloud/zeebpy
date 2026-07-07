"""ORM exception classes (Django-parity)."""

from __future__ import annotations

from typing import Any

NON_FIELD_ERRORS = "__all__"


class ValidationError(Exception):
    """Raised when data fails validation.

    Accepts a string, a list of messages, or a dict mapping field names
    to messages (each value may itself be a string or list).

    Attributes:
        messages: Flat list of all error messages.
        message_dict: Mapping of field name to list of messages
            (only available when constructed from a dict).
    """

    def __init__(self, message: str | list[Any] | dict[str, Any], code: str | None = None):
        super().__init__(message)
        self.code = code
        self.error_dict: dict[str, list[str]] | None = None
        self.error_list: list[str]

        if isinstance(message, dict):
            self.error_dict = {}
            for field, msgs in message.items():
                if isinstance(msgs, (list, tuple)):
                    self.error_dict[field] = [str(m) for m in msgs]
                else:
                    self.error_dict[field] = [str(msgs)]
            self.error_list = [m for msgs in self.error_dict.values() for m in msgs]
        elif isinstance(message, (list, tuple)):
            self.error_list = [str(m) for m in message]
        else:
            self.error_list = [str(message)]

    @property
    def messages(self) -> list[str]:
        """Flat list of all error messages."""
        return self.error_list

    @property
    def message_dict(self) -> dict[str, list[str]]:
        """Mapping of field name to messages.

        Errors constructed from a plain string or list are reported
        under the ``__all__`` key.
        """
        if self.error_dict is not None:
            return self.error_dict
        return {NON_FIELD_ERRORS: self.error_list}

    def __str__(self) -> str:
        if self.error_dict is not None:
            return str(self.error_dict)
        if len(self.error_list) == 1:
            return self.error_list[0]
        return str(self.error_list)


class NotSupportedError(Exception):
    """Raised when the database backend does not support an operation."""

    pass


class ProtectedError(Exception):
    """Raised when deleting an object protected by an on_delete=PROTECT relation."""

    def __init__(self, msg: str, protected_objects: Any):
        super().__init__(msg, protected_objects)
        self.protected_objects = protected_objects


class RestrictedError(Exception):
    """Raised when deleting an object restricted by an on_delete=RESTRICT relation."""

    def __init__(self, msg: str, restricted_objects: Any = None):
        super().__init__(msg, restricted_objects)
        self.restricted_objects = restricted_objects


class TransactionManagementError(Exception):
    """Raised for invalid transaction usage.

    For example when ``select_for_update()`` is evaluated outside of an
    ``atomic()`` block.
    """

    pass


class FieldDoesNotExist(Exception):
    """Raised when the requested model field does not exist."""

    pass


class FieldError(Exception):
    """Raised for problems with a field lookup or expression
    (e.g. an invalid ``__``-path in filter/order_by/values)."""

    pass


class ModelRegistrationError(Exception):
    """Raised when models from an installed app cannot be registered.

    Surfaced by ``makemigrations``/``migrate`` model loading instead of a
    warning, so schema tooling never runs against an incomplete model state
    (which could otherwise generate destructive migrations).
    """

    pass


# Re-exported for convenience — these are defined in zeeb_orm.models.base
# (imported at the bottom to avoid import cycles at module load time).
from zeeb_orm.models.base import (  # noqa: E402
    DoesNotExist,
    MultipleObjectsReturned,
)

__all__ = [
    "NON_FIELD_ERRORS",
    "ValidationError",
    "NotSupportedError",
    "ProtectedError",
    "RestrictedError",
    "TransactionManagementError",
    "FieldDoesNotExist",
    "FieldError",
    "ModelRegistrationError",
    "DoesNotExist",
    "MultipleObjectsReturned",
]
