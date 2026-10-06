"""
Pydantic-based serializers with Django/DRF-style API.

Provides serializers that:
- Use Pydantic for validation and schema generation
- Keep Django/DRF-style Meta class and field declaration
- Auto-generate FastAPI response models
"""

from __future__ import annotations

from typing import (
    Annotated, Any, Callable, Optional, TypeVar, Generic, ClassVar, Sequence,
    get_type_hints, TYPE_CHECKING,
)
from datetime import datetime, date, time, timedelta
from decimal import Decimal
from enum import Enum
import inspect
import uuid

from pydantic import (
    AfterValidator, AliasChoices, BaseModel, Field, ConfigDict,
    field_validator, model_validator,
)
from pydantic.fields import FieldInfo


def _field_has_constraints(field: Any) -> bool:
    """True if a declared serializer field carries validation beyond its type."""
    return bool(
        getattr(field, "max_length", None) is not None
        or getattr(field, "min_length", None) is not None
        or getattr(field, "allow_blank", True) is False
        or getattr(field, "validators", None)
    )


def _constraint_validator(field: Any):
    """Pydantic AfterValidator that enforces a declared field's own rules.

    Reuses the field's ``run_validation`` (max_length/min_length/allow_blank and
    any custom ``validators=[...]``) so declared constraints are actually applied
    by the exported serializer, and converts the field's error into a plain
    ``ValueError`` that Pydantic surfaces as a normal validation error.
    """

    def _validate(value: Any) -> Any:
        if value is None:
            return value  # nullability is handled by the type itself
        try:
            return field.run_validation(value)
        except Exception as exc:  # field raises zeeb_api ValidationError
            detail = getattr(exc, "detail", None)
            if isinstance(detail, dict):
                messages = [
                    str(m)
                    for msgs in detail.values()
                    for m in (msgs if isinstance(msgs, list) else [msgs])
                ]
                raise ValueError("; ".join(messages) or str(exc)) from exc
            raise ValueError(str(exc)) from exc

    return _validate

if TYPE_CHECKING:
    from zeeb_orm.models.base import Model

ModelT = TypeVar("ModelT")


# =============================================================================
# FIELD MAPPINGS
# =============================================================================

# Map ORM field types to Python/Pydantic types
ORM_TO_PYTHON_TYPE: dict[str, type] = {
    "AutoField": int,
    "BigAutoField": int,
    "UUIDAutoField": uuid.UUID,
    "CharField": str,
    "TextField": str,
    "IntegerField": int,
    "BigIntegerField": int,
    "SmallIntegerField": int,
    "PositiveIntegerField": int,
    "FloatField": float,
    "DecimalField": Decimal,
    "BooleanField": bool,
    "DateTimeField": datetime,
    "DateField": date,
    "TimeField": time,
    # Unmapped, a duration fell to Any: the request's ``60`` reached the column
    # as an int and the insert failed. pydantic parses seconds and ISO 8601.
    "DurationField": timedelta,
    "EmailField": str,
    "URLField": str,
    "UUIDField": uuid.UUID,
    "JSONField": dict,
    "BinaryField": bytes,
    "SlugField": str,
    "IPAddressField": str,
    "GenericIPAddressField": str,
}


def _relation_response_type(model: Any, field_name: str) -> tuple[Any, Any] | None:
    """Response ``(type, default)`` for a to-many / reverse relation.

    Returns the Pydantic field definition for a forward M2M, reverse FK,
    reverse M2M (``list[pk]``) or reverse one-to-one (``pk | None``)
    accessor, or ``None`` when ``field_name`` is not such a relation. These
    fields serialize to related primary keys and are read-only.
    """
    try:
        from zeeb_orm.models.relations import resolve_relation

        rel = resolve_relation(model, field_name)
    except Exception:
        rel = None
    if rel is None or rel.kind not in (
        "m2m", "reverse_m2m", "reverse_fk", "reverse_o2o"
    ):
        return None

    pk_type: Any = uuid.UUID
    target = getattr(rel, "target_model", None)
    target_pk = getattr(getattr(target, "_meta", None), "pk", None)
    if target_pk is not None:
        pk_type = getattr(target_pk, "_python_type", uuid.UUID)

    if rel.kind == "reverse_o2o":
        return (pk_type | None, None)
    return (list[pk_type], [])


# =============================================================================
# SERIALIZER METHOD FIELD
# =============================================================================

class SerializerMethodField:
    """
    Read-only field that gets value from a serializer method.
    
    Usage:
        class UserSerializer(ModelSerializer):
            full_name = SerializerMethodField()
            
            def get_full_name(self, obj) -> str:
                return f"{obj.first_name} {obj.last_name}"
    """
    
    def __init__(
        self,
        method_name: str | None = None,
        return_type: type = str,
    ) -> None:
        self.method_name = method_name
        self.return_type = return_type
        self.field_name: str = ""
    
    def bind(self, field_name: str) -> None:
        self.field_name = field_name
        if self.method_name is None:
            self.method_name = f"get_{field_name}"


class PrimaryKeyRelatedField:
    """
    Field for ForeignKey - accepts/returns primary key.
    
    Usage:
        author_id = PrimaryKeyRelatedField()
    """
    
    def __init__(
        self,
        queryset: Any = None,
        many: bool = False,
        read_only: bool = False,
        required: bool = True,
        allow_null: bool = False,
    ) -> None:
        self.queryset = queryset
        self.many = many
        self.read_only = read_only
        self.required = required
        self.allow_null = allow_null


class NestedSerializer:
    """
    Marker for nested serializer field.
    
    Usage:
        author = NestedSerializer(AuthorSerializer)
    """
    
    def __init__(
        self,
        serializer_class: type,
        many: bool = False,
        read_only: bool = True,
    ) -> None:
        self.serializer_class = serializer_class
        self.many = many
        self.read_only = read_only


# =============================================================================
# SERIALIZER METACLASS
# =============================================================================

