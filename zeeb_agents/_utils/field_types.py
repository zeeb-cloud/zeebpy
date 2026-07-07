"""Field type mapping, validation, and field-line rendering helpers.

This module is the canonical registry of field types the generators support.
Both up-front validation (:func:`validate_field_spec`) and code emission
(:func:`render_field_line`) share the same rules so they can never disagree.
"""

from __future__ import annotations

from zeeb_agents._utils.errors import AgentError, close_matches

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
    "possmallint": "PositiveSmallIntegerField",
    "PositiveSmallIntegerField": "PositiveSmallIntegerField",
    "posbigint": "PositiveBigIntegerField",
    "PositiveBigIntegerField": "PositiveBigIntegerField",
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
    "duration": "DurationField",
    "DurationField": "DurationField",
    # Other
    "json": "JSONField",
    "JSONField": "JSONField",
    "uuid": "UUIDField",
    "UUIDField": "UUIDField",
    "binary": "BinaryField",
    "bytes": "BinaryField",
    "BinaryField": "BinaryField",
    "ip": "GenericIPAddressField",
    "ipaddress": "GenericIPAddressField",
    "GenericIPAddressField": "GenericIPAddressField",
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

# Values accepted by zeeb_orm's on_delete (zeeb_orm/models/deletion.py — the
# constants are plain strings, so quoted rendering is the API).
ON_DELETE_VALUES = {"CASCADE", "PROTECT", "RESTRICT", "SET_NULL", "SET_DEFAULT", "DO_NOTHING"}

# Kwargs zeeb_orm's ManyToManyField.__init__ does not accept.
_M2M_REJECTED = {"on_delete", "null"}


def known_field_types() -> list[str]:
    """Return the sorted canonical zeeb_orm field class names."""
    return sorted(set(FIELD_TYPE_MAP.values()))


def resolve_field_type(type_hint: str) -> str:
    """Resolve a friendly type hint to a zeeb_orm field class name."""
    resolved = FIELD_TYPE_MAP.get(type_hint) or FIELD_TYPE_MAP.get(str(type_hint).lower())
    if resolved is None:
        suggestions = close_matches(str(type_hint), list(FIELD_TYPE_MAP))
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise AgentError(
            f"Unknown field type '{type_hint}'.{hint} "
            f"Valid types: {known_field_types()}",
            code="invalid_field_type",
            suggestions=suggestions,
        )
    return resolved


def render_py_literal(val: object) -> str:
    """Render *val* as Python source. Supports scalars, str, list, tuple, dict."""
    if isinstance(val, str):
        # Generated code style is double quotes; repr covers the tricky cases.
        if '"' not in val and "\\" not in val and "\n" not in val:
            return f'"{val}"'
        return repr(val)
    if val is None or isinstance(val, (bool, int, float)):
        return repr(val)
    if isinstance(val, list):
        return "[" + ", ".join(render_py_literal(v) for v in val) + "]"
    if isinstance(val, tuple):
        inner = ", ".join(render_py_literal(v) for v in val)
        return f"({inner},)" if len(val) == 1 else f"({inner})"
    if isinstance(val, dict):
        return (
            "{"
            + ", ".join(
                f"{render_py_literal(k)}: {render_py_literal(v)}" for k, v in val.items()
            )
            + "}"
        )
    raise AgentError(
        f"Cannot render a {type(val).__name__} value as a literal; "
        'use the "raw" key for arbitrary Python code '
        '(e.g. {"raw": {"default": "dict"}})',
        code="invalid_field_spec",
    )


