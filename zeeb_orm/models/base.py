"""Model base class with metaclass and SQLAlchemy integration."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar, cast

from sqlalchemy import Table

from zeeb_orm.models.fields import (
    DateField,
    DateTimeField,
    Field,
    ForeignKeyField,
    ManyToManyField,
    UUIDAutoField,
)
from zeeb_orm.models.manager import Manager
from zeeb_orm.models.options import Options

# Compatibility re-exports — these previously lived in this module and are
# imported from here by migrations/cli.py, tests and downstream code.
from zeeb_orm.models.permissions_gen import (  # noqa: F401
    PERMISSION_ATTRS,
    _make_check_method,
    _make_filter_method,
    _setup_permissions,
)
from zeeb_orm.models.relations import (  # noqa: F401
    _pending_relations,
    _process_pending_relations,
)
from zeeb_orm.models.sa_builder import (  # noqa: F401
    build_sa_model,
    build_table,
    metadata,
    to_sa_instance,
)

if TYPE_CHECKING:
    from zeeb_orm.permissions.rules import Rule

# Global model registry for resolving string references
_model_registry: dict[str, type[Model]] = {}

ModelT = TypeVar("ModelT", bound="Model")


class ModelBase(type):
    """
    Metaclass for Model that handles:
    - Field collection and registration
    - Meta options processing
    - SQLAlchemy model generation
    - Manager setup
    """

    def __new__(
        mcs, name: str, bases: tuple[type, ...], namespace: dict[str, Any], **kwargs: Any
    ) -> ModelBase:
        # Don't process the base Model class itself
        parents = [b for b in bases if isinstance(b, ModelBase)]
        if not parents:
            return super().__new__(mcs, name, bases, namespace)

        # Extract Meta class
        meta_class = namespace.pop("Meta", None)

        # Create the class
        new_class = cast("type[Model]", super().__new__(mcs, name, bases, namespace))

        # Process Meta options
        new_class._meta = Options.from_meta(meta_class, name)
        new_class._meta.model = new_class

        # Collect fields from class and parents
        fields: list[Field[Any]] = []
        fk_fields: list[ForeignKeyField[Any]] = []
        m2m_fields: list[ManyToManyField[Any]] = []
        has_pk = False

        # Inherit fields from parents (including abstract parents)
        for parent in reversed(parents):
            if hasattr(parent, "_meta"):
                for field in parent._meta.local_fields:
                    if field.name not in namespace:
                        # Clone field for this class
                        field_copy = field.__class__.__new__(field.__class__)
                        field_copy.__dict__.update(field.__dict__)
                        field_copy.contribute_to_class(new_class, field.name)
                        fields.append(field_copy)
                        if field_copy.primary_key:
                            has_pk = True
                        if isinstance(field_copy, ForeignKeyField):
                            fk_fields.append(field_copy)

        # Collect fields from this class
        for attr_name, attr_value in list(namespace.items()):
            if isinstance(attr_value, Field):
                attr_value.contribute_to_class(new_class, attr_name)
                fields.append(attr_value)
                if attr_value.primary_key:
                    has_pk = True
                    new_class._meta.pk = attr_value
                    new_class._meta.pk_name = attr_name
                if isinstance(attr_value, ForeignKeyField):
                    fk_fields.append(attr_value)
            elif isinstance(attr_value, ManyToManyField):
                attr_value.contribute_to_class(new_class, attr_name)
                m2m_fields.append(attr_value)

        new_class._meta.local_fields = fields
        new_class._fk_fields = fk_fields
        new_class._m2m_fields = m2m_fields

        # Set up permission rules and generate permission methods
        # (do this before abstract check so abstract models can define permissions)
        _setup_permissions(new_class, namespace)

        # Skip further setup for abstract models
        if new_class._meta.abstract:
            return new_class

        # Add auto PK if none defined (UUID by default)
        if not has_pk:
            pk_field = UUIDAutoField()
            pk_field.contribute_to_class(new_class, "id")
            pk_field.__set_name__(new_class, "id")
            setattr(new_class, "id", pk_field)  # Add as descriptor
            fields.insert(0, pk_field)
            new_class._meta.pk = pk_field
            new_class._meta.pk_name = "id"

        new_class._meta.local_fields = fields
        new_class._fk_fields = fk_fields
        new_class._m2m_fields = m2m_fields

        # Set up default manager if not defined
        if "objects" not in namespace:
            manager = Manager()
            manager.contribute_to_class(new_class, "objects")

        # Register model
        _model_registry[name] = new_class

        # Set up reverse relations for ForeignKey fields
        for fk_field in fk_fields:
            _pending_relations.append((new_class, fk_field))

        # Process any pending relations that point to us
        _process_pending_relations()

        # Generate SQLAlchemy model
        new_class._sa_model = None  # Will be created lazily
        new_class._sa_table = None  # Will be created lazily

        return new_class


class DoesNotExist(Exception):
    """Raised when a query returns no results."""

    pass


class MultipleObjectsReturned(Exception):
    """Raised when get() returns more than one object."""

    pass


class Model(metaclass=ModelBase):
    """
    Base class for all ORM models.

    Usage:
        class User(Model):
            name = CharField(max_length=100)
            email = EmailField(unique=True)
            age = IntegerField(null=True)

            class Meta:
                table_name = 'users'
                ordering = ['-created_at']
    """

    _meta: ClassVar[Options]
    _sa_model: ClassVar[type[Any] | None]
    _sa_table: ClassVar[Table | None]
    _fk_fields: ClassVar[list[ForeignKeyField[Any]]]
    _m2m_fields: ClassVar[list[ManyToManyField[Any]]]
    _permission_rules: ClassVar[dict[str, "Rule"]]

    # Exception classes bound to model
    DoesNotExist: ClassVar[type[DoesNotExist]] = DoesNotExist
    MultipleObjectsReturned: ClassVar[type[MultipleObjectsReturned]] = MultipleObjectsReturned

    objects: ClassVar[Manager[Model]]

    class Meta:
        abstract = True

    def __init__(self, **kwargs: Any) -> None:
        # Initialize field values
        for field in self._meta.local_fields:
            # Check for value by field name or db_column (for FK _id suffix)
            value = kwargs.pop(field.name, None)

            # For FK fields, also check for {name}_id
            if value is None and isinstance(field, ForeignKeyField):
                value = kwargs.pop(f"{field.name}_id", None)
                if value is not None:
                    # Store as the ID
                    setattr(self, f"_field_{field.name}_id", value)
                    continue

            # Handle defaults - don't apply callable defaults here (defer to create/save)
            # Only apply non-callable defaults
            if value is None and field.default is not None and not callable(field.default):
                value = field.default

            # Use the field's __set__ for proper handling (especially FK)
            if value is not None:
                setattr(self, field.name, value)
            elif not isinstance(field, ForeignKeyField):
                setattr(self, f"_field_{field.name}", value)

        # Store any extra kwargs (for related objects)
        for key, value in kwargs.items():
            setattr(self, key, value)

        # Track if instance is persisted
        self._state = ModelState()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Create model-specific exception classes
        cls.DoesNotExist = type("DoesNotExist", (DoesNotExist,), {"__module__": cls.__module__})
        cls.MultipleObjectsReturned = type(
            "MultipleObjectsReturned", (MultipleObjectsReturned,), {"__module__": cls.__module__}
        )

    def __repr__(self) -> str:
        pk_value = getattr(self, self._meta.pk_name, None)
        return f"<{self.__class__.__name__}: {pk_value}>"

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, self.__class__):
            return False
        pk_name = self._meta.pk_name
        return getattr(self, pk_name) == getattr(other, pk_name)

    def __hash__(self) -> int:
        pk_value = getattr(self, self._meta.pk_name, None)
        return hash((self.__class__.__name__, pk_value))

    @property
    def pk(self) -> Any:
        """Shortcut to primary key value."""
        return getattr(self, self._meta.pk_name)

    @pk.setter
    def pk(self, value: Any) -> None:
        setattr(self, self._meta.pk_name, value)

    @classmethod
    def _get_table(cls) -> Table:
        """Get or create the SQLAlchemy Table for this model."""
        return build_table(cls)

    @classmethod
    def _get_sa_model(cls) -> type[Any]:
        """Get or create a SQLAlchemy ORM model class."""
        return build_sa_model(cls)

    def _to_sa_instance(self) -> Any:
        """Convert to SQLAlchemy model instance.

        .. deprecated::
            Use :meth:`_to_insert_values` for new INSERT paths.  This method
            is kept for backwards compatibility but is not used internally.
        """
        return to_sa_instance(self)

    def _to_insert_values(self) -> dict[str, Any]:
        """Build a ``{column_name: value}`` dict ready for a Core SQL INSERT.

        This is the correct replacement for :meth:`_to_sa_instance`.  It uses
        Core SQL (no ``DeclarativeBase``) so FK models work without any shared-
        metadata issues.

        - FK fields: reads the raw id via the ``{name}_id`` property.
        - Callable defaults (e.g. ``UUIDAutoField``): called and stored on the
          instance so it reflects what will be written.
        - Auto timestamps (``auto_now_add`` / ``auto_now``): generated here and
          stored on the instance.
        - Auto-increment PKs with no value yet: omitted so the DB generates them.
        """
        values: dict[str, Any] = {}

        for field in self._meta.local_fields:
            if isinstance(field, ForeignKeyField):
                value = getattr(self, f"{field.name}_id", None)
            else:
                value = getattr(self, field.name, None)

            # Callable defaults
            if value is None and field.default is not None and callable(field.default):
                value = field.default()
                setattr(self, field.name, value)

            # Auto timestamps
            if isinstance(field, DateTimeField):
                if field.auto_now_add and value is None:
                    value = datetime.datetime.now(datetime.timezone.utc)
                    setattr(self, field.name, value)
                elif field.auto_now:
                    value = datetime.datetime.now(datetime.timezone.utc)
                    setattr(self, field.name, value)
            elif isinstance(field, DateField):
                if field.auto_now_add and value is None:
                    value = datetime.date.today()
                    setattr(self, field.name, value)
                elif field.auto_now:
                    value = datetime.date.today()
                    setattr(self, field.name, value)

            # Let the DB generate auto-increment PKs
            if field.primary_key and value is None:
                continue

            col_name = field.db_column or field.name
            if value is not None:
                values[col_name] = value

        return values

    @classmethod
    def _from_row(cls: type[ModelT], row: Any) -> ModelT:
        """Create model instance from a database row (tuple or Row object)."""
        kwargs = {}

        # Handle both named rows and mapping
        if hasattr(row, "_mapping"):
            # SQLAlchemy Row object
            mapping = row._mapping
            for field in cls._meta.local_fields:
                col_name = field.db_column or field.name
                value = mapping.get(col_name)
                # For FK fields, use the _id suffix key
                if isinstance(field, ForeignKeyField):
                    kwargs[f"{field.name}_id"] = value
                else:
                    kwargs[field.name] = value
        else:
            # Tuple - match by position
            for i, field in enumerate(cls._meta.local_fields):
                if i < len(row):
                    if isinstance(field, ForeignKeyField):
                        kwargs[f"{field.name}_id"] = row[i]
                    else:
                        kwargs[field.name] = row[i]

        instance = cls(**kwargs)
        instance._state.persisted = True
        return instance

    @classmethod
    def _from_sa_instance(cls: type[ModelT], sa_instance: Any) -> ModelT:
        """Create model instance from SQLAlchemy instance."""
        kwargs = {}

        for field in cls._meta.local_fields:
            col_name = field.db_column or field.name
            value = getattr(sa_instance, col_name, None)
            kwargs[field.name] = value

        instance = cls(**kwargs)
        instance._state.persisted = True
        return instance

    async def save(self, update_fields: list[str] | None = None) -> None:
        """
        Save the model instance to the database.

        If update_fields is provided, only those fields will be updated.

        Fires :data:`~zeeb_orm.signals.pre_save` before the DB write and
        :data:`~zeeb_orm.signals.post_save` after the commit.
        """
        from zeeb_orm.db.connection import get_connection
        from zeeb_orm.signals import post_save, pre_save

        db = await get_connection()
        created = not self._state.persisted

        # pre_save fires BEFORE the session opens — exceptions abort the save
        await pre_save.send(
            sender=type(self),
            instance=self,
            created=created,
            update_fields=update_fields,
        )

        async with db.session() as session:
            if self._state.persisted:
                # Update existing
                from sqlalchemy import update

                table = self._get_table()
                pk_col = getattr(table.c, self._meta.pk_name)
                pk_value = getattr(self, self._meta.pk_name)

                values = {}
                fields_to_update = update_fields or [f.name for f in self._meta.local_fields]

                for field in self._meta.local_fields:
                    if field.name not in fields_to_update:
                        continue
                    if field.primary_key:
                        continue

                    # For FK fields, get the _id value
                    if isinstance(field, ForeignKeyField):
                        value = getattr(self, f"{field.name}_id", None)
                    else:
                        value = getattr(self, field.name, None)

                    # Handle auto timestamps
                    if isinstance(field, DateTimeField) and field.auto_now:
                        value = datetime.datetime.now(datetime.timezone.utc)
                        setattr(self, field.name, value)
                    elif isinstance(field, DateField) and field.auto_now:
                        value = datetime.date.today()
                        setattr(self, field.name, value)

                    if value is not None or field.null:
                        values[field.db_column or field.name] = value

                stmt = update(table).where(pk_col == pk_value).values(**values)
                await session.execute(stmt)
                await session.commit()
            else:
                # Insert new via Core SQL (avoids DeclarativeBase FK resolution issues)
                from sqlalchemy import insert as _sa_insert

                table = self._get_table()
                insert_values = self._to_insert_values()
                stmt = _sa_insert(table).values(**insert_values)
                result = await session.execute(stmt)
                await session.commit()

                # Read back DB-generated PK (auto-increment integers)
                if getattr(self, self._meta.pk_name) is None:
                    pk_value = result.inserted_primary_key[0]
                    setattr(self, self._meta.pk_name, pk_value)

                self._state.persisted = True

        # post_save fires AFTER commit — data is in the database
        await post_save.send(
            sender=type(self),
            instance=self,
            created=created,
            update_fields=update_fields,
        )

    async def delete(self) -> None:
        """Delete this model instance from the database.

        Fires :data:`~zeeb_orm.signals.pre_delete` before the DB delete and
        :data:`~zeeb_orm.signals.post_delete` after the commit.
        """
        from sqlalchemy import delete

        from zeeb_orm.db.connection import get_connection
        from zeeb_orm.signals import post_delete, pre_delete

        if not self._state.persisted:
            return

        db = await get_connection()
        table = self._get_table()
        pk_col = getattr(table.c, self._meta.pk_name)
        pk_value = getattr(self, self._meta.pk_name)

        # pre_delete fires BEFORE the session opens — exceptions abort the delete
        await pre_delete.send(sender=type(self), instance=self)

        async with db.session() as session:
            stmt = delete(table).where(pk_col == pk_value)
            await session.execute(stmt)
            await session.commit()

        self._state.persisted = False

        # post_delete fires AFTER commit — instance pk is still set for reference
        await post_delete.send(sender=type(self), instance=self)

    async def refresh_from_db(self, fields: list[str] | None = None) -> None:
        """Reload the model from the database."""
        from sqlalchemy import select

        from zeeb_orm.db.connection import get_connection

        db = await get_connection()
        table = self._get_table()
        pk_col = getattr(table.c, self._meta.pk_name)
        pk_value = getattr(self, self._meta.pk_name)

        async with db.session() as session:
            stmt = select(table).where(pk_col == pk_value)
            result = await session.execute(stmt)
            row = result.fetchone()

            if row is None:
                raise self.DoesNotExist(f"{self.__class__.__name__} instance was deleted")

            for field in self._meta.local_fields:
                if fields and field.name not in fields:
                    continue
                col_name = field.db_column or field.name
                value = getattr(row, col_name, None)
                setattr(self, field.name, value)


class ModelState:
    """Track model instance state."""

    def __init__(self) -> None:
        self.persisted: bool = False
        self.db_alias: str | None = None
