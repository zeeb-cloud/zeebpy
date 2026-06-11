"""Django-style field definitions mapping to SQLAlchemy column types."""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import TYPE_CHECKING, Any, Callable, Generic, TypeVar, overload

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey as SAForeignKey,
    Integer,
    Interval,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from zeeb_orm import validators as orm_validators
from zeeb_orm.models import deletion

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

    def validate(self, value: Any, instance: Any = None) -> None:
        """Validate ``value`` for this field, raising ``ValidationError``.

        Checks performed:

        - ``null=False`` + ``None`` raises (skipped for primary keys,
          ``auto_now``/``auto_now_add`` fields and fields with a ``default``,
          since their values are filled in at insert time).
        - ``choices`` membership.
        - All callables in ``self.validators`` (each may raise
          :class:`~zeeb_orm.exceptions.ValidationError`).
        """
        from zeeb_orm.exceptions import ValidationError

        if value is None:
            if (
                self.null
                or self.primary_key
                or self.default is not None
                or getattr(self, "auto_now", False)
                or getattr(self, "auto_now_add", False)
            ):
                return
            raise ValidationError("This field cannot be null.", code="null")

        errors: list[str] = []

        if self.choices:
            valid_choices = [
                c[0] if isinstance(c, (list, tuple)) else c for c in self.choices
            ]
            if value not in valid_choices:
                errors.append(f"Value {value!r} is not a valid choice.")

        for validator in self.validators:
            try:
                validator(value)
            except ValidationError as exc:
                errors.extend(exc.messages)

        if errors:
            raise ValidationError(errors)

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
        if max_length is not None:
            self.validators.append(orm_validators.MaxLengthValidator(max_length))

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
        self.validators.append(orm_validators.EmailValidator())


class SlugField(CharField):
    """Slug field for URL-friendly strings."""

    def __init__(self, *, max_length: int = 50, **kwargs: Any) -> None:
        super().__init__(max_length=max_length, **kwargs)
        self.validators.append(orm_validators.validate_slug)


class URLField(CharField):
    """URL field."""

    def __init__(self, *, max_length: int = 200, **kwargs: Any) -> None:
        super().__init__(max_length=max_length, **kwargs)
        self.validators.append(orm_validators.URLValidator())


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
    """Positive integer field (>= 0; validation only, no DB constraint)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.validators.append(orm_validators.MinValueValidator(0))


class PositiveSmallIntegerField(SmallIntegerField):
    """Positive small integer field (>= 0; validation only, no DB constraint)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.validators.append(orm_validators.MinValueValidator(0))