def validate_field_spec(spec: object) -> tuple[str, str]:
    """Validate one field-spec dict; return ``(name, resolved_field_type)``.

    Raises :class:`AgentError` describing the first problem found.  Shares all
    rules with :func:`render_field_line` (which calls this first).
    """
    if not isinstance(spec, dict):
        raise AgentError(
            f"Field spec must be a dict, got {type(spec).__name__}",
            code="invalid_field_spec",
        )
    name = spec.get("name")
    if not isinstance(name, str) or not name.isidentifier():
        raise AgentError(
            f"Field spec needs a 'name' that is a valid identifier, got {name!r}",
            code="invalid_field_spec",
        )
    if "type" not in spec:
        raise AgentError(
            f"Field '{name}' is missing the 'type' key",
            code="invalid_field_spec",
        )
    field_type = resolve_field_type(spec["type"])

    raw = spec.get("raw")
    if raw is not None and (
        not isinstance(raw, dict)
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items())
    ):
        raise AgentError(
            f"Field '{name}': 'raw' must be a dict of kwarg name → Python source string",
            code="invalid_field_spec",
        )

    if field_type in RELATION_FIELDS:
        to_val = spec.get("to")
        if not to_val or not isinstance(to_val, str):
            raise AgentError(
                f"Field '{name}' ({field_type}) requires 'to' — the target model "
                "name (e.g. \"User\", \"accounts.User\" or \"self\")",
                code="invalid_field_spec",
            )
        if field_type == "ManyToManyField":
            rejected = _M2M_REJECTED & set(spec)
            if rejected:
                raise AgentError(
                    f"Field '{name}': ManyToManyField does not accept "
                    f"{', '.join(sorted(rejected))}",
                    code="invalid_field_spec",
                )
        else:
            on_delete = spec.get("on_delete")
            if on_delete is not None and on_delete not in ON_DELETE_VALUES:
                raise AgentError(
                    f"Field '{name}': invalid on_delete '{on_delete}'. "
                    f"Valid values: {sorted(ON_DELETE_VALUES)}",
                    code="invalid_field_spec",
                    suggestions=close_matches(str(on_delete), sorted(ON_DELETE_VALUES)),
                )
            if on_delete == "SET_NULL" and spec.get("null") is not True:
                raise AgentError(
                    f"Field '{name}': on_delete='SET_NULL' requires null=True",
                    code="invalid_field_spec",
                )
            if (
                on_delete == "SET_DEFAULT"
                and "default" not in spec
                and "default" not in (raw or {})
            ):
                raise AgentError(
                    f"Field '{name}': on_delete='SET_DEFAULT' requires a default",
                    code="invalid_field_spec",
                )
    elif "to" in spec:
        raise AgentError(
            f"Field '{name}': 'to' is only valid on relation fields "
            f"(ForeignKey, OneToOneField, ManyToManyField), not {field_type}",
            code="invalid_field_spec",
        )

    # Every non-raw kwarg must be renderable as a literal.
    for key, val in spec.items():
        if key in ("name", "type", "to", "raw"):
            continue
        render_py_literal(val)  # raises AgentError if not literal-safe

    return name, field_type


def render_field_line(field: dict) -> str:
    """Convert a field spec dict to a ``name = fields.X(...)`` assignment string.

    The dict must have at least ``name`` and ``type`` keys.  All other keys are
    rendered as keyword arguments (proper Python literals — strings, numbers,
    booleans, None, lists, tuples, dicts all supported, e.g.
    ``choices=[["draft", "Draft"], ["pub", "Published"]]``).

    Special handling:
    - Relation fields (ForeignKey etc.) require ``to``, rendered as the first
      positional arg.
    - ``on_delete`` values are validated against zeeb_orm's constants and
      rendered as quoted strings (the constants are plain strings).
    - The reserved ``raw`` key maps kwarg names to verbatim Python source
      (escape hatch for validators, callables, ``default=dict``, …); raw
      entries win over same-named plain keys.
    """
    validate_field_spec(field)

    spec = dict(field)
    name = spec.pop("name")
    field_type = resolve_field_type(spec.pop("type"))
    raw: dict[str, str] = spec.pop("raw", None) or {}

    parts: list[str] = []
    if field_type in RELATION_FIELDS:
        parts.append(render_py_literal(spec.pop("to")))
        if "through_fields" in spec and isinstance(spec["through_fields"], list):
            spec["through_fields"] = tuple(spec["through_fields"])

    for key, val in spec.items():
        if key in raw:
            continue  # raw wins
        parts.append(f"{key}={render_py_literal(val)}")
    for key, code in raw.items():
        parts.append(f"{key}={code}")

    return f"{name} = fields.{field_type}({', '.join(parts)})"


def field_extra_imports(field: dict) -> list[str]:
    """Return import lines needed by a field spec's ``raw`` code."""
    raw = field.get("raw") or {}
    imports: list[str] = []
    if any("validators." in code for code in raw.values()):
        imports.append("from zeeb_orm import validators")
    return imports
