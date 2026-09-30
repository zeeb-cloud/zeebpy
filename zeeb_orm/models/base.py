"""Model base class with metaclass and SQLAlchemy integration."""

from __future__ import annotations

import datetime
from collections.abc import Generator, Mapping
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
    _pending_m2m,
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

#: Global model registry for resolving string references, keyed by the
#: model's label ``"<app_label>.<ClassName>"`` (see :func:`model_label`), so
#: two apps may each define a ``Post`` without one silently replacing the
#: other. Re-registering the same label (a module re-import) replaces the
#: entry.
_model_registry: dict[str, type[Model]] = {}

#: Packages whose models a project model of the same class name shadows in a
#: bare-name lookup (see :func:`resolve_model_ref`).
_FRAMEWORK_PACKAGES = ("zeeb_orm", "zeeb_api", "zeeb_agents")

ModelT = TypeVar("ModelT", bound="Model")

#: "Not passed" marker for ``Model.__init__`` — ``None`` is a real value.
_MISSING: Any = object()


class AmbiguousModelReferenceError(LookupError):
    """A bare model name matches models in more than one app.

    Deliberately not a ``KeyError``: callers treat ``KeyError`` as "not
    registered yet, try again later", and an ambiguity never resolves itself.
    """


def derive_app_label(module: str) -> str:
    """The app label of a model defined in ``module``.

    ``apps.blog.models`` and ``apps.blog.models.post`` → ``blog``;
    ``zeeb_api.auth.models`` → ``auth``; a module with no ``models`` segment
    (a test file, a script) → its last segment. ``Meta.app_label`` overrides
    this.
    """
    parts = [p for p in module.split(".") if p]
    if "models" in parts:
        index = parts.index("models")
        if index > 0:
            return parts[index - 1]
    if len(parts) >= 2 and parts[0] == "apps":
        return parts[1]
    return parts[-1] if parts else module


def model_label(model: type) -> str:
    """``"<app_label>.<ClassName>"`` — the model's registry key."""
    meta = getattr(model, "_meta", None)
    app_label = getattr(meta, "app_label", "") or derive_app_label(model.__module__)
    return f"{app_label}.{model.__name__}"


def _bare_name(key: str) -> str:
    return key.rsplit(".", 1)[-1]


def _is_framework_model(model: type) -> bool:
    module = getattr(model, "__module__", "") or ""
    return module.split(".", 1)[0] in _FRAMEWORK_PACKAGES


