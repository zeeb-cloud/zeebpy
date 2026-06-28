"""
Pydantic-based serializers with Django/DRF-style API.

Provides serializers that:
- Use Pydantic for validation and schema generation
- Keep Django/DRF-style Meta class and field declaration
- Auto-generate FastAPI response models
"""

from __future__ import annotations

from typing import (
    Any, TypeVar, Generic, ClassVar, Sequence, 
    get_type_hints, TYPE_CHECKING,
)
from datetime import datetime, date, time
from decimal import Decimal
from enum import Enum
import inspect
import uuid

from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from pydantic.fields import FieldInfo

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
    "FloatField": float,
    "DecimalField": Decimal,
    "BooleanField": bool,
    "DateTimeField": datetime,
    "DateField": date,
    "TimeField": time,
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
        
        # Collect declared fields (SerializerMethodField, etc.)
        declared_fields: dict[str, Any] = {}
        
        # Collect old-style field declarations
        old_style_fields: dict[str, Any] = {}
        
        for key, value in list(namespace.items()):
            if isinstance(value, (SerializerMethodField, PrimaryKeyRelatedField, NestedSerializer)):
                declared_fields[key] = value
                if isinstance(value, SerializerMethodField):
                    value.bind(key)
            elif isinstance(value, OldField):
                old_style_fields[key] = value
        
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
    
    # Pydantic schemas - set by metaclass or manually
    Schema: ClassVar[type[BaseModel] | None] = None
    RequestSchema: ClassVar[type[BaseModel] | None] = None
    ResponseSchema: ClassVar[type[BaseModel] | None] = None
    
    @classmethod
    def _generate_schema_from_fields(cls, fields: dict[str, Any]) -> None:
        """Generate Pydantic schemas from old-style field declarations."""
        from pydantic import EmailStr
        
        response_fields: dict[str, tuple[type, Any]] = {}
        request_fields: dict[str, tuple[type, Any]] = {}
        
        for field_name, field in fields.items():
            field_class_name = field.__class__.__name__
            
            # Special handling for EmailField to use Pydantic's EmailStr
            if field_class_name == "EmailField":
                python_type = EmailStr
            else:
                python_type = OLD_FIELD_TO_TYPE.get(field_class_name, Any)
            
            # Handle nullability
            if getattr(field, "allow_null", False):
                python_type = python_type | None
            
            # Determine if field is required
            # The old Field class has a 'required' property that checks:
            # - If _required is set explicitly, use that
            # - Otherwise, required if default is empty and not read_only
            is_required = getattr(field, "required", True)
            
            # If not required, make nullable and give None default
            if not is_required:
                python_type = python_type | None
                default_value = None
            else:
                # Check for explicit default
                default_attr = getattr(field, "default", None)
                if default_attr is not None:
                    # Check if it's the 'empty' sentinel class
                    if hasattr(default_attr, "__name__") and default_attr.__name__ == "empty":
                        default_value = ...  # Required
                    elif callable(default_attr):
                        default_value = ...  # Factory, mark as required (Pydantic will call it)
                    else:
                        default_value = default_attr
                else:
                    default_value = ...  # Required
            
            # Check read_only / write_only
            read_only = getattr(field, "read_only", False)
            write_only = getattr(field, "write_only", False)
            
            # Add to response schema (unless write_only)
            if not write_only:
                response_fields[field_name] = (python_type, default_value if default_value is not ... else None)
            
            # Add to request schema (unless read_only)
            if not read_only:
                request_fields[field_name] = (python_type, default_value)
        
        # Create Pydantic models
        if response_fields:
            cls.ResponseSchema = type(
                f"{cls.__name__}Response",
                (BaseModel,),
                {
                    "__annotations__": {k: v[0] for k, v in response_fields.items()},
                    **{k: Field(default=v[1]) for k, v in response_fields.items()},
                    "model_config": ConfigDict(from_attributes=True),
                },
            )
        
        if request_fields:
            cls.RequestSchema = type(
                f"{cls.__name__}Request",
                (BaseModel,),
                {
                    "__annotations__": {k: v[0] for k, v in request_fields.items()},
                    **{k: Field(default=v[1]) for k, v in request_fields.items()},
                },
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
                        if inspect.isawaitable(result):
                            raise NotSupportedError(
                                f"Async SerializerMethodField '{field_name}' "
                                "requires the async serializer path; use "
                                "'await serializer.adata()'."
                            )
                        data[field_name] = result
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
                        if inspect.isawaitable(result):
                            result = await result
                        data[field_name] = result
                    continue
                if isinstance(field, NestedSerializer):
                    data[field_name] = await self._serialize_nested(
                        field, instance, field_name
                    )
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
        """Validate input data using Pydantic schema."""
        if self.initial_data is None:
            self._validated_data = {}
            return True
        
        schema_class = self.RequestSchema or self.Schema
        if schema_class is None:
            self._validated_data = self.initial_data
            return True
        
        try:
            # Validate with Pydantic
            if self.partial:
                # For partial updates, only validate provided fields
                validated = schema_class.model_construct(**self.initial_data)
                self._validated_data = {
                    k: v for k, v in self.initial_data.items()
                    if k in schema_class.model_fields
                }
            else:
                validated = schema_class.model_validate(self.initial_data)
                self._validated_data = validated.model_dump()
            
            self._errors = {}
            return True
            
        except Exception as e:
            self._validated_data = None
            # Extract Pydantic validation errors
            if hasattr(e, "errors"):
                for error in e.errors():
                    field = error["loc"][0] if error["loc"] else "non_field_errors"
                    if field not in self._errors:
                        self._errors[field] = []
                    self._errors[field].append(error["msg"])
            else:
                self._errors = {"non_field_errors": [str(e)]}
            
            if raise_exception:
                from zeeb_api.exceptions import ValidationError
                raise ValidationError(self._errors)
            
            return False
    
    async def save(self, **kwargs: Any) -> Any:
        """Save validated data."""
        if self._validated_data is None:
            raise AssertionError("Call .is_valid() before calling .save()")
        
        validated = {**self._validated_data, **kwargs}
        
        if self.instance is not None:
            return await self.update(self.instance, validated)
        return await self.create(validated)
    
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
        
        read_only = set(getattr(meta, "read_only_fields", []))
        extra_kwargs = getattr(meta, "extra_kwargs", {})
        
        # Build field definitions
        response_fields: dict[str, tuple[type, Any]] = {}
        request_fields: dict[str, tuple[type, Any]] = {}
        
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
                # related primary keys (read-only).
                rel_type = _relation_response_type(model, field_name)
                if rel_type is not None:
                    response_fields[field_name] = rel_type
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
            
            # Get extra kwargs
            field_extra = extra_kwargs.get(field_name, {})
            
            # Determine if read-only
            is_read_only = (
                field_name in read_only or
                getattr(model_field, "primary_key", False) or
                getattr(model_field, "auto_now", False) or
                getattr(model_field, "auto_now_add", False)
            )
            
            # Build Field info
            default = ...  # Required
            if is_nullable:
                default = None
            if hasattr(model_field, "default") and model_field.default is not None:
                if not callable(model_field.default):
                    default = model_field.default
            
            # Description
            description = field_extra.get("help_text", None)
            
            # Add to response schema
            response_fields[field_name] = (python_type, default if default is not ... else None)
            
            # Add to request schema (if not read-only)
            if not is_read_only:
                # For FK, use the _id version in request with target PK type
                if is_foreign_key:
                    # Use the same python_type we determined above (already includes nullable)
                    base_type = python_type.__args__[0] if hasattr(python_type, '__args__') else python_type
                    request_fields[field_name_for_request] = (base_type | None if is_nullable else base_type, default)
                else:
                    request_fields[field_name] = (python_type, default)
        
        # Create Pydantic models dynamically
        model_name = model.__name__
        
        # Response schema (for output)
        cls.ResponseSchema = type(
            f"{cls.__name__}Response",
            (BaseModel,),
            {
                "__annotations__": {k: v[0] for k, v in response_fields.items()},
                **{k: Field(default=v[1]) for k, v in response_fields.items()},
                "model_config": ConfigDict(from_attributes=True),
            },
        )
        
        # Request schema (for input)
        cls.RequestSchema = type(
            f"{cls.__name__}Request",
            (BaseModel,),
            {
                "__annotations__": {k: v[0] for k, v in request_fields.items()},
                **{k: Field(default=v[1]) for k, v in request_fields.items()},
            },
        )
        
        # Default Schema is ResponseSchema
        cls.Schema = cls.ResponseSchema
    
    async def create(self, validated_data: dict[str, Any]) -> ModelT:
        """Create new model instance."""
        model = self.Meta.model
        if model is None:
            raise ValueError("Meta.model is required")
        
        return await model.objects.create(**validated_data)
    
    async def update(self, instance: ModelT, validated_data: dict[str, Any]) -> ModelT:
        """Update existing model instance."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        await instance.save()
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
                "count": int,
                "next": str | None,
                "previous": str | None,
                "results": list[item_schema],
            },
            "count": Field(description="Total number of items"),
            "next": Field(default=None, description="URL to next page"),
            "previous": Field(default=None, description="URL to previous page"),
            "results": Field(description="List of items"),
        },
    )
