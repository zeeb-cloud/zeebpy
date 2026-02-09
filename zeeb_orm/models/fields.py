"""Django-style field definitions mapping to SQLAlchemy column types."""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import TYPE_CHECKING, Any, Generic, TypeVar, overload

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey as SAForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from sqlalchemy.orm import RelationshipProperty

    from zeeb_orm.models.base import Model

T = TypeVar("T")
ModelT = TypeVar("ModelT", bound="Model")


class Field(Generic[T]):
    """Base field class with Django-style options."""

    _column_type: Any = None
    _python_type: type[T] | None = None

    def __init__(
        self,
        *,
        null: bool = False,
        blank: bool = False,
        default: T | None = None,
        primary_key: bool = False,
        unique: bool = False,
        index: bool = False,
        db_column: str | None = None,
        verbose_name: str | None = None,
        help_text: str | None = None,
        choices: list[tuple[T, str]] | None = None,
        validators: list[Any] | None = None,
        editable: bool = True,
    ) -> None:
        self.null = null
        self.blank = blank
        self.default = default
        self.primary_key = primary_key
        self.unique = unique
        self.index = index
        self.db_column = db_column
        self.verbose_name = verbose_name
        self.help_text = help_text
        self.choices = choices
        self.validators = validators or []
        self.editable = editable

        # Set by metaclass
        self.name: str = ""
        self.model: type[Model] | None = None

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name
        if self.db_column is None:
            self.db_column = name

    @overload
    def __get__(self, obj: None, objtype: type) -> Field[T]: ...

    @overload
    def __get__(self, obj: object, objtype: type) -> T: ...

    def __get__(self, obj: object | None, objtype: type) -> Field[T] | T:
        if obj is None:
            return self
        return getattr(obj, f"_field_{self.name}", self.default)  # type: ignore

    def __set__(self, obj: object, value: T) -> None:
        setattr(obj, f"_field_{self.name}", value)

    def get_column_type(self) -> Any:
        """Return the SQLAlchemy column type."""
        return self._column_type

    def contribute_to_class(self, model: type[Model], name: str) -> None:
        """Called when field is added to a model class."""
        self.name = name
        self.model = model

    def to_mapped_column(self) -> Mapped[T]:
        """Convert to SQLAlchemy mapped_column."""
        kwargs: dict[str, Any] = {
            "nullable": self.null,
            "primary_key": self.primary_key,
            "unique": self.unique,
            "index": self.index,
        }

        if self.db_column and self.db_column != self.name:
            kwargs["name"] = self.db_column

        if self.default is not None:
            kwargs["default"] = self.default

        return mapped_column(self.get_column_type(), **kwargs)  # type: ignore


class AutoField(Field[int]):
    """Auto-incrementing integer primary key."""

    _column_type = Integer
    _python_type = int

    def __init__(self, **kwargs: Any) -> None:
        kwargs["primary_key"] = True
        super().__init__(**kwargs)

    def to_mapped_column(self) -> Mapped[int]:
        return mapped_column(Integer, primary_key=True, autoincrement=True)  # type: ignore


class BigAutoField(Field[int]):
    """Auto-incrementing big integer primary key."""

    _column_type = BigInteger
    _python_type = int

    def __init__(self, **kwargs: Any) -> None:
        kwargs["primary_key"] = True
        super().__init__(**kwargs)

    def to_mapped_column(self) -> Mapped[int]:
        return mapped_column(BigInteger, primary_key=True, autoincrement=True)  # type: ignore


class UUIDField(Field[uuid.UUID]):
    """UUID field."""

    _column_type = Uuid
    _python_type = uuid.UUID

    def __init__(self, *, auto: bool = False, **kwargs: Any) -> None:
        self.auto = auto
        if auto and kwargs.get("default") is None:
            kwargs["default"] = uuid.uuid4
        super().__init__(**kwargs)


class UUIDAutoField(Field[uuid.UUID]):
    """Auto-generating UUID primary key field.
    
    This is the default primary key type for models.
    Use AutoField or BigAutoField if you need integer IDs.
    """

    _column_type = Uuid
    _python_type = uuid.UUID

    def __init__(self, **kwargs: Any) -> None:
        kwargs["primary_key"] = True
        if kwargs.get("default") is None:
            kwargs["default"] = uuid.uuid4
        super().__init__(**kwargs)

    def to_mapped_column(self) -> Mapped[uuid.UUID]:
        return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)  # type: ignore


