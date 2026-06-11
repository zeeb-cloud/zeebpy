"""Base serializer classes."""

from __future__ import annotations

from typing import Any, TypeVar, Generic, ClassVar, Sequence
from collections import OrderedDict

from zeeb_api.serializers.fields import (
    Field, CharField, IntegerField, FloatField, DecimalField,
    BooleanField, DateTimeField, DateField, EmailField, UUIDField,
    empty,
)
from zeeb_api.exceptions import ValidationError

ModelT = TypeVar("ModelT")


class SerializerMetaclass(type):
    """Metaclass to collect declared fields."""
    
    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
    ) -> SerializerMetaclass:
        # Collect fields from this class
        declared_fields: dict[str, Field] = {}
        
        for key, value in list(namespace.items()):
            if isinstance(value, Field):
                declared_fields[key] = value
        
        # Remove fields from namespace (they'll be accessed via _declared_fields)
        for key in declared_fields:
            namespace.pop(key, None)
        
        # Collect fields from base classes
        for base in bases:
            if hasattr(base, "_declared_fields"):
                parent_fields = getattr(base, "_declared_fields", {})
                for key, value in parent_fields.items():
                    if key not in declared_fields:
                        declared_fields[key] = value
        
        namespace["_declared_fields"] = declared_fields
        
        return super().__new__(mcs, name, bases, namespace)


