"""Model base class with metaclass and SQLAlchemy integration."""

from __future__ import annotations

import datetime
from typing import Any, ClassVar, TypeVar, TYPE_CHECKING

from sqlalchemy import Column, MetaData, Table, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from zeeb_orm.models.fields import (
    AutoField,
    DateField,
    DateTimeField,
    Field,
    ForeignKeyField,
    ManyToManyField,
    UUIDAutoField,
)
from zeeb_orm.models.manager import Manager
from zeeb_orm.models.options import Options

if TYPE_CHECKING:
    from zeeb_orm.permissions.rules import Rule

# Global model registry for resolving string references
_model_registry: dict[str, type[Model]] = {}

# Shared metadata for all models
metadata = MetaData()

ModelT = TypeVar("ModelT", bound="Model")

# Permission attribute suffixes
PERMISSION_ATTRS = ("read_permission", "add_permission", "change_permission", "delete_permission")


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
        new_class = super().__new__(mcs, name, bases, namespace)

        # Process Meta options
        new_class._meta = Options.from_meta(meta_class, name)  # type: ignore
        new_class._meta.model = new_class  # type: ignore

        # Collect fields from class and parents
        fields: list[Field[Any]] = []
        fk_fields: list[ForeignKeyField[Any]] = []
        m2m_fields: list[ManyToManyField[Any]] = []
        has_pk = False

        # Inherit fields from parents (including abstract parents)
        for parent in reversed(parents):
            if hasattr(parent, "_meta"):
                for field in parent._meta.local_fields:  # type: ignore
                    if field.name not in namespace:
                        # Clone field for this class
                        field_copy = field.__class__.__new__(field.__class__)
                        field_copy.__dict__.update(field.__dict__)
                        field_copy.contribute_to_class(new_class, field.name)  # type: ignore
                        fields.append(field_copy)
                        if field_copy.primary_key:
                            has_pk = True
                        if isinstance(field_copy, ForeignKeyField):
                            fk_fields.append(field_copy)

        # Collect fields from this class
        for attr_name, attr_value in list(namespace.items()):
            if isinstance(attr_value, Field):
                attr_value.contribute_to_class(new_class, attr_name)  # type: ignore
                fields.append(attr_value)
                if attr_value.primary_key:
                    has_pk = True
                    new_class._meta.pk = attr_value  # type: ignore
                    new_class._meta.pk_name = attr_name  # type: ignore
                if isinstance(attr_value, ForeignKeyField):
                    fk_fields.append(attr_value)
            elif isinstance(attr_value, ManyToManyField):
                attr_value.contribute_to_class(new_class, attr_name)  # type: ignore
                m2m_fields.append(attr_value)

        new_class._meta.local_fields = fields  # type: ignore
        new_class._fk_fields = fk_fields  # type: ignore
        new_class._m2m_fields = m2m_fields  # type: ignore

        # Set up permission rules and generate permission methods
        # (do this before abstract check so abstract models can define permissions)
        _setup_permissions(new_class, namespace)

        # Skip further setup for abstract models
        if new_class._meta.abstract:  # type: ignore
            return new_class

        # Add auto PK if none defined (UUID by default)
        if not has_pk:
            pk_field = UUIDAutoField()
            pk_field.contribute_to_class(new_class, "id")  # type: ignore
            pk_field.__set_name__(new_class, "id")  # type: ignore
            setattr(new_class, "id", pk_field)  # Add as descriptor
            fields.insert(0, pk_field)
            new_class._meta.pk = pk_field  # type: ignore
            new_class._meta.pk_name = "id"  # type: ignore

        new_class._meta.local_fields = fields  # type: ignore
        new_class._fk_fields = fk_fields  # type: ignore
        new_class._m2m_fields = m2m_fields  # type: ignore

        # Set up default manager if not defined
        if "objects" not in namespace:
            manager = Manager()
            manager.contribute_to_class(new_class, "objects")  # type: ignore

        # Register model
        _model_registry[name] = new_class  # type: ignore

        # Set up reverse relations for ForeignKey fields
        for fk_field in fk_fields:
            _pending_relations.append((new_class, fk_field))  # type: ignore

        # Process any pending relations that point to us
        _process_pending_relations()

        # Generate SQLAlchemy model
        new_class._sa_model = None  # Will be created lazily
        new_class._sa_table = None  # Will be created lazily

        return new_class