def resolve_model_ref(ref: str, relative_to: type | None = None) -> type[Model]:
    """Resolve a string model reference to the registered class.

    Accepts a Django-style label (``"accounts.User"``, also
    ``"apps.accounts.User"``) or a bare class name (``"User"``). Resolution
    order:

    1. the exact registry key;
    2. for a dotted reference, ``<last label segment>.<Name>``;
    3. for a bare name with ``relative_to`` (the model declaring the
       relation), the model of that name in the same app — Django's rule for
       ``ForeignKey("Author")``;
    4. the only registered model of that class name. Several candidates are
       ambiguous — except that a project model shadows a framework model of
       the same name (a project's ``accounts.User`` wins a bare ``"User"``
       over ``zeeb_api``'s own), which is how bare names have always resolved
       in a generated project.

    Raises:
        KeyError: nothing of that name is registered (kept for callers that
            defer on unresolved references), listing the known models.
        AmbiguousModelReferenceError: the name matches models in several apps.
    """
    model = _model_registry.get(ref)
    if model is not None:
        return model

    name = _bare_name(ref)
    if "." in ref:
        label = ref.rsplit(".", 1)[0].rsplit(".", 1)[-1]
        model = _model_registry.get(f"{label}.{name}")
        if model is not None:
            return model
    elif relative_to is not None:
        model = _model_registry.get(f"{model_label(relative_to).rsplit('.', 1)[0]}.{name}")
        if model is not None:
            return model

    candidates: dict[str, type[Model]] = {}
    for key, candidate in list(_model_registry.items()):
        if _bare_name(key) == name and candidate not in candidates.values():
            candidates[key] = candidate
    if len(candidates) == 1:
        return next(iter(candidates.values()))
    if len(candidates) > 1:
        project = {k: m for k, m in candidates.items() if not _is_framework_model(m)}
        if len(project) == 1:
            return next(iter(project.values()))
        raise AmbiguousModelReferenceError(
            f"Model reference {ref!r} is ambiguous: it matches "
            f"{', '.join(sorted(candidates))}. Use the app-qualified label "
            f"(e.g. {sorted(candidates)[0]!r})."
        )
    raise KeyError(
        f"Model {ref!r} is not registered. Known models: "
        f"{', '.join(sorted(_model_registry)) or '(none)'}"
    )


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

        # Process Meta options. `Meta` is popped from every processed class's
        # namespace, so a parent's Meta is unreachable via getattr/MRO (it
        # would resolve to Model.Meta) — inherit from the parent's processed
        # Options instead. Bases are walked in MRO order: the first one to
        # supply an option wins, and the model's own Meta always wins.
        new_class._meta = Options.from_meta(meta_class, name)
        new_class._meta.model = new_class
        for parent in parents:
            parent_meta = getattr(parent, "_meta", None)
            if parent_meta is not None:
                new_class._meta.inherit_from(parent_meta)
        if not new_class._meta.app_label:
            # Derived, not declared: a child of this class derives its own.
            new_class._meta.app_label = derive_app_label(new_class.__module__)

        # Collect fields from class and parents
        fields: list[Field[Any]] = []
        fk_fields: list[ForeignKeyField[Any]] = []
        m2m_fields: list[ManyToManyField[Any]] = []
        has_pk = False

        # A primary key declared here replaces an inherited one instead of
        # joining it into a composite key (which SQLite rejects outright for
        # autoincrement columns, and which no caller intends).
        declares_own_pk = any(
            isinstance(value, Field) and value.primary_key
            for value in namespace.values()
        )

        # Inherit fields from parents (including abstract parents). Each copy
        # is installed on the child as its own descriptor: the parent's field
        # still belongs to the parent, so a relation reached through it would
        # resolve ``"self"`` — and every other per-model lookup — against the
        # parent (for an abstract parent, a class with no table or manager).
        for parent in reversed(parents):
            if hasattr(parent, "_meta"):
                for m2m in getattr(parent, "_m2m_fields", ()):
                    if m2m.name in namespace or any(f.name == m2m.name for f in m2m_fields):
                        continue
                    m2m_copy = m2m.__class__.__new__(m2m.__class__)
                    m2m_copy.__dict__.update(m2m.__dict__)
                    m2m_copy.contribute_to_class(new_class, m2m.name)
                    setattr(new_class, m2m.name, m2m_copy)
                    m2m_fields.append(m2m_copy)
                for field in parent._meta.local_fields:
                    if field.primary_key and declares_own_pk:
                        continue
                    if field.name not in namespace:
                        # Clone field for this class
                        field_copy = field.__class__.__new__(field.__class__)
                        field_copy.__dict__.update(field.__dict__)
                        field_copy.contribute_to_class(new_class, field.name)
                        setattr(new_class, field.name, field_copy)
                        fields.append(field_copy)
                        if field_copy.primary_key:
                            has_pk = True
                            # An inherited PK is still this model's PK; without
                            # this `_meta.pk` stays None while `has_pk` blocks
                            # the auto-PK below, and FK typing, joins and
                            # `obj.pk` all break. An own PK overrides it below.
                            new_class._meta.pk = field_copy
                            new_class._meta.pk_name = field_copy.name
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

        # Register model under its app-qualified label
        _model_registry[model_label(new_class)] = new_class

        # Set up reverse relations for ForeignKey fields
        for fk_field in fk_fields:
            _pending_relations.append((new_class, fk_field))

        # Set up reverse accessors for ManyToMany fields
        for m2m_field in m2m_fields:
            _pending_m2m.append((new_class, m2m_field))

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
        """Build an unsaved instance from field values.

        Mirrors Django: a field that is not passed gets its (non-callable)
        default; a field passed explicitly keeps the value given, ``None``
        included. Callable defaults and auto timestamps are filled in at
        INSERT time. A ForeignKey accepts the related instance under its name
        or the raw id under ``<name>_id``; a settable property (``pk``) may be
        passed too. Anything else raises ``TypeError`` — a misspelt field
        must not silently become a plain attribute that is never saved.
        """
        self._state = ModelState()
        for field in self._meta.local_fields:
            if isinstance(field, ForeignKeyField):
                value = kwargs.pop(field.name, _MISSING)
                raw_id = kwargs.pop(f"{field.name}_id", _MISSING)
                if (value is _MISSING or value is None) and raw_id is not _MISSING:
                    setattr(self, f"_field_{field.name}_id", raw_id)
                    continue
            else:
                value = kwargs.pop(field.name, _MISSING)

            if value is _MISSING:
                # Callable defaults are deferred to the INSERT.
                default = field.default
                value = default if default is not None and not callable(default) else None

            # Use the field's __set__ for proper handling (especially FK)
            if value is not None:
                setattr(self, field.name, value)
            elif isinstance(field, ForeignKeyField):
                setattr(self, f"_field_{field.name}_id", None)
            else:
                setattr(self, f"_field_{field.name}", None)

        if kwargs:
            unexpected = []
            for key, value in kwargs.items():
                attr = getattr(type(self), key, None)
                if isinstance(attr, property) and attr.fset is not None:
                    setattr(self, key, value)
                else:
                    unexpected.append(key)
            if unexpected:
                raise TypeError(
                    f"{type(self).__name__}() got unexpected keyword argument(s): "
                    f"{', '.join(repr(k) for k in unexpected)}. Valid fields: "
                    f"{', '.join(f.name for f in self._meta.local_fields)}."
                )

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
        """Django semantics: same model and same primary key.

        An unsaved instance (no pk yet) is equal only to itself — two new
        objects are not "the same row" just because neither has an id.
        """
        if not isinstance(other, Model):
            return NotImplemented
        if type(self) is not type(other):
            return False
        pk_value = self.pk
        if pk_value is None:
            return self is other
        return bool(pk_value == other.pk)

    def __hash__(self) -> int:
        """Hash of the primary key; an unsaved instance is unhashable.

        Its pk would change on save, and an object whose hash changes is lost
        in every set and dict it was put into (Django raises the same way).
        """
        pk_value = self.pk
        if pk_value is None:
            raise TypeError("Model instances without primary key value are unhashable")
        return hash(pk_value)

    def __await__(self) -> Generator[Any, None, Any]:
        """``await instance`` is the instance itself.

        A ForeignKey attribute is the related instance when it is cached
        (``select_related``, ``prefetch_related``, assignment, ``create``)
        and an awaitable loader otherwise. Making instances awaitable lets
        ``await post.author`` work in both cases, without a query when the
        object is cached, while ``post.author.name`` keeps working on a
        cached relation.
        """
        return self
        yield  # pragma: no cover - makes this a generator function

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
        - ``None`` on a nullable field is written as NULL; on a non-null field
          it is omitted, so the column default (if any) applies.
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

            # A nullable field set to None is written as NULL — omitting it
            # would let the column default overwrite the None the caller
            # chose. A non-null one is omitted so its column default applies.
            col_name = field.db_column or field.name
            if value is not None or field.null:
                values[col_name] = value

        return values

    @classmethod
    def _from_db(
        cls: type[ModelT], values: Mapping[str, Any], alias: str | None = None
    ) -> ModelT:
        """Build a persisted instance from ``{column_name: value}``.

        Loading never applies field defaults: a NULL column is ``None`` on the
        instance, whatever the field's default — otherwise the next
        ``save()`` would write the default over the stored NULL. A column
        absent from ``values`` (``only()`` / ``defer()``) is recorded as
        deferred; ``save()`` leaves deferred columns untouched.
        """
        kwargs: dict[str, Any] = {}
        deferred: set[str] = set()
        for field in cls._meta.local_fields:
            col_name = field.db_column or field.name
            if col_name in values:
                value = values[col_name]
            else:
                deferred.add(field.name)
                value = None
            if isinstance(field, ForeignKeyField):
                kwargs[f"{field.name}_id"] = value
            else:
                kwargs[field.name] = value

        instance = cls(**kwargs)
        instance._state.persisted = True
        instance._state.db_alias = alias
        instance._state.deferred = deferred
        return instance

    @classmethod
    def _from_row(cls: type[ModelT], row: Any) -> ModelT:
        """Create model instance from a database row (tuple or Row object)."""
        if hasattr(row, "_mapping"):
            # SQLAlchemy Row object
            return cls._from_db(row._mapping)
        # Tuple - match by position
        values = {
            field.db_column or field.name: row[i]
            for i, field in enumerate(cls._meta.local_fields)
            if i < len(row)
        }
        return cls._from_db(values)

    @classmethod
    def _from_sa_instance(cls: type[ModelT], sa_instance: Any) -> ModelT:
        """Create model instance from SQLAlchemy instance."""
        values = {}
        for field in cls._meta.local_fields:
            col_name = field.db_column or field.name
            values[col_name] = getattr(sa_instance, col_name, None)
        return cls._from_db(values)

    # Validation (Django-style full_clean / clean_fields / clean)

    def clean_fields(self, exclude: list[str] | None = None) -> None:
        """Validate every field value, collecting per-field errors.

        Raises:
            ValidationError: with a ``message_dict`` mapping field names to
                their error messages.
        """
        from zeeb_orm.exceptions import ValidationError

        excluded = set(exclude or ())
        errors: dict[str, list[str]] = {}

        for field in self._meta.local_fields:
            if field.name in excluded:
                continue
            if isinstance(field, ForeignKeyField):
                value = getattr(self, f"{field.name}_id", None)
            else:
                value = getattr(self, field.name, None)
            try:
                field.validate(value, self)
            except ValidationError as exc:
                errors[field.name] = exc.messages

        if errors:
            raise ValidationError(errors)

    async def clean(self) -> None:
        """Hook for custom model-level validation.

        Override to implement cross-field checks; raise
        :class:`~zeeb_orm.exceptions.ValidationError` on failure.
        Called by :meth:`full_clean` after field validation.
        """

    async def full_clean(self, exclude: list[str] | None = None) -> None:
        """Run :meth:`clean_fields` and :meth:`clean`, merging all errors.

        Raises:
            ValidationError: with a combined ``message_dict``.
        """
        from zeeb_orm.exceptions import ValidationError

        errors: dict[str, list[str]] = {}

        try:
            self.clean_fields(exclude=exclude)
        except ValidationError as exc:
            errors.update(exc.message_dict)

        try:
            await self.clean()
        except ValidationError as exc:
            for key, messages in exc.message_dict.items():
                errors.setdefault(key, []).extend(messages)

        if errors:
            raise ValidationError(errors)

    def _normalize_update_fields(self, update_fields: Any) -> list[str]:
        """Map ``update_fields`` entries to field names, rejecting unknown ones.

        Accepts field names and a ForeignKey's ``<name>_id``. Unknown names,
        many-to-many fields and reverse relations raise ``ValueError`` —
        silently skipping them would report a save that never wrote the value.
        """
        if isinstance(update_fields, str):
            update_fields = [update_fields]
        by_name: dict[str, str] = {}
        for field in self._meta.local_fields:
            by_name[field.name] = field.name
            if isinstance(field, ForeignKeyField):
                by_name[f"{field.name}_id"] = field.name
        names: list[str] = []
        unknown: list[str] = []
        for entry in update_fields:
            name = by_name.get(entry)
            if name is None:
                unknown.append(str(entry))
            elif name not in names:
                names.append(name)
        if unknown:
            raise ValueError(
                "The following fields do not exist in this model, are m2m "
                f"fields, or are non-concrete fields: {', '.join(unknown)}"
            )
        return names

    def _update_values(self, field_names: list[str]) -> dict[str, Any]:
        """``{column: value}`` for an UPDATE of ``field_names`` (pk excluded)."""
        values: dict[str, Any] = {}
        for field in self._meta.local_fields:
            if field.name not in field_names or field.primary_key:
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
        return values

    async def save(
        self,
        update_fields: list[str] | None = None,
        *,
        validate: bool = True,
        using: str | None = None,
    ) -> None:
        """
        Save the model instance to the database.

        Django semantics:

        - A new instance is INSERTed. A persisted one is UPDATEd; when that
          UPDATE matches no row (the row was deleted meanwhile) it is
          INSERTed again.
        - ``update_fields`` restricts the UPDATE to those fields (names, or a
          ForeignKey's ``<name>_id``). An unknown name raises ``ValueError``;
          an empty list saves nothing; an UPDATE that matches no row raises
          :class:`~zeeb_orm.exceptions.DatabaseError` instead of inserting.
        - An instance loaded with ``only()``/``defer()`` updates only the
          fields that were loaded (or assigned since).

        Unless ``validate=False``, :meth:`full_clean` runs first (when
        ``update_fields`` is given, fields not being updated are excluded
        from validation).

        The write joins the active ``atomic()`` transaction when one is
        open; otherwise it commits on its own session. ``using=`` targets a
        registered database alias (defaults to the alias the instance was
        loaded from).

        Fires :data:`~zeeb_orm.signals.pre_save` before the DB write and
        :data:`~zeeb_orm.signals.post_save` after it executes (after the
        commit when this save opened its own session).
        """
        from sqlalchemy import insert as _sa_insert
        from sqlalchemy import select, update

        from zeeb_orm.db.connection import get_session
        from zeeb_orm.exceptions import DatabaseError
        from zeeb_orm.signals import post_save, pre_save

        force_update = False
        if update_fields is not None:
            update_fields = self._normalize_update_fields(update_fields)
            if not update_fields:
                return
            if self.pk is None:
                raise ValueError("Cannot force an update in save() with no primary key.")
            force_update = True
        elif self._state.persisted and self._state.deferred:
            # Django: a deferred instance writes back only what was loaded.
            update_fields = [
                f.name for f in self._meta.local_fields if f.name not in self._state.deferred
            ]
            force_update = True

        if validate:
            exclude = None
            if update_fields:
                exclude = [
                    f.name
                    for f in self._meta.local_fields
                    if f.name not in update_fields
                ]
            await self.full_clean(exclude=exclude)

        alias = using or self._state.db_alias
        created = not self._state.persisted and not force_update

        # pre_save fires BEFORE the session opens — exceptions abort the save
        await pre_save.send(
            sender=type(self),
            instance=self,
            created=created,
            update_fields=update_fields,
        )

        table = self._get_table()
        async with get_session(alias) as (session, should_commit):
            inserted = False
            if self._state.persisted or force_update:
                pk_col = getattr(table.c, self._meta.pk.db_column or self._meta.pk_name)
                pk_value = self.pk
                values = self._update_values(
                    update_fields or [f.name for f in self._meta.local_fields]
                )
                if values:
                    result = await session.execute(
                        update(table).where(pk_col == pk_value).values(**values)
                    )
                    matched = bool(result.rowcount)
                else:
                    # Nothing to write but the pk: the row just has to exist.
                    result = await session.execute(select(pk_col).where(pk_col == pk_value))
                    matched = result.first() is not None
                if not matched:
                    if force_update:
                        raise DatabaseError(
                            "Save with update_fields did not affect any rows."
                            if update_fields is not None
                            else "Save of a deferred instance did not affect any rows."
                        )
                    inserted = True
            else:
                inserted = True

            if inserted:
                # Insert via Core SQL (avoids DeclarativeBase FK resolution issues)
                insert_values = self._to_insert_values()
                result = await session.execute(_sa_insert(table).values(**insert_values))

                # Read back DB-generated PK (auto-increment integers)
                if getattr(self, self._meta.pk_name) is None:
                    pk_value = result.inserted_primary_key[0]
                    setattr(self, self._meta.pk_name, pk_value)
                created = True

            if should_commit:
                await session.commit()

            self._state.persisted = True
            self._state.db_alias = alias
            self._state.deferred.difference_update(update_fields or ())

        # post_save fires AFTER the write — committed unless a surrounding
        # atomic() block owns the commit
        await post_save.send(
            sender=type(self),
            instance=self,
            created=created,
            update_fields=update_fields,
        )

    async def delete(self) -> tuple[int, dict[str, int]]:
        """Delete this model instance, honoring ``on_delete`` rules.

        Related rows are collected via :class:`~zeeb_orm.models.deletion.Collector`
        (CASCADE recursion, PROTECT/RESTRICT checks, SET_NULL/SET_DEFAULT
        updates). Collecting and all writes run in a single transaction
        (``atomic()``, unless one is already active on this database).

        Fires :data:`~zeeb_orm.signals.pre_delete` for every affected
        instance before its row is deleted and
        :data:`~zeeb_orm.signals.post_delete` once the rows are gone. Both run
        inside the delete's transaction, as in Django: a receiver that raises
        rolls the whole delete back. Work that must wait for the commit
        belongs in :func:`~zeeb_orm.db.transaction.on_commit`.

        Returns:
            ``(total_deleted, {model_name: count})``.

        Raises:
            ProtectedError: when PROTECT-related rows reference this object.
            RestrictedError: when RESTRICT-related rows reference this object
                and are not themselves deleted by the same operation.
        """
        from zeeb_orm.db.connection import atomic, get_active_session
        from zeeb_orm.models.deletion import Collector

        if not self._state.persisted:
            return 0, {}

        alias = self._state.db_alias

        async def _collect_and_delete() -> tuple[int, dict[str, int]]:
            # Collected inside the transaction that deletes: a row that
            # starts referencing this one between the two would otherwise
            # escape the cascade (or the PROTECT/RESTRICT check).
            collector = Collector(using=alias)
            # PROTECT / RESTRICT are checked here, BEFORE any delete runs.
            await collector.collect([self])
            return await collector.delete()

        if get_active_session(alias) is not None:
            result = await _collect_and_delete()
        else:
            async with atomic(alias):
                result = await _collect_and_delete()

        self._state.persisted = False
        return result

    async def refresh_from_db(
        self, fields: list[str] | None = None, using: str | None = None
    ) -> None:
        """Reload the model from the database.

        Reads through the active ``atomic()`` session when one is open, so
        in-transaction writes are visible. ``using=`` targets a registered
        database alias (defaults to the alias the instance was loaded from).
        ``fields`` limits the reload (field names or a ForeignKey's
        ``<name>_id``); deferred fields that are reloaded stop being deferred.

        A reloaded ForeignKey drops its cached related object (as in Django),
        so ``obj.author`` never keeps serving the object the old id pointed
        at.
        """
        from sqlalchemy import select

        from zeeb_orm.db.connection import get_session

        alias = using or self._state.db_alias
        wanted = set(self._normalize_update_fields(fields)) if fields else None
        table = self._get_table()
        pk_col = getattr(table.c, self._meta.pk.db_column or self._meta.pk_name)
        pk_value = self.pk

        async with get_session(alias) as (session, _):
            stmt = select(table).where(pk_col == pk_value)
            result = await session.execute(stmt)
            row = result.fetchone()

        if row is None:
            raise self.DoesNotExist(f"{self.__class__.__name__} instance was deleted")

        mapping = row._mapping
        for field in self._meta.local_fields:
            if wanted is not None and field.name not in wanted:
                continue
            value = mapping.get(field.db_column or field.name)
            if isinstance(field, ForeignKeyField):
                setattr(self, f"_field_{field.name}_id", value)
                self.__dict__.pop(f"_cache_{field.name}", None)
            else:
                setattr(self, f"_field_{field.name}", value)
            self._state.deferred.discard(field.name)
        self._state.persisted = True
        self._state.db_alias = alias


class ModelState:
    """Track model instance state."""

    def __init__(self) -> None:
        self.persisted: bool = False
        self.db_alias: str | None = None
        #: Field names not loaded from the database (``only()``/``defer()``).
        self.deferred: set[str] = set()