class Serializer(metaclass=SerializerMetaclass):
    """
    Base serializer class - DRF-style serialization and validation.
    
    Usage:
        class UserSerializer(Serializer):
            name = CharField(max_length=100)
            email = EmailField()
            age = IntegerField(min_value=0)
        
        # Validation
        serializer = UserSerializer(data={"name": "Alice", "email": "alice@example.com"})
        if serializer.is_valid():
            data = serializer.validated_data
        else:
            errors = serializer.errors
        
        # Serialization
        serializer = UserSerializer(instance=user)
        data = serializer.data
    """
    
    _declared_fields: ClassVar[dict[str, Field]]
    
    def __init__(
        self,
        instance: Any = None,
        data: dict[str, Any] | type[empty] = empty,
        *,
        many: bool = False,
        partial: bool = False,
        context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.instance = instance
        self.initial_data = data
        self.many = many
        self.partial = partial
        self.context = context or {}
        
        self._validated_data: dict[str, Any] | type[empty] = empty
        self._errors: dict[str, list[str]] = {}
        self._data: dict[str, Any] | None = None
        
        # Bind fields
        self.fields = self._get_fields()
        for field_name, field in self.fields.items():
            field.bind(field_name, self)
    
    def _get_fields(self) -> dict[str, Field]:
        """Get all fields for this serializer."""
        return OrderedDict(self._declared_fields.copy())
    
    @property
    def data(self) -> dict[str, Any] | list[dict[str, Any]]:
        """Get serialized output data."""
        if self._data is not None:
            return self._data
        
        if self.instance is not None:
            if self.many:
                self._data = [self._to_representation(item) for item in self.instance]
            else:
                self._data = self._to_representation(self.instance)
        elif self._validated_data is not empty:
            self._data = self._validated_data
        else:
            self._data = {}
        
        return self._data
    
    def _to_representation(self, instance: Any) -> dict[str, Any]:
        """Convert instance to dict representation."""
        result = OrderedDict()
        
        for field_name, field in self.fields.items():
            if field.write_only:
                continue
            
            # Get value from instance
            source = field.source or field_name
            if source == "*":
                value = instance
            elif "." in source:
                # Handle nested source like "author.name"
                value = instance
                for attr in source.split("."):
                    value = getattr(value, attr, None)
                    if value is None:
                        break
            else:
                value = getattr(instance, source, None)
            
            # Convert to representation
            if value is not None:
                result[field_name] = field.to_representation(value)
            elif field.allow_null:
                result[field_name] = None
        
        return result
    
    @property
    def validated_data(self) -> dict[str, Any]:
        """Get validated data (must call is_valid() first)."""
        if self._validated_data is empty:
            raise AssertionError(
                "You must call `.is_valid()` before accessing `.validated_data`"
            )
        return self._validated_data  # type: ignore
    
    @property
    def errors(self) -> dict[str, list[str]]:
        """Get validation errors (must call is_valid() first)."""
        if self._validated_data is empty and not self._errors:
            raise AssertionError(
                "You must call `.is_valid()` before accessing `.errors`"
            )
        return self._errors
    
    def is_valid(self, *, raise_exception: bool = False) -> bool:
        """
        Validate input data.
        
        Returns True if data is valid, False otherwise.
        If raise_exception=True, raises ValidationError on invalid data.
        """
        if self.initial_data is empty:
            self._validated_data = {}
            return True
        
        try:
            self._validated_data = self._run_validation(self.initial_data)
            self._errors = {}
        except ValidationError as e:
            self._validated_data = {}
            self._errors = e.detail if isinstance(e.detail, dict) else {"non_field_errors": [str(e.detail)]}
            if raise_exception:
                raise
            return False
        
        return True
    
    def _run_validation(self, data: dict[str, Any]) -> dict[str, Any]:
        """Run validation on input data."""
        if not isinstance(data, dict):
            raise ValidationError({"non_field_errors": ["Expected a dictionary"]})
        
        validated = OrderedDict()
        errors = {}
        
        for field_name, field in self.fields.items():
            if field.read_only:
                continue
            
            # Get value from input data
            value = data.get(field_name, empty)
            
            # Skip validation for partial updates on missing fields
            if self.partial and value is empty:
                continue
            
            try:
                validated_value = field.run_validation(value)
                if validated_value is not empty:
                    validated[field_name] = validated_value
            except ValidationError as e:
                errors.update(e.detail if isinstance(e.detail, dict) else {field_name: [str(e.detail)]})
        
        # Run object-level validation
        if not errors:
            try:
                validated = self.validate(validated)
            except ValidationError as e:
                errors.update(e.detail if isinstance(e.detail, dict) else {"non_field_errors": [str(e.detail)]})
        
        if errors:
            raise ValidationError(errors)
        
        return validated
    
    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """
        Object-level validation. Override to add custom validation.
        
        Example:
            def validate(self, attrs):
                if attrs.get("start") > attrs.get("end"):
                    raise ValidationError({"end": ["End must be after start"]})
                return attrs
        """
        return attrs
    
    async def save(self, **kwargs: Any) -> Any:
        """
        Save validated data.
        
        Calls create() for new instances or update() for existing ones.
        """
        if self._validated_data is empty:
            raise AssertionError(
                "You must call `.is_valid()` before calling `.save()`"
            )
        
        validated_data = {**self.validated_data, **kwargs}
        
        if self.instance is not None:
            self.instance = await self.update(self.instance, validated_data)
        else:
            self.instance = await self.create(validated_data)
        
        return self.instance
    
    async def create(self, validated_data: dict[str, Any]) -> Any:
        """
        Create a new instance. Override in subclasses.
        """
        raise NotImplementedError("Subclasses must implement create()")
    
    async def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        """
        Update an existing instance. Override in subclasses.
        """
        raise NotImplementedError("Subclasses must implement update()")


class ModelSerializer(Serializer, Generic[ModelT]):
    """
    Serializer for Zeeb ORM models - auto-generates fields from model.
    
    Usage:
        class UserSerializer(ModelSerializer):
            class Meta:
                model = User
                fields = ["id", "name", "email", "created_at"]
                read_only_fields = ["created_at"]
        
        # Or use __all__ for all fields
        class UserSerializer(ModelSerializer):
            class Meta:
                model = User
                fields = "__all__"
    """
    
    class Meta:
        model: type | None = None
        fields: list[str] | str = []
        exclude: list[str] = []
        read_only_fields: list[str] = []
        extra_kwargs: dict[str, dict[str, Any]] = {}
        depth: int = 0
    
    # Map ORM field types to serializer field types
    FIELD_MAPPING = {
        "CharField": CharField,
        "TextField": CharField,
        "IntegerField": IntegerField,
        "BigIntegerField": IntegerField,
        "SmallIntegerField": IntegerField,
        "FloatField": FloatField,
        "DecimalField": DecimalField,
        "BooleanField": BooleanField,
        "DateTimeField": DateTimeField,
        "DateField": DateField,
        "EmailField": EmailField,
        "UUIDField": UUIDField,
        "AutoField": IntegerField,
    }
    
    def _get_fields(self) -> dict[str, Field]:
        """Get fields from declared fields and model."""
        fields = OrderedDict()
        
        # Get Meta options
        meta = getattr(self, "Meta", None)
        if meta is None:
            return super()._get_fields()
        
        model = getattr(meta, "model", None)
        if model is None:
            return super()._get_fields()
        
        field_names = getattr(meta, "fields", [])
        exclude = getattr(meta, "exclude", [])
        read_only_fields = getattr(meta, "read_only_fields", [])
        extra_kwargs = getattr(meta, "extra_kwargs", {})
        
        # Get all model field names
        model_fields = {}
        if hasattr(model, "_meta") and hasattr(model._meta, "local_fields"):
            for f in model._meta.local_fields:
                model_fields[f.name] = f
        
        # Determine which fields to include
        if field_names == "__all__":
            field_names = list(model_fields.keys())
        elif not field_names:
            field_names = []
        
        # Apply exclusions
        field_names = [f for f in field_names if f not in exclude]
        
        # Build fields
        for field_name in field_names:
            # Check for declared field first
            if field_name in self._declared_fields:
                fields[field_name] = self._declared_fields[field_name]
                continue
            
            # Get model field
            model_field = model_fields.get(field_name)
            if model_field is None:
                continue
            
            # Create serializer field
            serializer_field = self._build_field(
                model_field,
                field_name,
                read_only=field_name in read_only_fields,
                extra_kwargs=extra_kwargs.get(field_name, {}),
            )
            
            if serializer_field:
                fields[field_name] = serializer_field
        
        return fields
    
    def _build_field(
        self,
        model_field: Any,
        field_name: str,
        read_only: bool = False,
        extra_kwargs: dict[str, Any] | None = None,
    ) -> Field | None:
        """Build a serializer field from a model field."""
        extra_kwargs = extra_kwargs or {}
        
        # Get field class name
        field_class_name = model_field.__class__.__name__
        
        # Map to serializer field
        field_class = self.FIELD_MAPPING.get(field_class_name)
        if field_class is None:
            # Default to CharField for unknown types
            field_class = CharField
        
        # Build kwargs
        kwargs: dict[str, Any] = {}
        
        # Required
        if hasattr(model_field, "null"):
            kwargs["required"] = not model_field.null and not read_only
            kwargs["allow_null"] = model_field.null
        
        # Read-only
        if read_only or getattr(model_field, "primary_key", False):
            kwargs["read_only"] = True
        
        # Auto fields
        if getattr(model_field, "auto_now", False) or getattr(model_field, "auto_now_add", False):
            kwargs["read_only"] = True
        
        # Max length
        if hasattr(model_field, "max_length") and model_field.max_length:
            kwargs["max_length"] = model_field.max_length
        
        # Default
        if hasattr(model_field, "default") and model_field.default is not None:
            if not callable(model_field.default):
                kwargs["default"] = model_field.default
        
        # Apply extra kwargs
        kwargs.update(extra_kwargs)
        
        return field_class(**kwargs)
    
    async def create(self, validated_data: dict[str, Any]) -> ModelT:
        """Create a new model instance."""
        meta = getattr(self, "Meta", None)
        model = getattr(meta, "model", None) if meta else None
        
        if model is None:
            raise ValueError("ModelSerializer requires a model in Meta class")
        
        return await model.objects.create(**validated_data)
    
    async def update(self, instance: ModelT, validated_data: dict[str, Any]) -> ModelT:
        """Update an existing model instance."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        await instance.save()
        return instance