def _setup_permissions(model_class: type, namespace: dict[str, Any]) -> None:
    """
    Set up permission rules and generate check methods.
    
    Looks for *_permission attributes and generates corresponding
    check_*_permission() and get_*_filter() methods.
    """
    from zeeb_orm.permissions.rules import Rule
    
    # Collect permission rules from class and parents
    permission_rules: dict[str, Rule] = {}
    
    # Inherit from parents
    for parent in model_class.__mro__[1:]:
        for attr_name in PERMISSION_ATTRS:
            if hasattr(parent, attr_name):
                rule = getattr(parent, attr_name)
                if isinstance(rule, Rule) and attr_name not in permission_rules:
                    permission_rules[attr_name] = rule
    
    # Collect from this class (overrides parents)
    for attr_name in PERMISSION_ATTRS:
        if attr_name in namespace:
            rule = namespace[attr_name]
            if isinstance(rule, Rule):
                permission_rules[attr_name] = rule
    
    # Store rules on model
    model_class._permission_rules = permission_rules  # type: ignore
    
    # Generate methods for each permission type
    for attr_name, rule in permission_rules.items():
        permission_type = attr_name.replace("_permission", "")  # "read", "add", etc.
        
        # Generate check method
        check_method_name = f"check_{permission_type}_permission"
        if check_method_name not in namespace:
            check_method = _make_check_method(permission_type, rule)
            setattr(model_class, check_method_name, check_method)
        
        # Generate filter method
        filter_method_name = f"get_{permission_type}_filter"
        if filter_method_name not in namespace:
            filter_method = _make_filter_method(permission_type, rule)
            setattr(model_class, filter_method_name, filter_method)


def _make_check_method(permission_type: str, rule: Rule):
    """Create an async check_*_permission method."""
    
    if permission_type == "add":
        # add_permission is a classmethod (no object yet)
        @classmethod
        async def check_add_permission(cls, user: Any) -> bool:
            """
            Check if user has add permission for this model.
            
            Args:
                user: User to check (can be None for anonymous)
            
            Returns:
                True if permission granted, False otherwise
            """
            return await rule.check(None, user)
        
        return check_add_permission
    
    else:
        # Other permissions are instance methods
        async def check_permission(self, user: Any) -> bool:
            """
            Check if user has permission for this object.
            
            Args:
                user: User to check (can be None for anonymous)
            
            Returns:
                True if permission granted, False otherwise
            """
            return await rule.check(self, user)
        
        check_permission.__doc__ = f"""
            Check if user has {permission_type} permission for this object.
            
            Args:
                user: User to check (can be None for anonymous)
            
            Returns:
                True if permission granted, False otherwise
            """
        check_permission.__name__ = f"check_{permission_type}_permission"
        
        return check_permission


def _make_filter_method(permission_type: str, rule: Rule):
    """Create a classmethod get_*_filter method."""
    
    @classmethod
    def get_filter(cls, user: Any):
        """
        Get Q filter for {permission_type} permission.
        
        Args:
            user: User to generate filter for (can be None for anonymous)
        
        Returns:
            Q filter object
        """
        return rule.to_q(user, cls)
    
    get_filter.__func__.__doc__ = f"""
        Get Q filter for {permission_type} permission.
        
        Args:
            user: User to generate filter for (can be None for anonymous)
        
        Returns:
            Q filter object
        """
    get_filter.__func__.__name__ = f"get_{permission_type}_filter"
    
    return get_filter


# Pending relations to set up (for forward references)
_pending_relations: list[tuple[type, Any]] = []