class SerializerMetaclass(type):
    """
    Metaclass that processes serializer class definition.
    
    Collects declared fields and generates Pydantic models.
    """
    
    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
    ) -> SerializerMetaclass:
        # Import old-style fields for detection
        from zeeb_api.serializers.fields import Field as OldField
        
        # Collect declared fields (SerializerMethodField, etc.) and old-style
        # field declarations. Start from the bases' (furthest first) so a
        # subclass inherits every field its parents declared and can override
        # one by redeclaring it.
        declared_fields: dict[str, Any] = {}
        old_style_fields: dict[str, Any] = {}
        for base in reversed(bases):
            for klass in reversed(getattr(base, "__mro__", (base,))):
                declared_fields.update(klass.__dict__.get("_declared_fields", {}))
                old_style_fields.update(klass.__dict__.get("_old_style_fields", {}))

        for key, value in list(namespace.items()):
            if isinstance(value, (SerializerMethodField, PrimaryKeyRelatedField, NestedSerializer)):
                declared_fields[key] = value
                old_style_fields.pop(key, None)
                if isinstance(value, SerializerMethodField):
                    value.bind(key)
            elif isinstance(value, OldField):
                old_style_fields[key] = value
                declared_fields.pop(key, None)
                if not value.field_name:
                    value.field_name = key
        
        namespace["_declared_fields"] = declared_fields
        namespace["_old_style_fields"] = old_style_fields
        
        # Create the class
        cls = super().__new__(mcs, name, bases, namespace)
        
        # Generate Pydantic schemas if this is a concrete serializer
        if hasattr(cls, "Meta") and hasattr(cls.Meta, "model"):
            cls._generate_schemas()
        elif old_style_fields:
            # Generate schema from old-style field declarations
            cls._generate_schema_from_fields(old_style_fields)
        
        return cls


# =============================================================================
# SCHEMA BUILDING HELPERS
# =============================================================================


def _build_model(
    name: str, entries: dict[str, tuple[Any, Any]], *, from_attributes: bool = False
) -> type[BaseModel]:
    """A Pydantic model from ``{name: (type, default-or-FieldInfo)}``."""
    namespace: dict[str, Any] = {
        "__annotations__": {k: v[0] for k, v in entries.items()},
        **{
            k: (v[1] if isinstance(v[1], FieldInfo) else Field(default=v[1]))
            for k, v in entries.items()
        },
    }
    if from_attributes:
        namespace["model_config"] = ConfigDict(from_attributes=True)
    return type(name, (BaseModel,), namespace)


def _all_optional(entries: dict[str, tuple[Any, Any]]) -> dict[str, tuple[Any, Any]]:
    """The entries with every field optional (default None), aliases kept."""
    optional: dict[str, tuple[Any, Any]] = {}
    for k, (typ, val) in entries.items():
        optional_type = typ if type(None) in getattr(typ, "__args__", ()) else Optional[typ]
        if isinstance(val, FieldInfo):
            optional[k] = (
                optional_type,
                Field(
                    default=None,
                    validation_alias=val.validation_alias,
                    description=val.description,
                ),
            )
        else:
            optional[k] = (optional_type, None)
    return optional


def _needs_await(value: Any) -> bool:
    """Whether a hook's/method's result must be awaited.

    Model instances are awaitable (they resolve to themselves, so a loaded
    relation and a lazy loader can be awaited alike) but are *values*: a
    ``get_<field>`` or ``validate_<field>`` returning one must not be treated
    as a coroutine.
    """
    if not inspect.isawaitable(value):
        return False
    from zeeb_orm.models.base import Model

    return not isinstance(value, Model)


def _discard(awaitable: Any) -> None:
    """Close an un-awaited coroutine so Python does not warn about it."""
    close = getattr(awaitable, "close", None)
    if callable(close):
        close()


class _HookErrors(Exception):
    """Field errors collected from validation hooks."""

    def __init__(self, errors: dict[str, list[str]]) -> None:
        super().__init__(errors)
        self.errors = errors


def _collect_hook_error(errors: dict[str, list[str]], field: str | None, exc: Exception) -> None:
    """Record a hook's rejection under *field* (or where its detail says).

    ``ValidationError("msg")`` / ``ValueError("msg")`` land on the hook's own
    field (``non_field_errors`` for ``validate()``); a dict detail such as
    ``ValidationError({"password_confirm": "..."})`` names its fields itself.
    Anything that is not a validation error is a bug in the hook and is
    re-raised.
    """
    from zeeb_api.exceptions import ValidationError as APIValidationError

    if isinstance(exc, APIValidationError):
        detail: Any = getattr(exc, "detail", str(exc))
    elif isinstance(exc, ValueError):
        detail = str(exc)
    else:
        raise exc

    def as_list(value: Any) -> list[str]:
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value]
        return [str(value)]

    if isinstance(detail, dict):
        for key, messages in detail.items():
            errors.setdefault(str(key), []).extend(as_list(messages))
    else:
        errors.setdefault(field or "non_field_errors", []).extend(as_list(detail))


# =============================================================================
# BASE SERIALIZER
# =============================================================================

# Mapping from old-style field class names to Python types
OLD_FIELD_TO_TYPE: dict[str, type] = {
    "CharField": str,
    "TextField": str,
    "IntegerField": int,
    "FloatField": float,
    "DecimalField": Decimal,
    "BooleanField": bool,
    "DateTimeField": datetime,
    "DateField": date,
    "TimeField": time,
    "EmailField": str,
    "URLField": str,
    "UUIDField": uuid.UUID,
    "ListField": list,
    "DictField": dict,
}