class CharField(Field[str]):
    """Character field with max_length."""

    _python_type = str

    def __init__(self, *, max_length: int = 255, **kwargs: Any) -> None:
        self.max_length = max_length
        super().__init__(**kwargs)

    def get_column_type(self) -> Any:
        return String(self.max_length)


class TextField(Field[str]):
    """Unlimited text field."""

    _column_type = Text
    _python_type = str


class EmailField(CharField):
    """Email field (CharField with email validation)."""

    def __init__(self, *, max_length: int = 254, **kwargs: Any) -> None:
        super().__init__(max_length=max_length, **kwargs)


class SlugField(CharField):
    """Slug field for URL-friendly strings."""

    def __init__(self, *, max_length: int = 50, **kwargs: Any) -> None:
        super().__init__(max_length=max_length, **kwargs)


class URLField(CharField):
    """URL field."""

    def __init__(self, *, max_length: int = 200, **kwargs: Any) -> None:
        super().__init__(max_length=max_length, **kwargs)


class IntegerField(Field[int]):
    """Integer field."""

    _column_type = Integer
    _python_type = int


class SmallIntegerField(Field[int]):
    """Small integer field."""

    _column_type = SmallInteger
    _python_type = int


class BigIntegerField(Field[int]):
    """Big integer field."""

    _column_type = BigInteger
    _python_type = int


class PositiveIntegerField(IntegerField):
    """Positive integer field (validation only, no DB constraint)."""

    pass


class FloatField(Field[float]):
    """Float field."""

    _column_type = Float
    _python_type = float


class DecimalField(Field[decimal.Decimal]):
    """Decimal field with precision."""

    _python_type = decimal.Decimal

    def __init__(
        self, *, max_digits: int = 10, decimal_places: int = 2, **kwargs: Any
    ) -> None:
        self.max_digits = max_digits
        self.decimal_places = decimal_places
        super().__init__(**kwargs)

    def get_column_type(self) -> Any:
        return Numeric(precision=self.max_digits, scale=self.decimal_places)


class BooleanField(Field[bool]):
    """Boolean field."""

    _column_type = Boolean
    _python_type = bool

    def __init__(self, *, default: bool = False, **kwargs: Any) -> None:
        super().__init__(default=default, **kwargs)


class DateField(Field[datetime.date]):
    """Date field."""

    _column_type = Date
    _python_type = datetime.date

    def __init__(self, *, auto_now: bool = False, auto_now_add: bool = False, **kwargs: Any) -> None:
        self.auto_now = auto_now
        self.auto_now_add = auto_now_add
        super().__init__(**kwargs)


class TimeField(Field[datetime.time]):
    """Time field."""

    _column_type = Time
    _python_type = datetime.time


class DateTimeField(Field[datetime.datetime]):
    """DateTime field."""

    _column_type = DateTime
    _python_type = datetime.datetime

    def __init__(
        self,
        *,
        auto_now: bool = False,
        auto_now_add: bool = False,
        timezone: bool = True,
        **kwargs: Any,
    ) -> None:
        self.auto_now = auto_now
        self.auto_now_add = auto_now_add
        self.timezone = timezone
        super().__init__(**kwargs)

    def get_column_type(self) -> Any:
        return DateTime(timezone=self.timezone)


class JSONField(Field[dict[str, Any] | list[Any]]):
    """JSON field."""

    _column_type = JSON
    _python_type = dict