class PositiveBigIntegerField(BigIntegerField):
    """Positive big integer field (>= 0; validation only, no DB constraint)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.validators.append(orm_validators.MinValueValidator(0))


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


class BinaryField(Field[bytes]):
    """Raw binary data field (``bytes``), stored as BLOB/BYTEA.

    Args:
        max_length: Optional maximum size in bytes (enforced by validation
            and, where the backend supports it, the column type).
    """

    _python_type = bytes

    def __init__(self, *, max_length: int | None = None, **kwargs: Any) -> None:
        self.max_length = max_length
        super().__init__(**kwargs)
        if max_length is not None:
            self.validators.append(orm_validators.MaxLengthValidator(max_length))

    def get_column_type(self) -> Any:
        return LargeBinary(length=self.max_length)


class DurationField(Field[datetime.timedelta]):
    """Duration field storing :class:`datetime.timedelta` values.

    Uses a native ``INTERVAL`` column on PostgreSQL.  SQLite and MySQL have
    no interval type, so SQLAlchemy stores the value as a datetime relative
    to the epoch — precision is limited to microseconds and durations must
    stay within the representable datetime range (roughly years 1-9999).
    """

    _column_type = Interval
    _python_type = datetime.timedelta


class GenericIPAddressField(Field[str]):
    """IPv4/IPv6 address field stored as a string.

    Args:
        protocol: ``"both"`` (default), ``"IPv4"`` or ``"IPv6"``.
            Determines which addresses validate.
    """

    _python_type = str

    def __init__(self, *, protocol: str = "both", **kwargs: Any) -> None:
        protocol_lower = protocol.lower()
        if protocol_lower not in ("both", "ipv4", "ipv6"):
            raise ValueError(
                f"Invalid protocol {protocol!r}: expected 'both', 'IPv4' or 'IPv6'."
            )
        self.protocol = protocol
        super().__init__(**kwargs)
        if protocol_lower == "ipv4":
            self.validators.append(orm_validators.validate_ipv4_address)
        elif protocol_lower == "ipv6":
            self.validators.append(orm_validators.validate_ipv6_address)
        else:
            self.validators.append(orm_validators.validate_ipv46_address)

    def get_column_type(self) -> Any:
        # 39 characters fits the longest textual IPv6 address.
        return String(39)


class ForeignKeyField(Field[ModelT]):
    """Foreign key relationship field."""

    def __init__(
        self,
        to: type[ModelT] | str | Callable[[], type[ModelT]],
        *,
        on_delete: str = deletion.CASCADE,
        related_name: str | None = None,
        db_column: str | None = None,
        null: bool = False,
        **kwargs: Any,
    ) -> None:
        if on_delete not in deletion.ON_DELETE_VALUES:
            raise ValueError(
                f"Unknown on_delete value {on_delete!r}. Valid values are: "
                f"{', '.join(sorted(deletion.ON_DELETE_VALUES))}."
            )
        if on_delete == deletion.SET_NULL and not null:
            raise ValueError(
                "ForeignKey with on_delete=SET_NULL requires null=True."
            )
        if on_delete == deletion.SET_DEFAULT and kwargs.get("default") is None:
            raise ValueError(
                "ForeignKey with on_delete=SET_DEFAULT requires a default."
            )
        self.to = to
        self.on_delete = on_delete
        self.related_name = related_name
        self._db_column = db_column
        super().__init__(null=null, **kwargs)

    def get_target_model(self) -> type[Model]:
        """Resolve the target model class.

        Supports three forms for ``to``:
        - A model **class** (returned as-is).
        - A **string** name looked up in the model registry (e.g. ``"User"``).
        - A **callable** that returns the model class (lazy resolution,
          useful for swappable models like ``AUTH_USER_MODEL``).
        """
        if callable(self.to) and not isinstance(self.to, type):
            return self.to()
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

    def __init__(self, to: type[ModelT] | str | Callable[[], type[ModelT]], **kwargs: Any) -> None:
        kwargs.setdefault("unique", True)
        super().__init__(to, **kwargs)


class ManyToManyField(Generic[ModelT]):
    """Many-to-many relationship field.

    Without ``through``, a join table is auto-created in the shared metadata
    (named ``db_table`` or ``"{source_table}_{field_name}"``) with
    ``{source}_id`` / ``{target}_id`` columns, ON DELETE CASCADE foreign
    keys and a unique constraint on the pair.

    With a custom ``through`` model, reads work through that model's table
    but ``add()``/``remove()``/``clear()``/``set()`` raise
    :class:`~zeeb_orm.exceptions.NotSupportedError` — create/delete rows on
    the through model directly (Django parity).

    Usage:
        class Post(Model):
            tags = fields.ManyToMany("Tag", related_name="posts")

        await post.tags.add(tag)
        await post.tags.all()
        Post.objects.filter(tags__name="python")
    """

    is_relation = True
    many_to_many = True

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

    # Descriptor protocol — instance access returns a ManyRelatedManager

    def __get__(self, obj: object | None, objtype: type | None = None) -> Any:
        if obj is None:
            return self
        from zeeb_orm.models.related_m2m import ManyRelatedManager

        return ManyRelatedManager(field=self, instance=obj, reverse=False)

    # Target / through resolution

    def get_target_model(self) -> type[Model]:
        """Resolve the target model class (class, registry name or 'self')."""
        if isinstance(self.to, str):
            if self.to == "self":
                assert self.model is not None
                return self.model
            from zeeb_orm.models.base import _model_registry

            return _model_registry[self.to]
        return self.to

    @property
    def has_custom_through(self) -> bool:
        """True when an explicit ``through=`` model was given."""
        return self.through is not None

    def get_through_model(self) -> type[Model] | None:
        """Resolve the custom through model (None for auto-created tables)."""
        if self.through is None:
            return None
        if isinstance(self.through, str):
            from zeeb_orm.models.base import _model_registry

            return _model_registry[self.through]
        return self.through

    def get_through_table_name(self) -> str:
        """Name of the join table (custom through table or auto name)."""
        through_model = self.get_through_model()
        if through_model is not None:
            return through_model._meta.db_table
        if self.db_table:
            return self.db_table
        assert self.model is not None
        return f"{self.model._meta.db_table}_{self.name}"

    def get_through_table(self) -> Any:
        """Return (creating if needed) the SQLAlchemy Table of the join table."""
        through_model = self.get_through_model()
        if through_model is not None:
            return through_model._get_table()
        from zeeb_orm.models.sa_builder import build_m2m_through_table

        return build_m2m_through_table(self)

    def _auto_column_names(self) -> tuple[str, str]:
        """(source_column, target_column) for the auto-created join table."""
        assert self.model is not None
        source = f"{self.model.__name__.lower()}_id"
        target = f"{self.get_target_model().__name__.lower()}_id"
        if source == target:  # self-referential M2M
            return f"from_{source}", f"to_{target}"
        return source, target

    def get_source_column(self) -> str:
        """Join-table column referencing the model owning this field."""
        if self.through is not None:
            fk = self._through_fk(to_source=True)
            return fk.db_column or f"{fk.name}_id"
        return self._auto_column_names()[0]

    def get_target_column(self) -> str:
        """Join-table column referencing the target model."""
        if self.through is not None:
            fk = self._through_fk(to_source=False)
            return fk.db_column or f"{fk.name}_id"
        return self._auto_column_names()[1]

    def _through_fk(self, *, to_source: bool) -> ForeignKeyField[Any]:
        """The FK field on the custom through model for one side."""
        through_model = self.get_through_model()
        assert through_model is not None
        wanted = self.model if to_source else self.get_target_model()

        if self.through_fields:
            fk_name = self.through_fields[0] if to_source else self.through_fields[1]
            for fk in through_model._fk_fields:
                if fk.name == fk_name:
                    return fk
            raise ValueError(
                f"through_fields: {through_model.__name__} has no "
                f"ForeignKey named {fk_name!r}."
            )

        for fk in through_model._fk_fields:
            try:
                if fk.get_target_model() is wanted:
                    return fk
            except (KeyError, AttributeError):
                continue
        assert wanted is not None
        raise ValueError(
            f"Could not find a ForeignKey to {wanted.__name__} on through "
            f"model {through_model.__name__}."
        )

    def __repr__(self) -> str:
        to_name = self.to if isinstance(self.to, str) else self.to.__name__
        return f"<ManyToManyField: {self.name or '?'} -> {to_name}>"


# Convenience aliases
ForeignKey = ForeignKeyField
OneToOne = OneToOneField
ManyToMany = ManyToManyField
