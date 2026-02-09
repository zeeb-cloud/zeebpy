"""
Serializers - DRF-like serialization for Zeeb ORM.

Provides Serializer and ModelSerializer classes for:
- Data validation (Pydantic-based)
- Object serialization/deserialization
- Nested relationships
- FastAPI schema generation
"""

from zeeb_api.serializers.pydantic import (
    Serializer,
    ModelSerializer,
    SerializerMethodField,
    PrimaryKeyRelatedField,
    NestedSerializer,
    create_list_response_schema,
)
from zeeb_api.serializers.fields import (
    Field,
    CharField,
    IntegerField,
    FloatField,
    DecimalField,
    BooleanField,
    DateTimeField,
    DateField,
    TimeField,
    EmailField,
    URLField,
    UUIDField,
    ListField,
    DictField,
    SlugRelatedField,
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
