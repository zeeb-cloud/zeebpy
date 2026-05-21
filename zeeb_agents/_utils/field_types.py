"""Field type mapping and field-line rendering helpers."""

from __future__ import annotations

# Friendly aliases → zeeb_orm field class names
FIELD_TYPE_MAP: dict[str, str] = {
    # String
    "string": "CharField",
    "str": "CharField",
    "char": "CharField",
    "varchar": "CharField",
    "CharField": "CharField",
    "text": "TextField",
    "TextField": "TextField",
    "email": "EmailField",
    "EmailField": "EmailField",
    "url": "URLField",
    "URLField": "URLField",
    "slug": "SlugField",
    "SlugField": "SlugField",
    # Numeric
    "int": "IntegerField",
    "integer": "IntegerField",
    "IntegerField": "IntegerField",
    "smallint": "SmallIntegerField",
    "SmallIntegerField": "SmallIntegerField",
    "bigint": "BigIntegerField",
    "BigIntegerField": "BigIntegerField",
    "posint": "PositiveIntegerField",
    "PositiveIntegerField": "PositiveIntegerField",
    "float": "FloatField",
    "FloatField": "FloatField",
    "decimal": "DecimalField",
    "DecimalField": "DecimalField",
    # Boolean
    "bool": "BooleanField",
    "boolean": "BooleanField",
    "BooleanField": "BooleanField",
    # Date / time
    "date": "DateField",
    "DateField": "DateField",
    "datetime": "DateTimeField",
    "timestamp": "DateTimeField",
    "DateTimeField": "DateTimeField",
    "time": "TimeField",
    "TimeField": "TimeField",
    # Other
    "json": "JSONField",
    "JSONField": "JSONField",
    "uuid": "UUIDField",
    "UUIDField": "UUIDField",
    "auto": "AutoField",
    "AutoField": "AutoField",
    "bigauto": "BigAutoField",
    "BigAutoField": "BigAutoField",
    "uuidauto": "UUIDAutoField",
    "UUIDAutoField": "UUIDAutoField",
    # Relations
    "fk": "ForeignKey",
    "foreignkey": "ForeignKey",
    "ForeignKey": "ForeignKey",
    "o2o": "OneToOneField",
    "onetoone": "OneToOneField",
    "OneToOneField": "OneToOneField",
    "m2m": "ManyToManyField",
    "manytomany": "ManyToManyField",
    "ManyToManyField": "ManyToManyField",
}

RELATION_FIELDS = {"ForeignKey", "OneToOneField", "ManyToManyField"}


def resolve_field_type(type_hint: str) -> str:
    """Resolve a friendly type hint to a zeeb_orm field class name."""
    resolved = FIELD_TYPE_MAP.get(type_hint) or FIELD_TYPE_MAP.get(type_hint.lower())
    if resolved is None:
        raise ValueError(
            f"Unknown field type '{type_hint}'. "
            f"Valid types: {sorted(set(FIELD_TYPE_MAP.values()))}"
        )
    return resolved


def render_field_line(field: dict) -> str:
    """Convert a field spec dict to a ``name = fields.X(...)`` assignment string.

    The dict must have at least ``name`` and ``type`` keys.  All other keys are
    passed as keyword arguments to the field constructor.

    Special handling:
    - Relation fields (ForeignKey etc.) take ``to`` as first positional arg.
    - ``on_delete`` values are rendered without quotes (e.g. ``on_delete="CASCADE"``
      is rendered as a plain string per zeeb_orm convention).
    - String values are quoted; booleans/ints/None are not.
    """
    spec = dict(field)
    name = spec.pop("name")
    raw_type = spec.pop("type")
    field_type = resolve_field_type(raw_type)

    parts: list[str] = []

    # Relation fields: `to` is the first positional arg
    if field_type in RELATION_FIELDS:
        to_val = spec.pop("to", None)
        if to_val:
            parts.append(f'"{to_val}"')

    for key, val in spec.items():
        if val is None:
            parts.append(f"{key}=None")
        elif isinstance(val, bool):
            parts.append(f"{key}={val}")
        elif isinstance(val, (int, float)):
            parts.append(f"{key}={val}")
        else:
            parts.append(f'{key}="{val}"')

    return f"{name} = fields.{field_type}({', '.join(parts)})"