class Serializer(metaclass=SerializerMetaclass):
    """
    Base serializer with Pydantic integration.
    
    Supports both Pydantic Schema definition and Django-style field declarations:
    
        # Pydantic style
        class LoginSerializer(Serializer):
            class Schema(BaseModel):
                username: str
                password: str
        
        # Django/DRF style  
        class LoginSerializer(Serializer):
            username = CharField()
            password = CharField(write_only=True)
    """
    
    _declared_fields: ClassVar[dict[str, Any]] = {}
    _old_style_fields: ClassVar[dict[str, Any]] = {}
    # Maps a foreign key's bare model name to its canonical "<name>_id" request
    # key. Populated by ModelSerializer._generate_schemas; empty otherwise.
    _fk_request_aliases: ClassVar[dict[str, str]] = {}
    
    # Pydantic schemas - set by metaclass or manually
    Schema: ClassVar[type[BaseModel] | None] = None
    RequestSchema: ClassVar[type[BaseModel] | None] = None
    ResponseSchema: ClassVar[type[BaseModel] | None] = None
    # All-optional variant of RequestSchema, used to type PATCH bodies.
    PartialRequestSchema: ClassVar[type[BaseModel] | None] = None
    
    @staticmethod
    def _declared_field_entries(
        field: Any,
    ) -> tuple[tuple[Any, Any] | None, tuple[Any, Any] | None]:
        """``(response_entry, request_entry)`` for a DRF-style declared field.

        Each entry is ``(type, default)``; None where the field does not appear
        (write-only fields are not returned, read-only ones not accepted).
        """
        from pydantic import EmailStr

        field_class_name = field.__class__.__name__
        if field_class_name == "EmailField":
            python_type: Any = EmailStr
        else:
            python_type = OLD_FIELD_TO_TYPE.get(field_class_name, Any)
        if getattr(field, "allow_null", False):
            python_type = python_type | None

        is_required = getattr(field, "required", True)
        if not is_required:
            python_type = python_type | None
            default_value: Any = None
            default_attr = getattr(field, "default", None)
            if default_attr is not None and not (
                hasattr(default_attr, "__name__") and default_attr.__name__ == "empty"
            ) and not callable(default_attr):
                default_value = default_attr
        else:
            default_attr = getattr(field, "default", None)
            if default_attr is not None:
                if hasattr(default_attr, "__name__") and default_attr.__name__ == "empty":
                    default_value = ...
                elif callable(default_attr):
                    default_value = ...
                else:
                    default_value = default_attr
            else:
                default_value = ...

        description = getattr(field, "help_text", None)
        response_entry = None
        if not getattr(field, "write_only", False):
            response_entry = (
                python_type,
                Field(
                    default=default_value if default_value is not ... else None,
                    description=description,
                ),
            )
        request_entry = None
        if not getattr(field, "read_only", False):
            request_type = python_type
            if _field_has_constraints(field):
                request_type = Annotated[python_type, AfterValidator(_constraint_validator(field))]
            request_entry = (request_type, Field(default=default_value, description=description))
        return response_entry, request_entry

    @classmethod
    def _generate_schema_from_fields(cls, fields: dict[str, Any]) -> None:
        """Generate Pydantic schemas from old-style field declarations."""
        response_fields: dict[str, tuple[Any, Any]] = {}
        request_fields: dict[str, tuple[Any, Any]] = {}

        for field_name, field in fields.items():
            response_entry, request_entry = cls._declared_field_entries(field)
            if response_entry is not None:
                response_fields[field_name] = response_entry
            if request_entry is not None:
                request_fields[field_name] = request_entry

        # Create Pydantic models
        if response_fields:
            cls.ResponseSchema = _build_model(
                f"{cls.__name__}Response", response_fields, from_attributes=True
            )

        if request_fields:
            cls.RequestSchema = _build_model(f"{cls.__name__}Request", request_fields)
            # PATCH schema: every field optional (so a partial payload is
            # accepted) but still constraint-checked when present, since the
            # request type (incl. any AfterValidator) is preserved.
            cls.PartialRequestSchema = _build_model(
                f"{cls.__name__}PartialRequest", _all_optional(request_fields)
            )

        # Default Schema
        cls.Schema = cls.ResponseSchema or cls.RequestSchema
    
    def __init__(
        self,
        instance: Any = None,
        data: dict[str, Any] | None = None,
        *,
        many: bool = False,
        partial: bool = False,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.instance = instance
        self.initial_data = data
        self.many = many
        self.partial = partial
        self.context = context or {}
        
        self._validated_data: dict[str, Any] | None = None
        self._errors: dict[str, list[str]] = {}
        # True once .save() has persisted an instance. The create/update
        # viewset mixins read this after perform_create/perform_update so a
        # hook that calls .save() itself is not saved a second time.
        self._saved: bool = False
    
    @property
    def data(self) -> dict[str, Any] | list[dict[str, Any]]:
        """Get serialized output data."""
        if self.instance is None:
            return self._validated_data or {}
        
        if self.many:
            return [self._serialize_instance(item) for item in self.instance]
        return self._serialize_instance(self.instance)
    
    def _fk_names(self) -> set[str]:
        """ForeignKey/OneToOne field names on the serializer's model.

        Used so FK fields are read from the raw id (e.g. ``project_id``)
        instead of the relation descriptor, which lazily returns a
        ``ForeignKeyLazyLoader`` and would fail response validation.
        ``isinstance`` covers ``OneToOneField`` (its class name lacks
        "foreign").
        """
        from zeeb_orm.models.fields import ForeignKeyField

        meta = getattr(self, "Meta", None)
        model_cls = getattr(meta, "model", None)
        names: set[str] = set()
        if model_cls is not None and hasattr(model_cls, "_meta"):
            for f in getattr(model_cls._meta, "local_fields", []):
                if isinstance(f, ForeignKeyField):
                    names.add(f.name)
        return names

    def _serialize_instance(self, instance: Any) -> dict[str, Any]:
        """Serialize a single instance to dict (synchronous).

        Resolves ForeignKey fields to their raw id and prefetched to-many
        relations to a list of primary keys. To-many / reverse relations that
        are *not* prefetched cannot be loaded synchronously — use
        :meth:`adata` (the async path the viewsets use) or
        ``prefetch_related`` instead.
        """
        from zeeb_orm.exceptions import NotSupportedError
        from zeeb_orm.models.fields import ForeignKeyLazyLoader
        from zeeb_orm.models.manager import Manager

        schema_class = self.ResponseSchema or self.Schema
        if schema_class is None:
            # Fallback: extract all attributes, unwrapping any leaked FK loaders.
            data = {}
            for k in dir(instance):
                if k.startswith("_"):
                    continue
                value = getattr(instance, k, None)
                if isinstance(value, ForeignKeyLazyLoader):
                    value = value._fk_id
                elif isinstance(value, Manager):
                    continue  # unresolved relation manager — skip in fallback
                data[k] = value
            return data

        fk_names = self._fk_names()

        # Build data dict from instance
        data = {}
        for field_name in schema_class.model_fields:
            # Check for SerializerMethodField
            if field_name in self._declared_fields:
                field = self._declared_fields[field_name]
                if isinstance(field, SerializerMethodField):
                    method = getattr(self, field.method_name, None)
                    if method:
                        result = method(instance)
                        if _needs_await(result):
                            raise NotSupportedError(
                                f"Async SerializerMethodField '{field_name}' "
                                "requires the async serializer path; use "
                                "'await serializer.adata()'."
                            )
                        data[field_name] = result
                    continue

            # A declared DRF-style field reads its ``source`` (dotted paths
            # follow loaded relations; an unloaded one needs adata()).
            if field_name in self._old_style_fields:
                value = self._source_value_sync(instance, field_name)
                if value is not None:
                    data[field_name] = value
                continue

            # Get value from instance. For ForeignKey fields, read the raw id
            # rather than the relation attribute (which lazily loads).
            if field_name in fk_names:
                value = getattr(instance, f"{field_name}_id", None)
            elif field_name.endswith("_id") and field_name[:-3] in fk_names:
                value = getattr(instance, field_name, None)
            else:
                value = getattr(instance, field_name, None)
                if isinstance(value, ForeignKeyLazyLoader):
                    # Safety net: unwrap any FK loader reached indirectly.
                    value = value._fk_id
                elif isinstance(value, (list, tuple)):
                    # Prefetched to-many relation: list of related instances.
                    value = [getattr(o, "pk", o) for o in value]
                elif isinstance(value, Manager):
                    raise NotSupportedError(
                        f"Field '{field_name}' is a to-many/reverse relation "
                        "that is not prefetched; it cannot be serialized "
                        "synchronously. Use 'await serializer.adata()' or "
                        "prefetch_related()."
                    )
            if value is not None:
                data[field_name] = value

        return data

    async def adata(self) -> dict[str, Any] | list[dict[str, Any]]:
        """Async serialized output.

        Like :attr:`data`, but resolves to-many / reverse relations (and
        nested serializers over them) by awaiting the related managers. This
        is the path the viewsets use so relation fields serialize to a list
        of related primary keys instead of leaking a manager object.
        """
        if self.instance is None:
            return self._validated_data or {}

        rel_kinds = self._relation_field_kinds()
        if self.many:
            return [
                await self._aserialize_instance(item, rel_kinds)
                for item in self.instance
            ]
        return await self._aserialize_instance(self.instance, rel_kinds)

    def _relation_field_kinds(self) -> dict[str, str]:
        """Map schema field name -> relation kind for to-many/reverse fields."""
        schema_class = self.ResponseSchema or self.Schema
        meta = getattr(self, "Meta", None)
        model_cls = getattr(meta, "model", None)
        kinds: dict[str, str] = {}
        if schema_class is None or model_cls is None:
            return kinds
        from zeeb_orm.models.relations import resolve_relation

        for field_name in schema_class.model_fields:
            if field_name in self._declared_fields:
                continue
            try:
                rel = resolve_relation(model_cls, field_name)
            except Exception:
                rel = None
            if rel is not None and rel.kind in (
                "m2m", "reverse_m2m", "reverse_fk", "reverse_o2o"
            ):
                kinds[field_name] = rel.kind
        return kinds

    async def _aserialize_instance(
        self, instance: Any, rel_kinds: dict[str, str]
    ) -> dict[str, Any]:
        """Async single-instance serialization (resolves relations)."""
        from zeeb_orm.models.fields import ForeignKeyLazyLoader

        schema_class = self.ResponseSchema or self.Schema
        if schema_class is None:
            return self._serialize_instance(instance)

        fk_names = self._fk_names()
        data: dict[str, Any] = {}
        for field_name in schema_class.model_fields:
            if field_name in self._declared_fields:
                field = self._declared_fields[field_name]
                if isinstance(field, SerializerMethodField):
                    method = getattr(self, field.method_name, None)
                    if method:
                        result = method(instance)
                        if _needs_await(result):
                            result = await result
                        data[field_name] = result
                    continue
                if isinstance(field, NestedSerializer):
                    data[field_name] = await self._serialize_nested(
                        field, instance, field_name
                    )
                    continue

            if field_name in self._old_style_fields:
                value = await self._source_value(instance, field_name)
                if value is not None:
                    data[field_name] = value
                continue

            if field_name in rel_kinds:
                pks = await self._resolve_related_pks(
                    getattr(instance, field_name, None)
                )
                if rel_kinds[field_name] == "reverse_o2o":
                    data[field_name] = pks[0] if pks else None
                else:
                    data[field_name] = pks
                continue

            if field_name in fk_names:
                value = getattr(instance, f"{field_name}_id", None)
            elif field_name.endswith("_id") and field_name[:-3] in fk_names:
                value = getattr(instance, field_name, None)
            else:
                value = getattr(instance, field_name, None)
                if isinstance(value, ForeignKeyLazyLoader):
                    value = value._fk_id
            if value is not None:
                data[field_name] = value

        return data

    def _source_parts(self, field_name: str) -> list[str]:
        field = self._old_style_fields[field_name]
        source = getattr(field, "source", None) or field_name
        return source.split(".")

    def _source_value_sync(self, instance: Any, field_name: str) -> Any:
        """A declared field's value from its ``source`` without awaiting."""
        from zeeb_orm.exceptions import NotSupportedError
        from zeeb_orm.models.fields import ForeignKeyLazyLoader

        value = instance
        for part in self._source_parts(field_name):
            if value is None:
                return None
            value = getattr(value, part, None)
            if isinstance(value, ForeignKeyLazyLoader):
                raise NotSupportedError(
                    f"Field '{field_name}' reads through an unloaded relation; "
                    "use 'await serializer.adata()' or select_related()."
                )
        return value

    async def _source_value(self, instance: Any, field_name: str) -> Any:
        """A declared field's value from its ``source``, loading relations."""
        from zeeb_orm.models.fields import ForeignKeyLazyLoader

        value = instance
        for part in self._source_parts(field_name):
            if value is None:
                return None
            value = getattr(value, part, None)
            if isinstance(value, ForeignKeyLazyLoader):
                value = await value
        return value

    async def _resolve_related_objects(self, value: Any) -> list[Any]:
        """Resolve a relation attribute to a list of related model instances."""
        from zeeb_orm.models.fields import ForeignKeyLazyLoader
        from zeeb_orm.models.manager import Manager

        if value is None:
            return []
        if isinstance(value, ForeignKeyLazyLoader):
            obj = await value
            return [obj] if obj is not None else []
        if isinstance(value, (list, tuple)):
            return list(value)
        if isinstance(value, Manager):
            return list(await value.all())
        return [value]

    async def _resolve_related_pks(self, value: Any) -> list[Any]:
        """Resolve a relation attribute to a list of related primary keys."""
        objs = await self._resolve_related_objects(value)
        return [getattr(o, "pk", o) for o in objs]

    async def _serialize_nested(
        self, field: NestedSerializer, instance: Any, field_name: str
    ) -> Any:
        """Serialize a NestedSerializer field, resolving the relation async."""
        objs = await self._resolve_related_objects(
            getattr(instance, field_name, None)
        )
        nested_cls = field.serializer_class
        if field.many:
            out = []
            for obj in objs:
                out.append(await nested_cls(instance=obj).adata())
            return out
        if not objs:
            return None
        return await nested_cls(instance=objs[0]).adata()

    @property
    def validated_data(self) -> dict[str, Any]:
        """Get validated data."""
        if self._validated_data is None:
            raise AssertionError("Call .is_valid() before accessing .validated_data")
        return self._validated_data
    
    @property
    def errors(self) -> dict[str, list[str]]:
        """Get validation errors."""
        return self._errors
    
    def is_valid(self, *, raise_exception: bool = False) -> bool:
        """Validate input data: the Pydantic schema, then the DRF-style hooks.

        After the schema, ``validate_<field>(self, value)`` runs for every
        field present in the input (its return value replaces the value), then
        ``validate(self, attrs)`` on the whole dict (its return value becomes
        ``validated_data``). A hook rejects input by raising
        ``zeeb_api.exceptions.ValidationError`` (or ``ValueError``). Async hooks
        need :meth:`ais_valid`, which the viewsets use.
        """
        if not self._validate_schema():
            return self._fail(raise_exception)
        try:
            self._run_hooks_sync()
        except _HookErrors as errors:
            self._validated_data = None
            self._errors = errors.errors
            return self._fail(raise_exception)
        return True

    async def ais_valid(self, *, raise_exception: bool = False) -> bool:
        """:meth:`is_valid` that also awaits ``async def`` validation hooks."""
        if not self._validate_schema():
            return self._fail(raise_exception)
        try:
            await self._run_hooks_async()
        except _HookErrors as errors:
            self._validated_data = None
            self._errors = errors.errors
            return self._fail(raise_exception)
        return True

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Object-level validation hook (DRF). Override; return the attrs."""
        return attrs

    def _fail(self, raise_exception: bool) -> bool:
        if raise_exception:
            from zeeb_api.exceptions import ValidationError

            raise ValidationError(self._errors)
        return False

    def _validate_schema(self) -> bool:
        """Pydantic validation of ``initial_data``; sets data or errors."""
        self._errors = {}
        if self.initial_data is None:
            self._validated_data = {}
            return True
        
        schema_class = self.RequestSchema or self.Schema
        if schema_class is None:
            self._validated_data = self.initial_data
            return True

        # Normalize bare foreign-key names (e.g. "author") to their canonical
        # request key ("author_id"). The partial branch below builds data via
        # model_construct + a model_fields filter, which bypasses pydantic
        # validation aliases; without this a bare FK key on PATCH is silently
        # dropped. Canonical key wins when both are present.
        initial_data = self.initial_data
        if self._fk_request_aliases and isinstance(initial_data, dict):
            normalized = dict(initial_data)
            for bare_name, canonical in self._fk_request_aliases.items():
                if bare_name in normalized and canonical not in normalized:
                    normalized[canonical] = normalized.pop(bare_name)
            initial_data = normalized

        try:
            # Validate with Pydantic
            if self.partial:
                # PATCH: validate ONLY the provided fields against their real
                # types/constraints (not model_construct, which skips validation
                # entirely and let e.g. {"age": "abc"} through). The all-optional
                # PartialRequestSchema allows missing fields; a value with the
                # wrong type is still rejected.
                partial_schema = self.PartialRequestSchema or schema_class
                validated = partial_schema.model_validate(initial_data)
            else:
                validated = schema_class.model_validate(initial_data)
            # Return exactly the keys the caller supplied (validated/coerced).
            # Fields the caller omitted are excluded so model-level defaults apply
            # on create instead of being overwritten with an explicit None, and
            # PATCH touches only what was sent.
            self._validated_data = validated.model_dump(exclude_unset=True)
            return True
            
        except Exception as e:
            self._validated_data = None
            # Extract Pydantic validation errors, keyed by their full path
            # ("address.zip", "items.0.name") - not just the top-level field.
            if hasattr(e, "errors"):
                for error in e.errors():
                    loc = error.get("loc") or ()
                    field = ".".join(str(part) for part in loc) if loc else "non_field_errors"
                    self._errors.setdefault(field, []).append(error["msg"])
            else:
                self._errors = {"non_field_errors": [str(e)]}
            return False

    def _field_hooks(self) -> list[tuple[str, Callable[[Any], Any]]]:
        """``(key, validate_<field>)`` for each validated key that has a hook."""
        bare_names = {canonical: bare for bare, canonical in self._fk_request_aliases.items()}
        hooks = []
        for key in list(self._validated_data or {}):
            hook = getattr(self, f"validate_{key}", None)
            if hook is None and key in bare_names:
                hook = getattr(self, f"validate_{bare_names[key]}", None)
            if callable(hook):
                hooks.append((key, hook))
        return hooks

    def _to_source_keys(self, data: dict[str, Any]) -> dict[str, Any]:
        """Key writable declared fields by their ``source`` (DRF).

        ``nick = CharField(source="nickname")`` validates as ``nick`` and is
        saved as ``nickname`` - the model has no ``nick`` to set. Dotted
        sources are read paths only and stay under the field name.
        """
        for name, field in self._old_style_fields.items():
            source = getattr(field, "source", None)
            if name in data and source and source != name and "." not in source:
                data[source] = data.pop(name)
        return data

    def _run_hooks_sync(self) -> None:
        errors: dict[str, list[str]] = {}
        data = dict(self._validated_data or {})
        for key, hook in self._field_hooks():
            try:
                result = hook(data[key])
            except Exception as exc:
                _collect_hook_error(errors, key, exc)
                continue
            if _needs_await(result):
                _discard(result)
                raise TypeError(
                    f"{type(self).__name__}.validate_{key}() is async; call "
                    "'await serializer.ais_valid()' instead of is_valid()"
                )
            data[key] = result
        if errors:
            raise _HookErrors(errors)
        data = self._to_source_keys(data)
        try:
            result = self.validate(data)
        except Exception as exc:
            _collect_hook_error(errors, None, exc)
            raise _HookErrors(errors) from None
        if _needs_await(result):
            _discard(result)
            raise TypeError(
                f"{type(self).__name__}.validate() is async; call "
                "'await serializer.ais_valid()' instead of is_valid()"
            )
        self._validated_data = data if result is None else dict(result)

    async def _run_hooks_async(self) -> None:
        errors: dict[str, list[str]] = {}
        data = dict(self._validated_data or {})
        for key, hook in self._field_hooks():
            try:
                result = hook(data[key])
                if _needs_await(result):
                    result = await result
            except Exception as exc:
                _collect_hook_error(errors, key, exc)
                continue
            data[key] = result
        if errors:
            raise _HookErrors(errors)
        data = self._to_source_keys(data)
        try:
            result = self.validate(data)
            if _needs_await(result):
                result = await result
        except Exception as exc:
            _collect_hook_error(errors, None, exc)
            raise _HookErrors(errors) from None
        self._validated_data = data if result is None else dict(result)
    
    async def save(self, **kwargs: Any) -> Any:
        """Save validated data.

        Extra ``kwargs`` (e.g. ``save(author_id=user.id)`` from a
        ``perform_create`` hook) are merged over the validated data. Bare
        foreign-key names and model instances passed as kwargs are normalized
        the same way request input is (``author=user`` -> ``author_id=user.pk``)
        so the create and update paths behave identically.

        Sets ``self.instance`` to the saved row so the caller and a second
        ``save()`` operate on it (a repeat call updates rather than inserting a
        duplicate), and marks the serializer saved for the viewset mixins.
        """
        if self._validated_data is None:
            raise AssertionError("Call .is_valid() before calling .save()")

        validated = {**self._validated_data, **self._normalize_save_kwargs(kwargs)}

        if self.instance is not None:
            self.instance = await self.update(self.instance, validated)
        else:
            self.instance = await self.create(validated)
        self._saved = True
        return self.instance

    def _normalize_save_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Normalize ``save(**kwargs)`` FK values to canonical ``<name>_id`` pks.

        Mirrors the request-input aliasing in ``is_valid`` (bare FK name ->
        ``<name>_id``, canonical key wins on collision) and additionally coerces
        a passed model instance to its primary key, so a hook may write
        ``save(author=user)``, ``save(author=user.id)``, ``save(author_id=user)``
        or ``save(author_id=user.id)`` interchangeably. No-op for a plain
        ``Serializer`` (its ``_fk_request_aliases`` is empty).
        """
        if not kwargs:
            return kwargs
        normalized = dict(kwargs)
        for bare_name, canonical in self._fk_request_aliases.items():
            if bare_name in normalized and canonical not in normalized:
                normalized[canonical] = normalized.pop(bare_name)
        for canonical in set(self._fk_request_aliases.values()):
            value = normalized.get(canonical)
            if value is not None and hasattr(value, "pk"):
                normalized[canonical] = value.pk
        return normalized
    
    async def create(self, validated_data: dict[str, Any]) -> Any:
        """Create new instance. Override in subclass."""
        raise NotImplementedError()
    
    async def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        """Update existing instance. Override in subclass."""
        raise NotImplementedError()


# =============================================================================
# MODEL SERIALIZER
# =============================================================================

class ModelSerializer(Serializer, Generic[ModelT]):
    """
    Serializer for Zeeb ORM models with automatic Pydantic schema generation.
    
    Usage:
        class UserSerializer(ModelSerializer):
            full_name = SerializerMethodField()
            
            class Meta:
                model = User
                fields = ["id", "name", "email", "full_name", "created_at"]
                read_only_fields = ["id", "created_at"]
            
            def get_full_name(self, obj) -> str:
                return f"{obj.first_name} {obj.last_name}"
    """
    
    class Meta:
        model: type | None = None
        fields: list[str] | str = "__all__"
        exclude: list[str] = []
        read_only_fields: list[str] = []
        extra_kwargs: dict[str, dict[str, Any]] = {}
    
    @classmethod
    def _generate_schemas(cls) -> None:
        """Generate Pydantic Request and Response schemas from model."""
        meta = cls.Meta
        model = meta.model
        
        if model is None:
            return
        
        # Get model fields
        model_fields = {}
        if hasattr(model, "_meta") and hasattr(model._meta, "local_fields"):
            for f in model._meta.local_fields:
                model_fields[f.name] = f
        
        # Determine which fields to include
        field_names = meta.fields
        if field_names == "__all__":
            field_names = list(model_fields.keys())
        
        exclude = getattr(meta, "exclude", [])
        field_names = [f for f in field_names if f not in exclude]

        # Declared fields: "__all__" includes them (DRF); an explicit list
        # must name every DRF-style field declared on the class, which used
        # to be ignored silently.
        if meta.fields == "__all__":
            for name in [*cls._declared_fields, *cls._old_style_fields]:
                if name not in field_names and name not in exclude:
                    field_names.append(name)
        else:
            unlisted = [
                name
                for name in cls._old_style_fields
                if name not in field_names and name not in exclude
            ]
            if unlisted:
                from zeeb_api.exceptions import ImproperlyConfigured

                raise ImproperlyConfigured(
                    f"{cls.__name__} declares field(s) {', '.join(unlisted)} "
                    "that Meta.fields does not include; add them to "
                    "Meta.fields (or to Meta.exclude)."
                )
        
        read_only = set(getattr(meta, "read_only_fields", []))
        extra_kwargs = getattr(meta, "extra_kwargs", {}) or {}
        cls._check_extra_kwargs(extra_kwargs)
        
        # Build field definitions
        response_fields: dict[str, tuple[type, Any]] = {}
        request_fields: dict[str, tuple[type, Any]] = {}
        # Maps a foreign key's bare model name (e.g. "author") to its canonical
        # request key ("author_id"). Consumed by Serializer.is_valid to normalize
        # bare FK names on the partial/PATCH path, which bypasses aliases.
        fk_request_aliases: dict[str, str] = {}

        # Forward M2M fields declared on the model. Auto-through M2Ms are
        # writable (as lists of target PKs) via the related manager's set();
        # custom-through M2Ms stay read-only (set() raises NotSupportedError).
        m2m_fields = {f.name: f for f in getattr(model, "_m2m_fields", [])}
        writable_m2m: list[str] = []

        for field_name in field_names:
            # Check for SerializerMethodField
            if field_name in cls._declared_fields:
                declared = cls._declared_fields[field_name]
                if isinstance(declared, SerializerMethodField):
                    # Add to response only
                    response_fields[field_name] = (declared.return_type, ...)
                    continue
                elif isinstance(declared, NestedSerializer):
                    # Nested serializer
                    nested_response = declared.serializer_class.ResponseSchema
                    if nested_response:
                        if declared.many:
                            response_fields[field_name] = (list[nested_response], ...)
                        else:
                            response_fields[field_name] = (nested_response | None, None)
                    continue

            # A DRF-style field declared on the class overrides the model
            # field of the same name (read_only/write_only/required/default/
            # validators all apply).
            if field_name in cls._old_style_fields:
                response_entry, request_entry = cls._declared_field_entries(
                    cls._old_style_fields[field_name]
                )
                if response_entry is not None:
                    response_fields[field_name] = response_entry
                if request_entry is not None and field_name not in read_only:
                    request_fields[field_name] = request_entry
                continue
            
            from zeeb_orm.models.fields import ForeignKeyField

            # Get model field
            # Handle the case where user specifies "field_id" for a FK named "field"
            model_field = model_fields.get(field_name)
            actual_field_name = field_name

            # If field not found, check if this is a "_id" reference to a FK
            if model_field is None and field_name.endswith("_id"):
                fk_name = field_name[:-3]  # Remove "_id" suffix
                model_field = model_fields.get(fk_name)
                if isinstance(model_field, ForeignKeyField):
                    actual_field_name = fk_name
                else:
                    model_field = None

            if model_field is None:
                # Not a local column: it may be a to-many / reverse relation
                # (forward M2M, reverse FK/O2O, reverse M2M). Serialize those as
                # related primary keys.
                rel_type = _relation_response_type(model, field_name)
                if rel_type is not None:
                    response_fields[field_name] = rel_type
                m2m_field = m2m_fields.get(field_name)
                if (
                    m2m_field is not None
                    and not m2m_field.has_custom_through
                    and field_name not in read_only
                ):
                    # Accept a list of target PKs on write.
                    target_pk_type: type = Any  # type: ignore[assignment]
                    try:
                        target_pk = m2m_field.get_target_model()._meta.pk
                        target_pk_type = getattr(target_pk, "_python_type", Any)
                    except Exception:
                        pass
                    request_fields[field_name] = (list[target_pk_type], None)
                    writable_m2m.append(field_name)
                continue

            # Determine Python type
            field_class_name = model_field.__class__.__name__
            python_type = ORM_TO_PYTHON_TYPE.get(field_class_name, Any)

            # Check if this is a ForeignKey. isinstance also covers
            # OneToOneField, whose class name does not contain "foreign".
            is_foreign_key = isinstance(model_field, ForeignKeyField)
            
            # Handle ForeignKey - get the target model's PK type
            if is_foreign_key:
                # Get target model's PK type
                target_model = None
                if hasattr(model_field, 'get_target_model'):
                    try:
                        target_model = model_field.get_target_model()
                    except Exception:
                        pass
                
                if target_model and hasattr(target_model, '_meta') and target_model._meta.pk:
                    target_pk = target_model._meta.pk
                    target_pk_type = getattr(target_pk, '_python_type', int)
                    python_type = target_pk_type
                else:
                    python_type = uuid.UUID  # Default to UUID (new default)
                
                # Keep the field name as user specified (could be "author" or "author_id")
                # For request, always use _id suffix
                if field_name.endswith("_id"):
                    field_name_for_request = field_name
                else:
                    field_name_for_request = f"{field_name}_id"
            else:
                field_name_for_request = field_name
            
            # Determine if nullable
            is_nullable = getattr(model_field, "null", False)
            if is_nullable:
                python_type = python_type | None
            
            # Get extra kwargs (keyed by the name used in Meta.fields)
            field_extra = extra_kwargs.get(field_name) or extra_kwargs.get(
                actual_field_name, {}
            )
            
            # Determine if read-only
            is_read_only = (
                field_name in read_only or
                bool(field_extra.get("read_only")) or
                getattr(model_field, "primary_key", False) or
                getattr(model_field, "auto_now", False) or
                getattr(model_field, "auto_now_add", False)
            )
            is_write_only = bool(field_extra.get("write_only"))
            
            # Build Field info
            default = ...  # Required
            if is_nullable:
                default = None
            if hasattr(model_field, "default") and model_field.default is not None:
                if not callable(model_field.default):
                    default = model_field.default
            if "default" in field_extra:
                default = field_extra["default"]
            if field_extra.get("allow_null") and not is_nullable:
                is_nullable = True
                python_type = python_type | None

            # extra_kwargs "required" decides the request side: True makes
            # the field mandatory even with a model default, False optional.
            request_default = default
            required = field_extra.get("required")
            if required is True:
                request_default = ...
            elif required is False and request_default is ...:
                request_default = None

            # Description and string constraints for the request field
            description = field_extra.get("help_text", None)
            constraints = {
                key: field_extra[key] for key in ("max_length", "min_length") if key in field_extra
            }
            
            # Add to response schema (unless write-only)
            if not is_write_only:
                response_fields[field_name] = (
                    python_type,
                    Field(
                        default=default if default is not ... else None,
                        description=description,
                    ),
                )
            
            # Add to request schema (if not read-only)
            if not is_read_only:
                # For FK, use the _id version in request with target PK type
                if is_foreign_key:
                    # Use the same python_type we determined above (already includes nullable)
                    base_type = python_type.__args__[0] if hasattr(python_type, '__args__') else python_type
                    fk_type = base_type | None if is_nullable else base_type
                    # Accept the bare relationship name (e.g. "author") as an
                    # alias for the canonical "<name>_id" request key. GET
                    # responses expose the bare name, so a client echoing a
                    # response back into a create/update would otherwise fail
                    # with a spurious "field required" for "<name>_id".
                    if field_name_for_request != actual_field_name:
                        fk_request_aliases[actual_field_name] = field_name_for_request
                        request_fields[field_name_for_request] = (
                            fk_type,
                            Field(
                                default=request_default,
                                description=description,
                                validation_alias=AliasChoices(
                                    field_name_for_request, actual_field_name
                                ),
                            ),
                        )
                    else:
                        request_fields[field_name_for_request] = (
                            fk_type,
                            Field(default=request_default, description=description),
                        )
                else:
                    request_type = python_type
                    if required is False and type(None) not in getattr(
                        python_type, "__args__", ()
                    ):
                        request_type = python_type | None
                    request_fields[field_name] = (
                        request_type,
                        Field(default=request_default, description=description, **constraints),
                    )
        
        # Create Pydantic models dynamically
        cls.ResponseSchema = _build_model(
            f"{cls.__name__}Response", response_fields, from_attributes=True
        )
        cls.RequestSchema = _build_model(f"{cls.__name__}Request", request_fields)

        # Partial request schema (for PATCH): every field optional. Preserves FK
        # validation aliases so the bare relationship name is accepted, and lets
        # the writable field set appear in OpenAPI for PATCH without marking
        # anything required.
        cls.PartialRequestSchema = _build_model(
            f"{cls.__name__}PartialRequest", _all_optional(request_fields)
        )

        # Default Schema is ResponseSchema
        cls.Schema = cls.ResponseSchema

        # M2M fields (list-of-PKs) applied after create/update via .set()
        cls._writable_m2m = writable_m2m

        # Bare-FK-name -> canonical-request-key map (see is_valid).
        cls._fk_request_aliases = fk_request_aliases

    #: Keys ``Meta.extra_kwargs`` understands per field.
    EXTRA_KWARGS_OPTIONS: ClassVar[frozenset[str]] = frozenset(
        {
            "read_only",
            "write_only",
            "required",
            "allow_null",
            "default",
            "help_text",
            "max_length",
            "min_length",
        }
    )

    @classmethod
    def _check_extra_kwargs(cls, extra_kwargs: dict[str, dict[str, Any]]) -> None:
        """Warn about ``extra_kwargs`` options that have no effect."""
        import warnings

        for name, options in extra_kwargs.items():
            unknown = sorted(set(options or {}) - cls.EXTRA_KWARGS_OPTIONS)
            if unknown:
                warnings.warn(
                    f"{cls.__name__}.Meta.extra_kwargs[{name!r}]: unsupported "
                    f"option(s) {', '.join(unknown)} are ignored; supported: "
                    f"{', '.join(sorted(cls.EXTRA_KWARGS_OPTIONS))}",
                    UserWarning,
                    stacklevel=4,
                )

    def _pop_m2m_values(self, validated_data: dict[str, Any]) -> dict[str, list[Any]]:
        """Extract writable M2M values; they can't go through objects.create()."""
        m2m_values: dict[str, list[Any]] = {}
        for name in getattr(self, "_writable_m2m", []):
            value = validated_data.pop(name, None)
            if value is not None:
                m2m_values[name] = value
        return m2m_values

    async def _apply_m2m(self, instance: ModelT, m2m_values: dict[str, list[Any]]) -> None:
        for name, pks in m2m_values.items():
            await getattr(instance, name).set(pks)

    async def create(self, validated_data: dict[str, Any]) -> ModelT:
        """Create new model instance."""
        model = self.Meta.model
        if model is None:
            raise ValueError("Meta.model is required")

        m2m_values = self._pop_m2m_values(validated_data)
        instance = await model.objects.create(**validated_data)
        await self._apply_m2m(instance, m2m_values)
        return instance

    async def update(self, instance: ModelT, validated_data: dict[str, Any]) -> ModelT:
        """Update existing model instance."""
        m2m_values = self._pop_m2m_values(validated_data)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        await instance.save()
        await self._apply_m2m(instance, m2m_values)
        return instance


# =============================================================================
# LIST RESPONSE SCHEMA
# =============================================================================

def create_list_response_schema(
    item_schema: type[BaseModel],
    name: str | None = None,
) -> type[BaseModel]:
    """
    Create a paginated list response schema.
    
    Returns schema like:
    {
        "count": 100,
        "next": "http://...",
        "previous": "http://...",
        "results": [...]
    }
    """
    schema_name = name or f"{item_schema.__name__}List"
    
    return type(
        schema_name,
        (BaseModel,),
        {
            "__annotations__": {
                "count": int | None,
                "next": str | None,
                "previous": str | None,
                "results": list[item_schema],
            },
            "count": Field(
                default=None,
                description="Total number of items (null under cursor pagination)",
            ),
            "next": Field(default=None, description="URL to next page"),
            "previous": Field(default=None, description="URL to previous page"),
            "results": Field(description="List of items"),
        },
    )
