"""
Serializers - DRF-like serialization for Zeeb ORM.

Provides Serializer and ModelSerializer classes for:
- Data validation (Pydantic-based)
- Object serialization/deserialization
- Nested relationships
- FastAPI schema generation
"""

from zeeb_api.serializers.fields import (
    BooleanField,
    CharField,
    DateField,
    DateTimeField,
    DecimalField,
    DictField,
    EmailField,
    Field,
    FloatField,
    IntegerField,
    ListField,
    SlugRelatedField,
    TimeField,
    URLField,
    UUIDField,
)
from zeeb_api.serializers.pydantic import (
    ModelSerializer,
    NestedSerializer,
    PrimaryKeyRelatedField,
    Serializer,
    SerializerMethodField,
    create_list_response_schema,
)

__all__ = [
    # Pydantic-based serializers
    "Serializer",
    "ModelSerializer",
    "SerializerMethodField",
    "PrimaryKeyRelatedField",
    "NestedSerializer",
    "create_list_response_schema",
    # Fields (for manual use)
    "Field",
    "CharField",
    "IntegerField",
    "FloatField",
    "DecimalField",
    "BooleanField",
    "DateTimeField",
    "DateField",
    "TimeField",
    "EmailField",
    "URLField",
    "UUIDField",
    "ListField",
    "DictField",
    "SlugRelatedField",
]
