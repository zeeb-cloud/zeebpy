"""Serializer fields for validation and serialization."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, date, time
from decimal import Decimal
from typing import Any, Callable, TypeVar, Generic

T = TypeVar("T")


class empty:
    """Sentinel for empty/unset values."""
    pass


class Field(Generic[T]):
    """
    Base serializer field.
    
    Handles validation, serialization, and deserialization of values.
    """
    
    default_error_messages = {
        "required": "This field is required.",
        "null": "This field may not be null.",
        "invalid": "Invalid value.",
    }
    
    def __init__(
        self,
        *,
        read_only: bool = False,
        write_only: bool = False,
        required: bool | None = None,
        default: T | Callable[[], T] | type[empty] = empty,
        allow_null: bool = False,
        source: str | None = None,
        validators: list[Callable[[T], None]] | None = None,
        error_messages: dict[str, str] | None = None,
        label: str | None = None,
        help_text: str | None = None,
    ) -> None:
        self.read_only = read_only
        self.write_only = write_only
        self._required = required
        self.default = default
        self.allow_null = allow_null
        self.source = source
        self.validators = validators or []
        self.label = label
        self.help_text = help_text
        
        # Merge error messages
        self.error_messages = {**self.default_error_messages}
        if error_messages:
            self.error_messages.update(error_messages)
        
        # Set by serializer
        self.field_name: str = ""
        self.parent: Any = None
    
    @property
    def required(self) -> bool:
        """Whether field is required for input."""
        if self._required is not None:
            return self._required
        return self.default is empty and not self.read_only
    
    def bind(self, field_name: str, parent: Any) -> None:
        """Bind field to serializer."""
        self.field_name = field_name
        self.parent = parent
        if self.source is None:
            self.source = field_name
    
    def get_default(self) -> T | type[empty]:
        """Get default value."""
        if self.default is empty:
            return empty
        if callable(self.default):
            return self.default()
        return self.default
    
    def validate_empty_values(self, data: Any) -> tuple[bool, Any]:
        """
        Validate empty values and return (is_empty, data).
        
        Returns True if this is an empty value that should skip further validation.
        """
        if data is empty:
            if self.required:
                self.fail("required")
            return (True, self.get_default())
        
        if data is None:
            if not self.allow_null:
                self.fail("null")
            return (True, None)
        
        return (False, data)
    
    def run_validation(self, data: Any) -> T:
        """Run full validation on value."""
        is_empty, data = self.validate_empty_values(data)
        if is_empty:
            return data
        
        value = self.to_internal_value(data)
        self.run_validators(value)
        return value
    
    def run_validators(self, value: T) -> None:
        """Run all validators on value."""
        errors = []
        for validator in self.validators:
            try:
                validator(value)
            except Exception as e:
                errors.append(str(e))
        
        if errors:
            from zeeb_api.exceptions import ValidationError
            raise ValidationError({self.field_name: errors})
    
    def to_internal_value(self, data: Any) -> T:
        """Convert input data to internal Python value."""
        raise NotImplementedError()
    
    def to_representation(self, value: T) -> Any:
        """Convert internal value to output representation."""
        return value
    
    def fail(self, key: str, **kwargs: Any) -> None:
        """Raise validation error with message."""
        from zeeb_api.exceptions import ValidationError
        message = self.error_messages.get(key, key).format(**kwargs)
        raise ValidationError({self.field_name: [message]})


class CharField(Field[str]):
    """String field."""
    
    default_error_messages = {
        **Field.default_error_messages,
        "blank": "This field may not be blank.",
        "max_length": "Ensure this field has no more than {max_length} characters.",
        "min_length": "Ensure this field has at least {min_length} characters.",
    }
    
    def __init__(
        self,
        *,
        max_length: int | None = None,
        min_length: int | None = None,
        allow_blank: bool = False,
        trim_whitespace: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.max_length = max_length
        self.min_length = min_length
        self.allow_blank = allow_blank
        self.trim_whitespace = trim_whitespace
    
    def to_internal_value(self, data: Any) -> str:
        if not isinstance(data, str):
            data = str(data)
        
        if self.trim_whitespace:
            data = data.strip()
        
        if not self.allow_blank and data == "":
            self.fail("blank")
        
        if self.max_length is not None and len(data) > self.max_length:
            self.fail("max_length", max_length=self.max_length)
        
        if self.min_length is not None and len(data) < self.min_length:
            self.fail("min_length", min_length=self.min_length)
        
        return data


class IntegerField(Field[int]):
    """Integer field."""
    
    default_error_messages = {
        **Field.default_error_messages,
        "invalid": "A valid integer is required.",
        "max_value": "Ensure this value is less than or equal to {max_value}.",
        "min_value": "Ensure this value is greater than or equal to {min_value}.",
    }
    
    def __init__(
        self,
        *,
        max_value: int | None = None,
        min_value: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.max_value = max_value
        self.min_value = min_value
    
    def to_internal_value(self, data: Any) -> int:
        try:
            value = int(data)
        except (TypeError, ValueError):
            self.fail("invalid")
        
        if self.max_value is not None and value > self.max_value:
            self.fail("max_value", max_value=self.max_value)
        
        if self.min_value is not None and value < self.min_value:
            self.fail("min_value", min_value=self.min_value)
        
        return value


class FloatField(Field[float]):
    """Float field."""
    
    default_error_messages = {
        **Field.default_error_messages,
        "invalid": "A valid number is required.",
        "max_value": "Ensure this value is less than or equal to {max_value}.",
        "min_value": "Ensure this value is greater than or equal to {min_value}.",
    }
    
    def __init__(
        self,
        *,
        max_value: float | None = None,
        min_value: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.max_value = max_value
        self.min_value = min_value
    
    def to_internal_value(self, data: Any) -> float:
        try:
            value = float(data)
        except (TypeError, ValueError):
            self.fail("invalid")
        
        if self.max_value is not None and value > self.max_value:
            self.fail("max_value", max_value=self.max_value)
        
        if self.min_value is not None and value < self.min_value:
            self.fail("min_value", min_value=self.min_value)
        
        return value


class DecimalField(Field[Decimal]):
    """Decimal field."""
    
    default_error_messages = {
        **Field.default_error_messages,
        "invalid": "A valid number is required.",
        "max_value": "Ensure this value is less than or equal to {max_value}.",
        "min_value": "Ensure this value is greater than or equal to {min_value}.",
        "max_digits": "Ensure that there are no more than {max_digits} digits in total.",
        "max_decimal_places": "Ensure that there are no more than {max_decimal_places} decimal places.",
    }
    
    def __init__(
        self,
        *,
        max_digits: int | None = None,
        decimal_places: int | None = None,
        max_value: Decimal | None = None,
        min_value: Decimal | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.max_digits = max_digits
        self.decimal_places = decimal_places
        self.max_value = max_value
        self.min_value = min_value
    
    def to_internal_value(self, data: Any) -> Decimal:
        try:
            value = Decimal(str(data))
        except Exception:
            self.fail("invalid")
        
        if self.max_value is not None and value > self.max_value:
            self.fail("max_value", max_value=self.max_value)
        
        if self.min_value is not None and value < self.min_value:
            self.fail("min_value", min_value=self.min_value)
        
        return value
    
    def to_representation(self, value: Decimal) -> str:
        if self.decimal_places is not None:
            return f"{value:.{self.decimal_places}f}"
        return str(value)


class BooleanField(Field[bool]):
    """Boolean field."""
    
    TRUE_VALUES = {"true", "True", "TRUE", "1", "yes", "Yes", "YES", True, 1}
    FALSE_VALUES = {"false", "False", "FALSE", "0", "no", "No", "NO", False, 0}
    NULL_VALUES = {"null", "Null", "NULL", "", None}
    
    default_error_messages = {
        **Field.default_error_messages,
        "invalid": "Must be a valid boolean.",
    }
    
    def to_internal_value(self, data: Any) -> bool:
        if data in self.TRUE_VALUES:
            return True
        if data in self.FALSE_VALUES:
            return False
        if data in self.NULL_VALUES and self.allow_null:
            return None  # type: ignore
        self.fail("invalid")


class DateTimeField(Field[datetime]):
    """DateTime field."""
    
    default_error_messages = {
        **Field.default_error_messages,
        "invalid": "Datetime has wrong format. Use ISO 8601 format.",
    }
    
    def __init__(
        self,
        *,
        format: str | None = None,  # noqa: A002
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.format = format
    
    def to_internal_value(self, data: Any) -> datetime:
        if isinstance(data, datetime):
            return data
        
        if isinstance(data, str):
            try:
                # Try ISO format first
                return datetime.fromisoformat(data.replace("Z", "+00:00"))
            except ValueError:
                pass
            
            if self.format:
                try:
                    return datetime.strptime(data, self.format)
                except ValueError:
                    pass
        
        self.fail("invalid")
    
    def to_representation(self, value: datetime) -> str:
        if self.format:
            return value.strftime(self.format)
        return value.isoformat()


class DateField(Field[date]):
    """Date field."""
    
    default_error_messages = {
        **Field.default_error_messages,
        "invalid": "Date has wrong format. Use YYYY-MM-DD.",
    }
    
    def __init__(
        self,
        *,
        format: str | None = None,  # noqa: A002
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.format = format or "%Y-%m-%d"
    
    def to_internal_value(self, data: Any) -> date:
        if isinstance(data, date) and not isinstance(data, datetime):
            return data
        
        if isinstance(data, str):
            try:
                return datetime.strptime(data, self.format).date()
            except ValueError:
                pass
        
        self.fail("invalid")
    
    def to_representation(self, value: date) -> str:
        return value.strftime(self.format)


class TimeField(Field[time]):
    """Time field."""
    
    default_error_messages = {
        **Field.default_error_messages,
        "invalid": "Time has wrong format. Use HH:MM:SS.",
    }
    
    def __init__(
        self,
        *,
        format: str | None = None,  # noqa: A002
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.format = format or "%H:%M:%S"
    
    def to_internal_value(self, data: Any) -> time:
        if isinstance(data, time):
            return data
        
        if isinstance(data, str):
            try:
                return datetime.strptime(data, self.format).time()
            except ValueError:
                pass
        
        self.fail("invalid")
    
    def to_representation(self, value: time) -> str:
        return value.strftime(self.format)


class EmailField(CharField):
    """Email field with validation."""
    
    default_error_messages = {
        **CharField.default_error_messages,
        "invalid": "Enter a valid email address.",
    }
    
    EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
    
    def to_internal_value(self, data: Any) -> str:
        value = super().to_internal_value(data)
        if not self.EMAIL_REGEX.match(value):
            self.fail("invalid")
        return value.lower()


class URLField(CharField):
    """URL field with validation."""
    
    default_error_messages = {
        **CharField.default_error_messages,
        "invalid": "Enter a valid URL.",
    }
    
    URL_REGEX = re.compile(
        r"^https?://"
        r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"
        r"localhost|"
        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
        r"(?::\d+)?"
        r"(?:/?|[/?]\S+)$",
        re.IGNORECASE,
    )
    
    def to_internal_value(self, data: Any) -> str:
        value = super().to_internal_value(data)
        if not self.URL_REGEX.match(value):
            self.fail("invalid")
        return value


class UUIDField(Field[uuid.UUID]):
    """UUID field."""
    
    default_error_messages = {
        **Field.default_error_messages,
        "invalid": "Must be a valid UUID.",
    }
    
    def to_internal_value(self, data: Any) -> uuid.UUID:
        if isinstance(data, uuid.UUID):
            return data
        
        try:
            return uuid.UUID(str(data))
        except (ValueError, AttributeError):
            self.fail("invalid")
    
    def to_representation(self, value: uuid.UUID) -> str:
        return str(value)


class ListField(Field[list]):
    """List/array field."""
    
    default_error_messages = {
        **Field.default_error_messages,
        "not_a_list": "Expected a list of items.",
        "min_length": "Ensure this field has at least {min_length} elements.",
        "max_length": "Ensure this field has no more than {max_length} elements.",
    }
    
    def __init__(
        self,
        *,
        child: Field | None = None,
        min_length: int | None = None,
        max_length: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.child = child
        self.min_length = min_length
        self.max_length = max_length
    
    def to_internal_value(self, data: Any) -> list:
        if not isinstance(data, list):
            self.fail("not_a_list")
        
        if self.min_length is not None and len(data) < self.min_length:
            self.fail("min_length", min_length=self.min_length)
        
        if self.max_length is not None and len(data) > self.max_length:
            self.fail("max_length", max_length=self.max_length)
        
        if self.child:
            return [self.child.run_validation(item) for item in data]
        
        return data
    
    def to_representation(self, value: list) -> list:
        if self.child:
            return [self.child.to_representation(item) for item in value]
        return value


class DictField(Field[dict]):
    """Dictionary field."""
    
    default_error_messages = {
        **Field.default_error_messages,
        "not_a_dict": "Expected a dictionary.",
    }
    
    def __init__(
        self,
        *,
        child: Field | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.child = child
    
    def to_internal_value(self, data: Any) -> dict:
        if not isinstance(data, dict):
            self.fail("not_a_dict")
        
        if self.child:
            return {key: self.child.run_validation(val) for key, val in data.items()}
        
        return data
    
    def to_representation(self, value: dict) -> dict:
        if self.child:
            return {key: self.child.to_representation(val) for key, val in value.items()}
        return value


class SerializerMethodField(Field):
    """
    Read-only field that gets its value from a method on the serializer.
    
    Usage:
        class MySerializer(Serializer):
            full_name = SerializerMethodField()
            
            def get_full_name(self, obj):
                return f"{obj.first_name} {obj.last_name}"
    """
    
    def __init__(self, method_name: str | None = None, **kwargs: Any) -> None:
        kwargs["read_only"] = True
        super().__init__(**kwargs)
        self.method_name = method_name
    
    def bind(self, field_name: str, parent: Any) -> None:
        super().bind(field_name, parent)
        if self.method_name is None:
            self.method_name = f"get_{field_name}"
    
    def to_internal_value(self, data: Any) -> Any:
        # Read-only field
        return None
    
    def to_representation(self, value: Any) -> Any:
        method = getattr(self.parent, self.method_name, None)
        if method is None:
            raise AttributeError(
                f"Method '{self.method_name}' not found on serializer"
            )
        
        # Get the instance from parent context
        instance = getattr(self.parent, "instance", None)
        if instance is not None:
            return method(instance)
        return None


class PrimaryKeyRelatedField(Field):
    """
    Field for ForeignKey relationships using primary key.
    
    Usage:
        author = PrimaryKeyRelatedField(queryset=Author.objects.all())
    """
    
    default_error_messages = {
        **Field.default_error_messages,
        "does_not_exist": "Object with pk={pk_value} does not exist.",
        "incorrect_type": "Incorrect type. Expected pk value.",
    }
    
    def __init__(
        self,
        *,
        queryset: Any = None,
        many: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.queryset = queryset
        self.many = many
    
    def to_internal_value(self, data: Any) -> Any:
        if self.many:
            return [self._get_object(pk) for pk in data]
        return self._get_object(data)
    
    def _get_object(self, pk: Any) -> Any:
        # This would need async handling in practice
        # Return the PK for now, actual lookup happens in serializer.create/update
        return pk
    
    def to_representation(self, value: Any) -> Any:
        if self.many:
            return [getattr(obj, "pk", obj) for obj in value]
        return getattr(value, "pk", value)


class SlugRelatedField(Field):
    """
    Field for ForeignKey relationships using a slug field.
    
    Usage:
        author = SlugRelatedField(slug_field="username", queryset=Author.objects.all())
    """
    
    default_error_messages = {
        **Field.default_error_messages,
        "does_not_exist": "Object with {slug_field}={value} does not exist.",
        "invalid": "Invalid value.",
    }
    
    def __init__(
        self,
        *,
        slug_field: str,
        queryset: Any = None,
        many: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.slug_field = slug_field
        self.queryset = queryset
        self.many = many
    
    def to_internal_value(self, data: Any) -> Any:
        if self.many:
            return data  # List of slugs
        return data  # Single slug
    
    def to_representation(self, value: Any) -> Any:
        if self.many:
            return [getattr(obj, self.slug_field, obj) for obj in value]
        return getattr(value, self.slug_field, value)