def _process_pending_relations(resolve_callables: bool = False) -> None:
    """Process pending reverse relations.

    Args:
        resolve_callables: If True, also resolve callable FK targets
            (e.g. ``get_user_model``).  Set to True after all models
            and settings have been loaded (inside ``_register_models``).
    """
    from zeeb_orm.models.manager import RelatedManagerDescriptor

    still_pending = []

    for source_model, fk_field in _pending_relations:
        try:
            # Callable FK targets (e.g. get_user_model) may not be resolvable
            # during import — keep them pending until _register_models() runs
            if (not resolve_callables
                    and callable(fk_field.to)
                    and not isinstance(fk_field.to, type)):
                still_pending.append((source_model, fk_field))
                continue
            target_model = fk_field.get_target_model()

            # Determine the reverse accessor name
            related_name = fk_field.related_name
            if related_name is None:
                # Default: modelname_set
                related_name = f"{source_model.__name__.lower()}_set"

            # Skip if already set
            if hasattr(target_model, related_name):
                continue

            # Add the RelatedManager descriptor to the target model
            descriptor = RelatedManagerDescriptor(
                related_model=source_model,
                field_name=related_name,
                fk_field_name=f"{fk_field.name}_id",
            )
            setattr(target_model, related_name, descriptor)

        except (KeyError, AttributeError):
            # Target model not yet registered
            still_pending.append((source_model, fk_field))

    _pending_relations.clear()
    _pending_relations.extend(still_pending)


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
        if cls._sa_table is None:
            from sqlalchemy import (
                BigInteger,
                Boolean,
                Column,
                Date,
                DateTime,
                Float,
                ForeignKey,
                Integer,
                Numeric,
                String,
                Text,
                Time,
                Uuid,
            )

            columns = []

            for field in cls._meta.local_fields:
                col_type = field.get_column_type()
                col_kwargs: dict[str, Any] = {
                    "nullable": field.null,
                    "primary_key": field.primary_key,
                    "unique": field.unique,
                    "index": field.index,
                }

                if field.default is not None and not callable(field.default):
                    col_kwargs["default"] = field.default

                # Handle FK columns
                if isinstance(field, ForeignKeyField):
                    target_model = field.get_target_model()
                    target_table = target_model._meta.db_table
                    target_pk = target_model._meta.pk_name
                    # Get FK column type from target model's PK
                    target_pk_field = target_model._meta.pk
                    if target_pk_field:
                        col_type = target_pk_field.get_column_type()
                    else:
                        col_type = Integer()  # Fallback
                    col_kwargs["nullable"] = field.null
                    fk = ForeignKey(
                        f"{target_table}.{target_pk}",
                        ondelete=field.on_delete,
                    )
                    columns.append(Column(field.db_column, col_type, fk, **col_kwargs))
                else:
                    columns.append(Column(field.db_column or field.name, col_type, **col_kwargs))

            cls._sa_table = Table(cls._meta.db_table, metadata, *columns)

        return cls._sa_table

    @classmethod
    def _get_sa_model(cls) -> type[Any]:
        """Get or create a SQLAlchemy ORM model class."""
        if cls._sa_model is None:
            from sqlalchemy import ForeignKey as SAForeignKey
            from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

            # Create a unique base for this model
            class Base(DeclarativeBase):
                pass

            # Build attributes for the SA model
            attrs: dict[str, Any] = {
                "__tablename__": cls._meta.db_table,
                "__table_args__": {"extend_existing": True},
            }

            for field in cls._meta.local_fields:
                if isinstance(field, ForeignKeyField):
                    # FK column
                    target_model = field.get_target_model()
                    target_table = target_model._meta.db_table
                    target_pk = target_model._meta.pk_name
                    attrs[field.db_column] = mapped_column(
                        SAForeignKey(f"{target_table}.{target_pk}", ondelete=field.on_delete),
                        nullable=field.null,
                    )
                else:
                    # Regular column
                    col_type = field.get_column_type()
                    kwargs: dict[str, Any] = {
                        "nullable": field.null,
                        "primary_key": field.primary_key,
                        "unique": field.unique,
                        "index": field.index,
                    }
                    if field.default is not None and not callable(field.default):
                        kwargs["default"] = field.default
                    attrs[field.db_column or field.name] = mapped_column(col_type, **kwargs)

            # Create the SA model class
            sa_model = type(f"_SA{cls.__name__}", (Base,), attrs)
            cls._sa_model = sa_model

        return cls._sa_model

    def _to_sa_instance(self) -> Any:
        """Convert to SQLAlchemy model instance."""
        sa_model = self._get_sa_model()
        kwargs = {}

        for field in self._meta.local_fields:
            value = getattr(self, field.name, None)
            
            # Handle callable defaults (e.g., UUIDAutoField)
            if value is None and hasattr(field, 'default') and field.default is not None:
                if callable(field.default):
                    value = field.default()
                    setattr(self, field.name, value)  # Store on instance too

            # Handle auto timestamps
            if isinstance(field, DateTimeField):
                if field.auto_now_add and value is None and not self._state.persisted:
                    value = datetime.datetime.now(datetime.timezone.utc)
                elif field.auto_now:
                    value = datetime.datetime.now(datetime.timezone.utc)
            elif isinstance(field, DateField):
                if field.auto_now_add and value is None and not self._state.persisted:
                    value = datetime.date.today()
                elif field.auto_now:
                    value = datetime.date.today()

            if value is not None:
                kwargs[field.db_column or field.name] = value

        return sa_model(**kwargs)

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
                # Insert new
                sa_instance = self._to_sa_instance()
                session.add(sa_instance)
                await session.commit()
                await session.refresh(sa_instance)

                # Update our instance with generated values
                for field in self._meta.local_fields:
                    col_name = field.db_column or field.name
                    value = getattr(sa_instance, col_name, None)
                    setattr(self, field.name, value)

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
        from zeeb_orm.db.connection import get_connection
        from sqlalchemy import delete
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
        from zeeb_orm.db.connection import get_connection
        from sqlalchemy import select

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
