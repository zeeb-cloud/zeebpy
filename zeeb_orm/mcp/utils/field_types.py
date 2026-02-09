"""Field type definitions for MCP code generation."""

from __future__ import annotations

from typing import Any


# Map simple type names to Zeeb field definitions
FIELD_TYPES = {
    # Basic types
    "string": "CharField",
    "char": "CharField",
    "text": "TextField",
    "integer": "IntegerField",
    "int": "IntegerField",
    "bigint": "BigIntegerField",
    "smallint": "SmallIntegerField",
    "float": "FloatField",
    "decimal": "DecimalField",
    "boolean": "BooleanField",
    "bool": "BooleanField",
    
    # Date/Time
    "datetime": "DateTimeField",
    "date": "DateField",
    "time": "TimeField",
    
    # Special strings
    "email": "EmailField",
    "url": "URLField",
    "slug": "SlugField",
    
    # Complex types
    "uuid": "UUIDField",
    "json": "JSONField",
    
    # Relationships
    "foreign_key": "ForeignKey",
    "fk": "ForeignKey",
    "one_to_one": "OneToOneField",
    "o2o": "OneToOneField",
    "many_to_many": "ManyToManyField",
    "m2m": "ManyToManyField",
}

# Default options for specific field types
FIELD_DEFAULTS = {
    "CharField": {"max_length": 255},
    "DecimalField": {"max_digits": 10, "decimal_places": 2},
    "ForeignKey": {"on_delete": "'CASCADE'"},
    "OneToOneField": {"on_delete": "'CASCADE'"},
}

# Map option names to field option syntax
FIELD_OPTIONS = {
    "null": "null={value}",
    "blank": "blank={value}",
    "unique": "unique={value}",
    "index": "db_index={value}",
    "default": "default={value}",
    "max_length": "max_length={value}",
    "max_digits": "max_digits={value}",
    "decimal_places": "decimal_places={value}",
    "choices": "choices={value}",
    "help_text": "help_text='{value}'",
    "verbose_name": "verbose_name='{value}'",
    "related_name": "related_name='{value}'",
    "on_delete": "on_delete={value}",
    "auto_now": "auto_now={value}",
    "auto_now_add": "auto_now_add={value}",
    "primary_key": "primary_key={value}",
    "to": None,  # Handled specially for relationships
}


def parse_field_definition(field_def: dict[str, Any]) -> str:
    """
    Parse a field definition dict into a Zeeb field string.
    
    Args:
        field_def: Dict with 'name', 'type', and optional field options
            Example: {"name": "title", "type": "string", "max_length": 200}
    
    Returns:
        Field definition string like "fields.CharField(max_length=200)"
    """
    field_name = field_def.get("name", "")
    field_type = field_def.get("type", "string").lower()
    
    # Get the Zeeb field class
    field_class = FIELD_TYPES.get(field_type, "CharField")
    
    # Build options list
    options = []
    
    # Handle relationship target
    if field_class in ("ForeignKey", "OneToOneField", "ManyToManyField"):
        target = field_def.get("to", field_def.get("target", ""))
        if target:
            # Check if it's a string reference or direct model
            if "." in target or target[0].isupper():
                options.append(f"'{target}'")
            else:
                options.append(f"'{target}'")
    
    # Add default options for field type
    defaults = FIELD_DEFAULTS.get(field_class, {})
    for key, default_value in defaults.items():
        if key not in field_def:
            field_def[key] = default_value
    
    # Process all options
    for key, value in field_def.items():
        if key in ("name", "type", "to", "target"):
            continue
        
        if key in FIELD_OPTIONS and FIELD_OPTIONS[key] is not None:
            # Format the option
            if isinstance(value, bool):
                formatted = FIELD_OPTIONS[key].format(value=str(value))
            elif isinstance(value, str) and key not in ("default", "on_delete", "choices"):
                formatted = FIELD_OPTIONS[key].format(value=value)
            else:
                formatted = FIELD_OPTIONS[key].format(value=value)
            options.append(formatted)
    
    # Build the field string
    options_str = ", ".join(options)
    return f"fields.{field_class}({options_str})"


def generate_field_line(field_name: str, field_def: dict[str, Any]) -> str:
    """Generate a complete field line for a model."""
    field_str = parse_field_definition(field_def)
    return f"    {field_name} = {field_str}"