class ForeignKeyField(Field[ModelT]):
    """Foreign key relationship field."""

    def __init__(
        self,
        to: type[ModelT] | str,
        *,
        on_delete: str = "CASCADE",
        related_name: str | None = None,
        db_column: str | None = None,
        null: bool = False,
        **kwargs: Any,
    ) -> None:
        self.to = to
        self.on_delete = on_delete
        self.related_name = related_name
        self._db_column = db_column
        super().__init__(null=null, **kwargs)

    def get_target_model(self) -> type[Model]:
        """Resolve the target model class."""
        if isinstance(self.to, str):
            # Handle self-referential ForeignKey
            if self.to == "self":
                return self._owner_model
            from zeeb_orm.models.base import _model_registry
            return _model_registry[self.to]
        return self.to
    
    def __set_name__(self, owner: type, name: str) -> None:
        # Store owner model for self-referential FK resolution
        self._owner_model = owner
        super().__set_name__(owner, name)
        if self._db_column is None:
            self._db_column = f"{name}_id"
        self.db_column = self._db_column

    def __get__(self, obj: object | None, objtype: type) -> Any:
        """
        Django-like FK access:
        - post.author returns the related Author object (lazy loaded)
        - Use post.author_id for raw ID access
        """
        if obj is None:
            return self

        # Get the stored FK ID
        fk_id = getattr(obj, f"_field_{self.name}_id", None)

        if fk_id is None:
            return None

        # Check if we have a cached instance
        cached = getattr(obj, f"_cache_{self.name}", None)
        if cached is not None:
            return cached

        # Return a lazy loader that fetches on await
        return ForeignKeyLazyLoader(self, obj, fk_id)

    def __set__(self, obj: object, value: Any) -> None:
        """
        Django-like FK assignment:
        - post.author = user (accepts model instance)
        - post.author = 1 (accepts raw ID)
        """
        if value is None:
            setattr(obj, f"_field_{self.name}_id", None)
            setattr(obj, f"_cache_{self.name}", None)
        elif isinstance(value, int):
            # Raw ID
            setattr(obj, f"_field_{self.name}_id", value)
            setattr(obj, f"_cache_{self.name}", None)
        elif hasattr(value, "pk"):
            # Model instance - extract ID and cache instance
            setattr(obj, f"_field_{self.name}_id", value.pk)
            setattr(obj, f"_cache_{self.name}", value)
        else:
            setattr(obj, f"_field_{self.name}_id", value)

    def contribute_to_class(self, model: type[Model], name: str) -> None:
        """Add FK column and relationship to model."""
        super().contribute_to_class(model, name)

        # Add {name}_id property for raw ID access (Django-style)
        id_attr = f"{name}_id"
        field_name = name

        def id_getter(self: Any) -> Any:
            return getattr(self, f"_field_{field_name}_id", None)

        def id_setter(self: Any, value: Any) -> None:
            setattr(self, f"_field_{field_name}_id", value)
            setattr(self, f"_cache_{field_name}", None)  # Clear cache

        # Create the property and set it on the model class
        setattr(model, id_attr, property(id_getter, id_setter))


class ForeignKeyLazyLoader:
    """
    Lazy loader for ForeignKey that fetches the related object on await.
    
    Usage:
        author = await post.author  # Fetches from DB
    """

    def __init__(self, field: ForeignKeyField[Any], instance: Any, fk_id: Any) -> None:
        self._field = field
        self._instance = instance
        self._fk_id = fk_id

    def __await__(self) -> Any:
        return self._fetch().__await__()

    async def _fetch(self) -> Any:
        """Fetch the related object from the database."""
        target_model = self._field.get_target_model()
        related_obj = await target_model.objects.get(pk=self._fk_id)

        # Cache the result
        setattr(self._instance, f"_cache_{self._field.name}", related_obj)

        return related_obj

    def __repr__(self) -> str:
        return f"<ForeignKeyLazyLoader: {self._field.name}={self._fk_id}>"


class OneToOneField(ForeignKeyField[ModelT]):
    """One-to-one relationship field."""

    def __init__(self, to: type[ModelT] | str, **kwargs: Any) -> None:
        kwargs.setdefault("unique", True)
        super().__init__(to, **kwargs)


class ManyToManyField(Generic[ModelT]):
    """Many-to-many relationship field."""

    def __init__(
        self,
        to: type[ModelT] | str,
        *,
        related_name: str | None = None,
        through: type[Model] | str | None = None,
        through_fields: tuple[str, str] | None = None,
        db_table: str | None = None,
    ) -> None:
        self.to = to
        self.related_name = related_name
        self.through = through
        self.through_fields = through_fields
        self.db_table = db_table
        self.name: str = ""
        self.model: type[Model] | None = None

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def contribute_to_class(self, model: type[Model], name: str) -> None:
        """Add M2M relationship to model."""
        self.name = name
        self.model = model


# Convenience aliases
ForeignKey = ForeignKeyField
OneToOne = OneToOneField
ManyToMany = ManyToManyField
