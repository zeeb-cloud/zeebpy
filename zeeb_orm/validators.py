"""Django-style field validators.

Validators are callables that take a value and raise
:class:`~zeeb_orm.exceptions.ValidationError` when the value is invalid.
They can be passed to any field via ``validators=[...]`` and are run by
``Field.validate()`` / ``Model.full_clean()`` and on ``save()`` / ``create()``.

Usage::

    from zeeb_orm import fields, validators

    class Product(Model):
        sku = fields.CharField(
            max_length=20,
            validators=[validators.RegexValidator(r"^[A-Z]{3}-\\d+$")],
        )
        stock = fields.IntegerField(validators=[validators.MinValueValidator(0)])
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any

from zeeb_orm.exceptions import ValidationError


class BaseValidator:
    """Base class for limit-based validators."""

    message = "Ensure this value is %(limit_value)s (it is %(show_value)s)."
    code = "limit_value"

    def __init__(self, limit_value: Any, message: str | None = None) -> None:
        self.limit_value = limit_value
        if message is not None:
            self.message = message

    def compare(self, a: Any, b: Any) -> bool:
        """Return True when the value violates the limit."""
        return a is not b

    def clean(self, x: Any) -> Any:
        return x

    def __call__(self, value: Any) -> None:
        cleaned = self.clean(value)
        params = {"limit_value": self.limit_value, "show_value": cleaned, "value": value}
        if self.compare(cleaned, self.limit_value):
            raise ValidationError(self.message % params, code=self.code)

    def __eq__(self, other: Any) -> bool:
        return (
            isinstance(other, self.__class__)
            and self.limit_value == other.limit_value
            and self.message == other.message
            and self.code == other.code
        )


class MinValueValidator(BaseValidator):
    """Validate that the value is >= ``limit_value``."""

    message = "Ensure this value is greater than or equal to %(limit_value)s."
    code = "min_value"

    def compare(self, a: Any, b: Any) -> bool:
        return a < b


class MaxValueValidator(BaseValidator):
    """Validate that the value is <= ``limit_value``."""

    message = "Ensure this value is less than or equal to %(limit_value)s."
    code = "max_value"

    def compare(self, a: Any, b: Any) -> bool:
        return a > b


class MinLengthValidator(BaseValidator):
    """Validate that the value has at least ``limit_value`` items/characters."""

    message = (
        "Ensure this value has at least %(limit_value)d characters "
        "(it has %(show_value)d)."
    )
    code = "min_length"

    def compare(self, a: Any, b: Any) -> bool:
        return a < b

    def clean(self, x: Any) -> int:
        return len(x)


class MaxLengthValidator(BaseValidator):
    """Validate that the value has at most ``limit_value`` items/characters."""

    message = (
        "Ensure this value has at most %(limit_value)d characters "
        "(it has %(show_value)d)."
    )
    code = "max_length"

    def compare(self, a: Any, b: Any) -> bool:
        return a > b

    def clean(self, x: Any) -> int:
        return len(x)


class RegexValidator:
    """Validate the value against a regular expression.

    Args:
        regex: Pattern string or compiled pattern.
        message: Error message on failure.
        code: Error code on failure.
        inverse_match: When True, a *match* is the failure condition.
        flags: ``re`` flags (only when ``regex`` is a string).
    """

    regex: Any = ""
    message = "Enter a valid value."
    code = "invalid"
    inverse_match = False
    flags = 0

    def __init__(
        self,
        regex: str | re.Pattern[str] | None = None,
        message: str | None = None,
        code: str | None = None,
        inverse_match: bool | None = None,
        flags: int = 0,
    ) -> None:
        if regex is not None:
            self.regex = regex
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code
        if inverse_match is not None:
            self.inverse_match = inverse_match
        if flags:
            self.flags = flags
        if isinstance(self.regex, str):
            self.regex = re.compile(self.regex, self.flags)

    def __call__(self, value: Any) -> None:
        regex_matches = self.regex.search(str(value))
        invalid_input = regex_matches if self.inverse_match else not regex_matches
        if invalid_input:
            raise ValidationError(self.message, code=self.code)


class EmailValidator:
    """Validate that the value looks like an email address."""

    message = "Enter a valid email address."
    code = "invalid"
    # Pragmatic pattern: local@domain.tld without whitespace or extra '@'.
    regex = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    def __init__(self, message: str | None = None, code: str | None = None) -> None:
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code

    def __call__(self, value: Any) -> None:
        if not isinstance(value, str) or not self.regex.match(value):
            raise ValidationError(self.message, code=self.code)


class URLValidator(RegexValidator):
    """Validate that the value looks like an http(s)/ftp(s) URL."""

    regex = re.compile(r"^(?:https?|ftps?)://[^\s/$.?#].[^\s]*$", re.IGNORECASE)
    message = "Enter a valid URL."
    code = "invalid"


#: Validator for URL-friendly slugs (letters, numbers, hyphens, underscores).
validate_slug = RegexValidator(
    r"^[-a-zA-Z0-9_]+$",
    message=(
        "Enter a valid slug consisting of letters, numbers, "
        "underscores or hyphens."
    ),
    code="invalid",
)


def validate_ipv4_address(value: Any) -> None:
    """Validate that ``value`` is a valid IPv4 address."""
    try:
        ipaddress.IPv4Address(str(value))
    except ValueError:
        raise ValidationError("Enter a valid IPv4 address.", code="invalid") from None


def validate_ipv6_address(value: Any) -> None:
    """Validate that ``value`` is a valid IPv6 address."""
    try:
        ipaddress.IPv6Address(str(value))
    except ValueError:
        raise ValidationError("Enter a valid IPv6 address.", code="invalid") from None


def validate_ipv46_address(value: Any) -> None:
    """Validate that ``value`` is a valid IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(str(value))
    except ValueError:
        raise ValidationError(
            "Enter a valid IPv4 or IPv6 address.", code="invalid"
        ) from None


__all__ = [
    "BaseValidator",
    "MinValueValidator",
    "MaxValueValidator",
    "MinLengthValidator",
    "MaxLengthValidator",
    "RegexValidator",
    "EmailValidator",
    "URLValidator",
    "validate_slug",
    "validate_ipv4_address",
    "validate_ipv6_address",
    "validate_ipv46_address",
]
