"""Code generation and file manipulation helpers."""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

from zeeb_agents._utils import AgentResult
from zeeb_agents._utils.errors import AgentError, close_matches
from zeeb_agents._utils.field_types import render_field_line, render_py_literal

# Re-exported, not redefined: ``startapp`` derives a route prefix with the same
# helper, so a resource lands under an identical path whichever layer created
# it. Imported here because feature_spec and viewsets import it from this module.
from zeeb_orm.scaffold.naming import pluralize  # noqa: F401

# ---------------------------------------------------------------------------
# Idempotency policy shared by the create_* scaffolding tools.
#
# ``if_exists`` controls what happens when the target (model/serializer/viewset/
# route) already exists:
#   - "error" (default): fail with ``already_exists`` — the historical behavior.
#   - "skip": treat as success, return ``data["skipped"] = True`` and change
#     nothing — makes retries and generate_crud re-runs resumable.
# ---------------------------------------------------------------------------
IF_EXISTS_VALUES = ("error", "skip")


def validate_if_exists(value: str) -> None:
    """Raise ``AgentError(invalid_input)`` unless *value* is a known policy."""
    if value not in IF_EXISTS_VALUES:
        raise AgentError(
            f"if_exists must be one of {list(IF_EXISTS_VALUES)}, got '{value}'.",
            code="invalid_input",
        )


def skip_result(message: str, **data: object) -> AgentResult:
    """Build a success result for a no-op skip (``data['skipped'] = True``)."""
    return AgentResult(success=True, message=message, data={**data, "skipped": True})


# The full ``class Meta`` surface zeeb_orm's Options.from_meta understands.
# ``indexes``/``constraints`` accept lists of plain dicts (Options does
# ``Index(**idx)`` / ``UniqueConstraint(**c)`` / ``CheckConstraint(**c)``).
KNOWN_META_KEYS = frozenset(
    {
        "table_name",
        "db_table",
        "abstract",
        "managed",
        "ordering",
        "unique_together",
        "index_together",
        "indexes",
        "constraints",
        "default_permissions",
        "app_label",
    }
)


# ---------------------------------------------------------------------------
# Class rendering
# ---------------------------------------------------------------------------


def render_model_class(
    model_name: str,
    fields: list[dict],
    meta: dict | None = None,
) -> str:
    """Render a complete ``Model`` subclass definition.

    Meta values are emitted as Python literals, so nested structures work:
    ``unique_together=[["author", "slug"]]``,
    ``indexes=[{"fields": ["created_at"], "name": "idx_created"}]``,
    ``constraints=[{"check": "price >= 0", "name": "ck_price"}]``.
    """
    lines = [f"class {model_name}(Model):"]

    if fields:
        for f in fields:
            lines.append(f"    {render_field_line(f)}")
    else:
        lines.append("    pass")

    if meta:
        for key in meta:
            if key not in KNOWN_META_KEYS:
                suggestions = close_matches(str(key), sorted(KNOWN_META_KEYS))
                hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
                raise AgentError(
                    f"Unknown Meta key '{key}'.{hint} "
                    f"Valid keys: {sorted(KNOWN_META_KEYS)}",
                    code="invalid_meta",
                    suggestions=suggestions,
                )
        lines.append("")
        lines.append("    class Meta:")
        for key, val in meta.items():
            lines.append(f"        {key} = {render_py_literal(val)}")

    return "\n".join(lines)


# Declared-field types available on zeeb_api.serializers (plus the "nested"
# pseudo-type, which renders a nested serializer instance).
SERIALIZER_FIELD_TYPES = frozenset(
    {
        "BooleanField",
        "CharField",
        "DateField",
        "DateTimeField",
        "DecimalField",
        "DictField",
        "EmailField",
        "FloatField",
        "IntegerField",
        "ListField",
        "PrimaryKeyRelatedField",
        "SerializerMethodField",
        "SlugRelatedField",
        "TimeField",
        "URLField",
        "UUIDField",
    }
)


def _render_serializer_field(spec: dict) -> tuple[str, str | None]:
    """Render one declared serializer field.

    Returns ``(field_line, method_stub)`` where *method_stub* is the
    ``get_<name>`` body for ``SerializerMethodField`` (else ``None``).
    """
    spec = dict(spec)
    name = spec.pop("name", None)
    if not isinstance(name, str) or not name.isidentifier():
        raise AgentError(
            f"Serializer field spec needs a 'name' that is a valid identifier, got {name!r}",
            code="invalid_field_spec",
        )
    ftype = spec.pop("type", None)
    if ftype == "nested":
        serializer = spec.pop("serializer", None)
        if not isinstance(serializer, str) or not serializer.isidentifier():
            raise AgentError(
                f"Nested field '{name}' needs a 'serializer' class name "
                '(e.g. {"name": "author", "type": "nested", "serializer": "UserSerializer"})',
                code="invalid_field_spec",
            )
        kwargs = ", ".join(f"{k}={render_py_literal(v)}" for k, v in spec.items())
        return f"{name} = {serializer}({kwargs})", None
    if ftype not in SERIALIZER_FIELD_TYPES:
        suggestions = close_matches(str(ftype), sorted(SERIALIZER_FIELD_TYPES))
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise AgentError(
            f"Unknown serializer field type '{ftype}'.{hint} "
            f"Valid types: {sorted(SERIALIZER_FIELD_TYPES)} or 'nested'",
            code="invalid_field_type",
            suggestions=suggestions,
        )
    kwargs = ", ".join(f"{k}={render_py_literal(v)}" for k, v in spec.items())
    line = f"{name} = serializers.{ftype}({kwargs})"
    stub: str | None = None
    if ftype == "SerializerMethodField":
        stub = (
            f"    def get_{name}(self, obj):\n"
            f"        return None  # TODO: implement {name}"
        )
    return line, stub


def render_serializer_class(
    model_name: str,
    fields: list[str] | None = None,
    read_only_fields: list[str] | None = None,
    extra_fields: list[dict] | None = None,
    validate_fields: list[str] | None = None,
) -> str:
    """Render a ``ModelSerializer`` subclass definition.

    *extra_fields* are declared serializer fields (``SerializerMethodField``,
    typed fields with ``write_only``/``source``/…, or nested serializers via
    ``{"type": "nested", "serializer": "UserSerializer"}``).  Their names are
    appended to an explicit ``fields`` list automatically.  *validate_fields*
    emits ``validate_<field>`` stub methods.
    """
    class_name = f"{model_name}Serializer"

    declared_lines: list[str] = []
    method_stubs: list[str] = []
    declared_names: list[str] = []
    for spec in extra_fields or []:
        line, stub = _render_serializer_field(spec)
        declared_lines.append(f"    {line}")
        declared_names.append(spec["name"])
        if stub:
            method_stubs.append(stub)

    for field in validate_fields or []:
        if not isinstance(field, str) or not field.isidentifier():
            raise AgentError(
                f"validate_fields entries must be field names, got {field!r}",
                code="invalid_field_spec",
            )
        method_stubs.append(
            f"    def validate_{field}(self, value):\n"
            '        # TODO: raise serializers.ValidationError("...") on invalid input\n'
            "        return value"
        )

    meta_fields = list(fields) if fields else None
    if meta_fields is not None:
        for name in declared_names:
            if name not in meta_fields:
                meta_fields.append(name)
    if meta_fields:
        fields_line = "        fields = [{}]".format(
            ", ".join(f'"{f}"' for f in meta_fields)
        )
    else:
        # Must be the bare string, not a one-element list: the serializer
        # Meta compares ``fields == "__all__"`` — ``["__all__"]`` would be
        # treated as a literal field named __all__ and yield empty schemas.
        fields_line = '        fields = "__all__"'

    lines = [f"class {class_name}(ModelSerializer):"]
    if declared_lines:
        lines.extend(declared_lines)
        lines.append("")
    lines.extend(
        [
            "    class Meta:",
            f"        model = {model_name}",
            fields_line,
        ]
    )
    if read_only_fields:
        ro_repr = ", ".join(f'"{f}"' for f in read_only_fields)
        lines.append(f"        read_only_fields = [{ro_repr}]")
    for stub in method_stubs:
        lines.append("")
        lines.append(stub)
    return "\n".join(lines)


# Permission classes exported by zeeb_api.permissions.
VIEWSET_PERMISSIONS = frozenset(
    {
        "AllowAny",
        "IsAuthenticated",
        "IsAdminUser",
        "IsAuthenticatedOrReadOnly",
        "IsOwner",
        "IsOwnerOrReadOnly",
        "ModelPermissions",
    }
)

# Authentication classes exported by zeeb_api.authentication.
VIEWSET_AUTHENTICATION = frozenset(
    {
        "JWTAuthentication",
        "JWTStatelessAuthentication",
        "OAuth2BearerAuthentication",
    }
)

# Throttle classes exported by zeeb_api.throttling that work as-is on a viewset.
VIEWSET_THROTTLES = frozenset({"AnonRateThrottle", "UserRateThrottle", "ScopedRateThrottle"})

# The API operations a generated viewset can expose, and the zeeb_api mixin that
# implements each. The router binds a mapped action only when the viewset defines
# it (zeeb_api/routers/default.py), so composing a mixin subset yields exactly the
# requested endpoints — no route-level filtering needed.
VIEWSET_OPERATIONS = ("list", "retrieve", "create", "update", "delete")

_OPERATION_MIXINS = {
    "create": "CreateModelMixin",
    "list": "ListModelMixin",
    "retrieve": "RetrieveModelMixin",
    "update": "UpdateModelMixin",
    "delete": "DestroyModelMixin",
}

# Names an agent is likely to reach for — the framework's internal action names
# and the HTTP verbs — mapped to the operation that actually serves them. Used
# for suggestions only: they are rejected, not silently accepted, so the spec
# keeps one vocabulary.
_OPERATION_ALIASES = {
    "destroy": "delete",
    "remove": "delete",
    "partial_update": "update",
    "patch": "update",
    "put": "update",
    "post": "create",
    "add": "create",
    "get": "retrieve",
    "read": "retrieve",
    "detail": "retrieve",
    "index": "list",
    "all": "list",
}

# MRO order must follow ModelViewSet's own base order so a composed subset behaves
# identically to the prebuilt classes.
_MIXIN_ORDER = (
    "CreateModelMixin",
    "QueryModelMixin",
    "ListModelMixin",
    "RetrieveModelMixin",
    "UpdateModelMixin",
    "DestroyModelMixin",
)

# Friendly aliases → zeeb_api.pagination class names.
PAGINATION_ALIASES = {
    "page": "PageNumberPagination",
    "limit_offset": "LimitOffsetPagination",
    "cursor": "CursorPagination",
    "PageNumberPagination": "PageNumberPagination",
    "LimitOffsetPagination": "LimitOffsetPagination",
    "CursorPagination": "CursorPagination",
}


def validate_permission(permission: str) -> str:
    """Validate a permission class name against zeeb_api.permissions exports."""
    if permission not in VIEWSET_PERMISSIONS:
        suggestions = close_matches(permission, sorted(VIEWSET_PERMISSIONS))
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise AgentError(
            f"Unknown permission class '{permission}'.{hint} "
            f"Valid classes: {sorted(VIEWSET_PERMISSIONS)}",
            code="invalid_permission",
            suggestions=suggestions,
        )
    return permission


# Matches a project-local permission reference: ``apps.<app>.permissions.<Class>``.
_DOTTED_PERMISSION_RE = re.compile(
    r"^apps\.([A-Za-z_]\w*)\.permissions\.([A-Za-z_]\w*)$"
)
_CLASS_DEF_RE = re.compile(r"^class ([A-Za-z_]\w*)\b", re.MULTILINE)


def project_permission_classes(app_dir: Path) -> list[str]:
    """Return the class names defined in *app_dir*'s ``permissions.py`` (if any)."""
    path = app_dir / "permissions.py"
    if not path.exists():
        return []
    return _CLASS_DEF_RE.findall(path.read_text(encoding="utf-8"))


def resolve_permission(
    permission: str, app_dir: Path | None = None
) -> tuple[str | None, str]:
    """Resolve a permission name to ``(import_line, class_ref)`` for a viewset.

    Accepted forms:

    - a built-in ``zeeb_api.permissions`` class name (see
      :data:`VIEWSET_PERMISSIONS`) → ``(None, "permissions.<Name>")`` — the
      standard ``from zeeb_api import viewsets, permissions`` import covers it;
    - a bare class name defined in *app_dir*'s ``permissions.py`` (scaffold it
      with :func:`~zeeb_agents.permissions_scaffold.create_permission_class`)
      → ``("from apps.<app>.permissions import <Name>", "<Name>")``;
    - a dotted ``apps.<other_app>.permissions.<Class>`` reference to any app's
      class → the matching import and bare class reference.

    Raises ``AgentError(invalid_permission)`` with close-match suggestions
    spanning both built-ins and the app's custom classes otherwise.
    """
    if permission in VIEWSET_PERMISSIONS:
        return None, f"permissions.{permission}"

    dotted = _DOTTED_PERMISSION_RE.match(permission)
    if dotted and app_dir is not None:
        other_app, class_name = dotted.groups()
        target = app_dir.parent / other_app / "permissions.py"
        if target.exists() and class_exists(
            target.read_text(encoding="utf-8"), class_name
        ):
            return f"from apps.{other_app}.permissions import {class_name}", class_name
        raise AgentError(
            f"Permission class '{class_name}' not found in "
            f"apps/{other_app}/permissions.py. Create it first with "
            f"create_permission_class(app='{other_app}', class_name='{class_name}').",
            code="invalid_permission",
            suggestions=close_matches(
                class_name,
                project_permission_classes(app_dir.parent / other_app),
            ),
        )

    custom = project_permission_classes(app_dir) if app_dir is not None else []
    if permission in custom:
        return f"from apps.{app_dir.name}.permissions import {permission}", permission

    candidates = sorted(VIEWSET_PERMISSIONS) + sorted(custom)
    suggestions = close_matches(permission, candidates)
    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    custom_note = (
        f" Custom classes in this app: {sorted(custom)}."
        if custom
        else (
            " No custom classes exist in this app yet — scaffold one with "
            "create_permission_class, or reference another app's via "
            "'apps.<app>.permissions.<Class>'."
        )
    )
    raise AgentError(
        f"Unknown permission class '{permission}'.{hint} "
        f"Built-in classes: {sorted(VIEWSET_PERMISSIONS)}.{custom_note}",
        code="invalid_permission",
        suggestions=suggestions,
    )


# Matches a project-local authentication reference:
# ``apps.<app>.authentication.<Class>``.
_DOTTED_AUTHENTICATION_RE = re.compile(
    r"^apps\.([A-Za-z_]\w*)\.authentication\.([A-Za-z_]\w*)$"
)


def project_authentication_classes(app_dir: Path) -> list[str]:
    """Return the class names defined in *app_dir*'s ``authentication.py`` (if any)."""
    path = app_dir / "authentication.py"
    if not path.exists():
        return []
    return _CLASS_DEF_RE.findall(path.read_text(encoding="utf-8"))


def _normalize_class_list(value: str | list[str], *, param: str) -> list[str]:
    """Normalize a ``str | list[str]`` class-name parameter to a list.

    Raises ``AgentError(invalid_input)`` on an empty list or blank entries so
    callers fail fast before touching any file.
    """
    names = [value] if isinstance(value, str) else list(value)
    if not names:
        raise AgentError(
            f"{param} must name at least one class (got an empty list); "
            f"omit the parameter to use the default instead.",
            code="invalid_input",
        )
    for name in names:
        if not isinstance(name, str) or not name.strip():
            raise AgentError(
                f"Every {param} entry must be a non-empty class name, "
                f"got {name!r}.",
                code="invalid_input",
            )
    return names


def _resolve_many(
    values: str | list[str],
    app_dir: Path | None,
    resolver,
    *,
    param: str,
) -> tuple[list[str], list[str]]:
    """Map *values* through a single-name *resolver* to ``(imports, refs)``.

    Preserves caller order, dedupes on the resolved reference, and — when the
    input listed more than one entry — prefixes resolution errors with the
    failing entry so the caller knows which one to fix.
    """
    names = _normalize_class_list(values, param=param)
    imports: list[str] = []
    refs: list[str] = []
    for name in names:
        try:
            import_line, ref = resolver(name, app_dir)
        except AgentError as exc:
            if len(names) == 1:
                raise
            data = exc.result.data or {}
            raise AgentError(
                f"Invalid {param} entry '{name}': {exc}",
                code=data.get("error_code", "invalid_input"),
                suggestions=data.get("suggestions"),
            ) from exc
        if ref not in refs:
            refs.append(ref)
            if import_line and import_line not in imports:
                imports.append(import_line)
    return imports, refs


def resolve_permissions(
    permission: str | list[str], app_dir: Path | None = None
) -> tuple[list[str], list[str]]:
    """Resolve one or more permission names to ``(import_lines, class_refs)``.

    Plural companion to :func:`resolve_permission`: accepts a single name or a
    list (all classes are enforced together — AND semantics at runtime),
    preserves order, and dedupes repeated entries.
    """
    return _resolve_many(permission, app_dir, resolve_permission, param="permission")


def _resolve_authentication_ref(
    authentication: str, app_dir: Path | None = None
) -> tuple[str | None, str]:
    """Resolve one authentication name to ``(import_line, class_ref)``.

    Accepted forms mirror :func:`resolve_permission`:

    - a built-in ``zeeb_api.authentication`` class name (see
      :data:`VIEWSET_AUTHENTICATION`) → ``("from zeeb_api import
      authentication", "authentication.<Name>")``;
    - a bare class name defined in *app_dir*'s ``authentication.py``
      (hand-written) → ``("from apps.<app>.authentication import <Name>",
      "<Name>")``;
    - a dotted ``apps.<other_app>.authentication.<Class>`` reference.

    Raises ``AgentError(invalid_authentication)`` with close-match suggestions
    otherwise.
    """
    if authentication in VIEWSET_AUTHENTICATION:
        return "from zeeb_api import authentication", f"authentication.{authentication}"

    dotted = _DOTTED_AUTHENTICATION_RE.match(authentication)
    if dotted and app_dir is not None:
        other_app, class_name = dotted.groups()
        target = app_dir.parent / other_app / "authentication.py"
        if target.exists() and class_exists(
            target.read_text(encoding="utf-8"), class_name
        ):
            return (
                f"from apps.{other_app}.authentication import {class_name}",
                class_name,
            )
        raise AgentError(
            f"Authentication class '{class_name}' not found in "
            f"apps/{other_app}/authentication.py. Define it there (subclass "
            f"zeeb_api.authentication.BaseAuthentication) first.",
            code="invalid_authentication",
            suggestions=close_matches(
                class_name,
                project_authentication_classes(app_dir.parent / other_app),
            ),
        )

    custom = project_authentication_classes(app_dir) if app_dir is not None else []
    if authentication in custom:
        return (
            f"from apps.{app_dir.name}.authentication import {authentication}",
            authentication,
        )

    candidates = sorted(VIEWSET_AUTHENTICATION) + sorted(custom)
    suggestions = close_matches(authentication, candidates)
    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    custom_note = (
        f" Custom classes in this app: {sorted(custom)}."
        if custom
        else (
            " No custom classes exist in this app yet — define one in "
            "apps/<app>/authentication.py (subclass "
            "zeeb_api.authentication.BaseAuthentication), or reference "
            "another app's via 'apps.<app>.authentication.<Class>'."
        )
    )
    raise AgentError(
        f"Unknown authentication class '{authentication}'.{hint} "
        f"Built-in classes: {sorted(VIEWSET_AUTHENTICATION)}.{custom_note}",
        code="invalid_authentication",
        suggestions=suggestions,
    )


def resolve_authentication(
    authentication: str | list[str], app_dir: Path | None = None
) -> tuple[list[str], list[str]]:
    """Resolve one or more authentication names to ``(import_lines, class_refs)``.

    Accepts a single name or a list (classes are tried in order at runtime —
    the first one that recognizes the request's credentials wins), preserves
    order, and dedupes repeated entries.
    """
    return _resolve_many(
        authentication, app_dir, _resolve_authentication_ref, param="authentication"
    )


def resolve_viewset_base(
    operations: list[str] | None = None,
    read_only: bool = False,
) -> tuple[str, list[str]]:
    """Resolve the base-class expression and imports for a generated viewset.

    Returns ``(bases, imports)`` where *bases* is rendered verbatim into the
    ``class X(...)`` header. Full CRUD stays ``ModelViewSet`` and a plain
    read-only pair stays ``ReadOnlyModelViewSet`` — the prebuilt classes keep
    generated code idiomatic for the common cases. Any other subset composes the
    matching mixins explicitly, so an entity that asked for ``list``/``create``
    does not silently get a ``DELETE`` endpoint.

    *operations* ``None`` falls back to the *read_only* flag (the pre-operations
    contract). ``QueryModelMixin`` rides along with ``list`` only: it is the
    filterable companion read, so a write-only viewset must not gain it.
    """
    if operations is None:
        base = "ReadOnlyModelViewSet" if read_only else "ModelViewSet"
        return base, [f"from zeeb_api.viewsets import {base}"]

    requested = validate_viewset_operations(operations)
    if requested == set(VIEWSET_OPERATIONS):
        return "ModelViewSet", ["from zeeb_api.viewsets import ModelViewSet"]
    if requested == {"list", "retrieve"}:
        return "ReadOnlyModelViewSet", ["from zeeb_api.viewsets import ReadOnlyModelViewSet"]

    mixins = {_OPERATION_MIXINS[op] for op in requested}
    if "list" in requested:
        mixins.add("QueryModelMixin")
    ordered = [name for name in _MIXIN_ORDER if name in mixins]
    bases = ", ".join([*ordered, "GenericViewSet"])
    imports = [
        f"from zeeb_api.viewsets import {', '.join([*ordered, 'GenericViewSet'])}"
    ]
    return bases, imports


def validate_viewset_operations(operations: list[str]) -> set[str]:
    """Validate an operations list and return it as a set."""
    if not operations:
        raise AgentError(
            "operations must name at least one of "
            f"{list(VIEWSET_OPERATIONS)} — an endpoint with no operations serves nothing. "
            "Use api.expose=false to skip the endpoint entirely.",
            code="invalid_input",
        )
    unknown = [op for op in operations if op not in VIEWSET_OPERATIONS]
    if unknown:
        # difflib cannot bridge the framework's own action names (destroy →
        # delete), which are exactly what an agent reaches for first.
        alias = _OPERATION_ALIASES.get(str(unknown[0]).lower())
        suggestions = [alias] if alias else close_matches(
            str(unknown[0]), list(VIEWSET_OPERATIONS)
        )
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise AgentError(
            f"Unknown operation '{unknown[0]}'.{hint} "
            f"Valid operations: {list(VIEWSET_OPERATIONS)}",
            code="invalid_input",
            suggestions=suggestions,
        )
    return set(operations)


def resolve_pagination(pagination: str) -> str:
    """Resolve a pagination alias/class name to a zeeb_api.pagination class name."""
    resolved = PAGINATION_ALIASES.get(pagination)
    if resolved is None:
        suggestions = close_matches(pagination, sorted(PAGINATION_ALIASES))
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise AgentError(
            f"Unknown pagination '{pagination}'.{hint} "
            f"Valid values: {sorted(set(PAGINATION_ALIASES))}",
            code="invalid_input",
            suggestions=suggestions,
        )
    return resolved


def validate_throttles(throttles: list[str]) -> list[str]:
    """Validate throttle class names against zeeb_api.throttling exports."""
    for name in throttles:
        if name not in VIEWSET_THROTTLES:
            suggestions = close_matches(name, sorted(VIEWSET_THROTTLES))
            hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
            raise AgentError(
                f"Unknown throttle class '{name}'.{hint} "
                f"Valid classes: {sorted(VIEWSET_THROTTLES)}",
                code="invalid_input",
                suggestions=suggestions,
            )
    return throttles


def render_viewset_class(
    model_name: str,
    serializer_class: str | None = None,
    permission: str | list[str] = "permissions.IsAuthenticatedOrReadOnly",
    extra_actions: list[dict] | None = None,
    read_only: bool = False,
    lookup_field: str | None = None,
    pagination: str | None = None,
    throttles: list[str] | None = None,
    search_fields: list[str] | None = None,
    ordering_fields: list[str] | None = None,
    filterset: str | None = None,
    authentication: list[str] | None = None,
    operations: list[str] | None = None,
    owner_field: str | None = None,
    owner_scoped_reads: bool = False,
) -> str:
    """Render a ``ModelViewSet`` (or ``ReadOnlyModelViewSet``) subclass definition.

    *permission* is one or more **resolved class references** rendered
    verbatim into ``permission_classes`` (``"permissions.<Builtin>"`` or a
    bare project class name) — resolve user input with
    :func:`resolve_permissions` first; the caller also owns the matching
    imports. *authentication* is likewise a list of resolved references (from
    :func:`resolve_authentication`); when given, an ``authentication_classes``
    attribute is emitted — omitted, no line is rendered and the project's
    global authentication middleware stays in charge.

    *operations* names the API operations to expose (see
    :func:`resolve_viewset_base`); ``None`` falls back to the *read_only* flag.

    *owner_field* wires object ownership: a ``perform_create`` that stamps the
    authenticated user into ``<owner_field>_id`` server-side, plus — when
    *owner_scoped_reads* is set — a ``get_queryset`` that filters reads to the
    caller's own rows. The matching permission class is the caller's job.

    Optional attributes cover the full viewset surface: ``lookup_field``,
    ``pagination_class`` (via :data:`PAGINATION_ALIASES`), ``throttle_classes``,
    and ``filter_backends`` built from *filterset* / *search_fields* /
    *ordering_fields*.  The caller is responsible for the matching imports
    (see :func:`viewset_option_imports`).
    """
    class_name = f"{model_name}ViewSet"
    ser_class = serializer_class or f"{model_name}Serializer"
    base_class, _ = resolve_viewset_base(operations, read_only)
    perms = [permission] if isinstance(permission, str) else list(permission)
    lines = [
        f"class {class_name}({base_class}):",
        f"    queryset = {model_name}.objects",
        f"    serializer_class = {ser_class}",
        f"    permission_classes = [{', '.join(perms)}]",
    ]
    if authentication:
        lines.append(f"    authentication_classes = [{', '.join(authentication)}]")
    if lookup_field:
        lines.append(f'    lookup_field = "{lookup_field}"')
    if pagination:
        lines.append(f"    pagination_class = {resolve_pagination(pagination)}")
    if throttles:
        lines.append(
            f"    throttle_classes = [{', '.join(validate_throttles(throttles))}]"
        )
    backends: list[str] = []
    if filterset:
        backends.append(filterset)
    if search_fields:
        backends.append("SearchFilter")
    if ordering_fields:
        backends.append("OrderingFilter")
    if backends:
        lines.append(f"    filter_backends = [{', '.join(backends)}]")
    if search_fields:
        quoted = ", ".join(f'"{f}"' for f in search_fields)
        lines.append(f"    search_fields = [{quoted}]")
    if ordering_fields:
        quoted = ", ".join(f'"{f}"' for f in ordering_fields)
        lines.append(f"    ordering_fields = [{quoted}]")
    if owner_field:
        # Ownership is three things, not one: the permission class checks the
        # object, perform_create stamps the owner server-side (never trusting
        # the body), and get_queryset stops one user listing another's rows.
        # Emitting only the permission class left the first two to the agent.
        lines.append("")
        lines.append("    async def perform_create(self, serializer):")
        lines.append('        """Stamp the authenticated user as owner (never from the body)."""')
        lines.append("        user = self._get_user_from_request()")
        lines.append("        if user is not None:")
        lines.append(f'            serializer.validated_data["{owner_field}_id"] = user.id')
        if owner_scoped_reads:
            lines.append("")
            lines.append("    def get_queryset(self):")
            lines.append('        """Restrict every read to the caller\'s own rows."""')
            lines.append("        queryset = super().get_queryset()")
            lines.append("        user = self._get_user_from_request()")
            lines.append("        if user is None:")
            lines.append("            return queryset.none()")
            lines.append(f"        return queryset.filter({owner_field}_id=user.id)")
    if extra_actions:
        for action in extra_actions:
            a_name = action["name"]
            a_detail = action.get("detail", True)
            a_methods = action.get("methods", ["get"])
            methods_repr = ", ".join(f'"{m}"' for m in a_methods)
            lines.append("")
            lines.append(f'    @action(detail={a_detail}, methods=[{methods_repr}])')
            lines.append(f"    async def {a_name}(self, request, pk=None):")
            lines.append(
                f"        pass  # TODO: implement {a_name}; "
                "raise zeeb_api.exceptions (e.g. NotFound) on errors"
            )
    return "\n".join(lines)


def viewset_option_imports(
    read_only: bool = False,
    pagination: str | None = None,
    throttles: list[str] | None = None,
    search_fields: list[str] | None = None,
    ordering_fields: list[str] | None = None,
    filterset: str | None = None,
) -> list[str]:
    """Return the import lines a viewset rendered with these options needs."""
    imports: list[str] = []
    if read_only:
        imports.append("from zeeb_api.viewsets import ReadOnlyModelViewSet")
    if pagination:
        imports.append(f"from zeeb_api.pagination import {resolve_pagination(pagination)}")
    if throttles:
        imports.append(
            f"from zeeb_api.throttling import {', '.join(validate_throttles(throttles))}"
        )
    filter_imports = [name for name, flag in
                      (("SearchFilter", search_fields), ("OrderingFilter", ordering_fields))
                      if flag]
    if filter_imports:
        imports.append(f"from zeeb_api.filters import {', '.join(filter_imports)}")
    if filterset:
        imports.append(f"from .filters import {filterset}")
    return imports


def render_action_method(
    action_name: str,
    detail: bool = True,
    methods: list[str] | None = None,
    body: str | None = None,
    url_path: str | None = None,
    request_serializer: str | None = None,
    response_serializer: str | None = None,
    request_schema: str | None = None,
    response_schema: str | None = None,
    permission: str | list[str] | None = None,
) -> str:
    """Render a custom ``@action`` method (decorator + body) for a ViewSet.

    The decorator carries only the kwargs that were supplied — ``detail`` and
    ``methods`` always, then optionally ``url_path`` (a string literal),
    ``request_serializer``/``response_serializer``/``request_schema``/
    ``response_schema`` (bare class-name references — the caller ensures the
    imports) and ``permission_classes=[<resolved refs>]`` (*permission* is one
    or more resolved class references from :func:`resolve_permissions`,
    rendered verbatim).

    When *body* is given it is used verbatim (dedented then re-indented). When
    omitted, a scaffold is generated: a bare ``pass`` placeholder if no
    serializer/schema is wired, otherwise a serializer-aware skeleton that reads
    validated input via ``self.get_action_request_body()`` (loading the instance
    with ``await self.get_object()`` for detail actions) and returns serialized
    output.

    This function only renders text — resolving *permission* and validating
    the serializer/schema class names is the caller's job (see
    :func:`resolve_permission` and ``ensure_identifier``).
    """
    action_methods = [m.lower() for m in (methods or ["get"])]
    methods_repr = ", ".join(f'"{m}"' for m in action_methods)

    parts = [f"detail={detail}", f"methods=[{methods_repr}]"]
    if url_path:
        parts.append(f'url_path="{url_path}"')
    if request_serializer:
        parts.append(f"request_serializer={request_serializer}")
    if response_serializer:
        parts.append(f"response_serializer={response_serializer}")
    if request_schema:
        parts.append(f"request_schema={request_schema}")
    if response_schema:
        parts.append(f"response_schema={response_schema}")
    if permission:
        perms = [permission] if isinstance(permission, str) else list(permission)
        parts.append(f"permission_classes=[{', '.join(perms)}]")
    decorator = f"    @action({', '.join(parts)})"
    signature = f"    async def {action_name}(self, request, pk=None):"

    if body is not None and body.strip():
        dedented = textwrap.dedent(body).strip("\n")
        body_block = textwrap.indent(dedented, "        ")
    else:
        body_block = _render_action_scaffold(
            action_name,
            detail=detail,
            request_serializer=request_serializer,
            response_serializer=response_serializer,
            request_schema=request_schema,
            response_schema=response_schema,
        )
    return f"{decorator}\n{signature}\n{body_block}\n"


def _render_action_scaffold(
    action_name: str,
    *,
    detail: bool,
    request_serializer: str | None,
    response_serializer: str | None,
    request_schema: str | None,
    response_schema: str | None,
) -> str:
    """Return the indented method body for a custom action lacking an explicit body.

    With no serializer/schema wired this preserves the historical
    ``pass  # TODO`` placeholder; otherwise it emits a serializer-aware skeleton
    the developer fills in.
    """
    has_wiring = any(
        (request_serializer, response_serializer, request_schema, response_schema)
    )
    if not has_wiring:
        return f"        pass  # TODO: implement {action_name}"

    lines: list[str] = []
    if request_serializer:
        lines.append(
            f"serializer = {request_serializer}(data=self.get_action_request_body())"
        )
        lines.append("serializer.is_valid(raise_exception=True)")
        lines.append("data = serializer.validated_data")
    elif request_schema:
        lines.append("data = self.get_action_request_body()")
    if detail:
        lines.append("instance = await self.get_object()")
    lines.append(f"# TODO: apply your logic for {action_name}")
    if response_serializer:
        if detail:
            lines.append(f"return {response_serializer}(instance).data")
        else:
            lines.append(
                f"# serialize your result set: {response_serializer}(items, many=True).data"
            )
            lines.append('return {"detail": "TODO"}')
    else:
        lines.append('return {"detail": "TODO"}')
    return textwrap.indent("\n".join(lines), "        ")


# ---------------------------------------------------------------------------
# File-level helpers
# ---------------------------------------------------------------------------


def router_registrations(source: str) -> list[tuple[str, str, int]]:
    """Return ``(prefix, viewset, lineno)`` for every real ``.register("x", Y)`` call.

    Parsed from the AST, not matched textually. A scaffolded ``urls.py`` carries
    a copy-pasteable ``router.register(...)`` example in its module docstring,
    and a text scan counts that example as a live route — which would make
    ``list_endpoints`` advertise an endpoint that 404s, ``register_route``
    refuse a prefix nothing occupies, and ``unregister_route`` delete a line out
    of the middle of a docstring.

    ``lineno`` is 1-based, as ``ast`` reports it. A call whose prefix is not a
    string literal or whose viewset is not a plain name is skipped: it cannot be
    reasoned about statically, and guessing is worse than omitting.

    Returns an empty list for source that does not parse — a broken ``urls.py``
    serves nothing, so "no registrations" is the accurate answer.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    found: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "register"):
            continue
        if len(node.args) < 2:
            continue
        prefix, viewset = node.args[0], node.args[1]
        if not (isinstance(prefix, ast.Constant) and isinstance(prefix.value, str)):
            continue
        if not isinstance(viewset, ast.Name):
            continue
        found.append((prefix.value, viewset.id, node.lineno))
    return found


def find_settings_file(root: Path) -> Path | None:
    """Return the project package's ``settings.py`` (first match), or ``None``."""
    for item in root.iterdir():
        if item.is_dir() and (item / "settings.py").exists():
            return item / "settings.py"
    return None


def _strip_strings_and_comments(line: str) -> str:
    """Return *line* with string-literal contents and comments blanked out.

    Used by the bracket-balance fallback in :func:`set_or_append_setting` so
    brackets inside string values (URLs, regexes, secrets) don't miscount the
    nesting depth. Single-line state machine — good enough for the broken-file
    fallback it serves.
    """
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        if ch == "#":
            break
        out.append(ch)
        i += 1
    return "".join(out)


def set_or_append_setting(content: str, key: str, rendered: str) -> str:
    """Replace an existing ``KEY = ...`` assignment or append it at end of file.

    Handles assignments whose value spans multiple lines (e.g. a list literal
    split across lines), replacing the whole bracketed span rather than only
    the first line — which would otherwise orphan the continuation lines and
    corrupt ``settings.py``. The span comes from the parsed AST, so brackets
    inside string values can't confuse it; a bracket-balance scan (with strings
    and comments stripped) remains as fallback for files that don't parse.
    """
    new_line = f"{key} = {rendered}"
    lines = content.splitlines(keepends=True)

    try:
        tree = ast.parse(content)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == key
            ):
                start, end = node.lineno - 1, node.end_lineno - 1
                return (
                    "".join(lines[:start]) + new_line + "\n" + "".join(lines[end + 1 :])
                )
        return content.rstrip("\n") + f"\n{new_line}\n"

    key_re = re.compile(rf"^{re.escape(key)}\s*=")
    start = next((i for i, ln in enumerate(lines) if key_re.match(ln)), None)
    if start is None:
        return content.rstrip("\n") + f"\n{new_line}\n"

    # Extend the span until any opened (), [], {} brackets are balanced again,
    # so multi-line literals are fully replaced.
    depth = 0
    end = start
    for j in range(start, len(lines)):
        code = _strip_strings_and_comments(lines[j])
        depth += code.count("(") + code.count("[") + code.count("{")
        depth -= code.count(")") + code.count("]") + code.count("}")
        end = j
        if depth <= 0:
            break

    return "".join(lines[:start]) + new_line + "\n" + "".join(lines[end + 1 :])


def append_block(path: Path, code: str) -> None:
    """Append *code* to *path*, ensuring two blank lines of separation."""
    content = path.read_text(encoding="utf-8")
    if content and not content.endswith("\n\n"):
        content = content.rstrip("\n") + "\n\n"
    content += code + "\n"
    path.write_text(content, encoding="utf-8")


def last_import_line(text: str) -> int:
    """Return the 1-based line after the last top-level import in *text*.

    Parsed from the AST so a parenthesized multi-line import counts as one
    statement — a line scan would stop at its ``from x import (`` line and
    insert *inside* the parentheses, producing a SyntaxError.

    Falls back to a line scan when the source does not parse, which is the
    best that can be done for a file that is already broken.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        insert_at = 0
        for i, line in enumerate(text.splitlines()):
            if line.startswith(("import ", "from ")):
                insert_at = i + 1
        return insert_at

    insert_at = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            insert_at = node.end_lineno or node.lineno
    return insert_at


def imports_name(source: str, module: str, name: str) -> bool:
    """Whether *source* really imports *name* from *module*.

    Asked of the AST rather than of the text. The scaffolded modules carry a
    copy-pasteable ``from .views import XViewSet`` inside their docstring, and a
    substring check would report that example as a live import — leaving the
    module referencing a name nothing binds, which fails at import time and
    takes the whole app down rather than just one endpoint.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return f"import {name}" in source  # best effort on a half-written file
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            source_module = ("." * (node.level or 0)) + (node.module or "")
            if source_module != module:
                continue
            if any(alias.asname == name or alias.name == name for alias in node.names):
                return True
        elif isinstance(node, ast.Import):
            if any(alias.asname == name or alias.name == name for alias in node.names):
                return True
    return False


def _already_imported(content: str, import_line: str) -> bool:
    """Whether every name *import_line* binds is already imported by *content*.

    Asked of the AST rather than by matching the line's text, because the same
    import is written many ways: the scaffold ships
    ``from zeeb_api import permissions, viewsets`` while a generator adds
    ``from zeeb_api import viewsets, permissions``. A textual comparison calls
    those different and writes the second one, leaving a duplicate that fails
    the project's own ``ruff check .`` with a redefinition.
    """
    try:
        tree = ast.parse(import_line.strip())
    except SyntaxError:
        return import_line in content
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = ("." * (node.level or 0)) + (node.module or "")
            return all(
                imports_name(content, module, alias.asname or alias.name) for alias in node.names
            )
        if isinstance(node, ast.Import):
            return all(
                imports_name(content, "", alias.asname or alias.name) for alias in node.names
            )
    return import_line in content


def ensure_import(path: Path, import_line: str) -> None:
    """Add *import_line* to *path* unless the names it binds are already imported."""
    content = path.read_text(encoding="utf-8")
    if _already_imported(content, import_line):
        return
    lines = content.splitlines(keepends=True)
    lines.insert(last_import_line(content), import_line + "\n")
    path.write_text("".join(lines), encoding="utf-8")


def imports_referenced_by(content: str, block: str) -> list[str]:
    """Return *content*'s import lines whose bound names appear in *block*.

    Used when a block of code is lifted out of a module to be put back later:
    the block alone is not restorable, because the names it leans on are bound
    by imports that stayed behind (and that the removal may have pruned as
    unused). Selecting only the imports the block actually references — rather
    than replaying the whole header — keeps the restored module free of unused
    imports, which a generated project must be to stay lint-clean.

    Matching is on whole words in the block text. Over-selecting is harmless
    (the import was already there); the word boundary is what stops
    ``Post`` from dragging in an import bound as ``PostSerializer``.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    lines = content.splitlines()
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        bound = [(alias.asname or alias.name).split(".")[0] for alias in node.names]
        if not any(re.search(rf"\b{re.escape(name)}\b", block) for name in bound):
            continue
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        text = "\n".join(lines[node.lineno - 1 : end]).strip()
        # Drop a trailing comment: the scaffold's placeholder imports carry an
        # unused-import suppression that stops being true the moment a block
        # which actually uses the name is put back.
        text = _code_before_comment(text).strip() or text
        if text and text not in out:
            out.append(text)
    return out


def _code_before_comment(line: str) -> str:
    """The code part of *line*, dropping a trailing ``#`` comment."""
    return _strip_strings_and_comments(line).rstrip()


def ensure_middleware(settings_path: Path, dotted_path: str) -> bool:
    """Ensure *dotted_path* is an active entry in ``settings.MIDDLEWARE``.

    Returns ``True`` when the entry was added, ``False`` when it was already
    present (an uncommented occurrence inside the ``MIDDLEWARE`` list). A
    commented-out entry (``# "..."``) does not count as present and is left
    untouched. When no ``MIDDLEWARE`` assignment exists at all, a new one is
    appended.
    """
    content = settings_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    start = next(
        (i for i, ln in enumerate(lines) if re.match(r"^MIDDLEWARE\s*=\s*\[", ln)),
        None,
    )
    if start is None:
        new = content.rstrip("\n") + f'\n\nMIDDLEWARE = [\n    "{dotted_path}",\n]\n'
        settings_path.write_text(new, encoding="utf-8")
        return True

    # Extend the span until the opened bracket is balanced again.
    depth = 0
    end = start
    for j in range(start, len(lines)):
        depth += lines[j].count("[") - lines[j].count("]")
        end = j
        if depth <= 0:
            break

    # Already an active (non-commented) entry anywhere in the list?
    for ln in lines[start : end + 1]:
        if dotted_path in ln.split("#", 1)[0]:
            return False

    close_line = lines[end]
    idx = close_line.rfind("]")
    before, after = close_line[:idx], close_line[idx:]
    if before.strip() == "":
        # Closing bracket sits on its own line — insert an entry above it.
        lines.insert(end, f'    "{dotted_path}",\n')
    else:
        # Content shares the closing-bracket line, e.g. ``["a", "b"]``.
        head = before.rstrip()
        if head.endswith("["):
            joined = f'"{dotted_path}"'
        else:
            joined = head.rstrip(",") + f', "{dotted_path}"'
        lines[end] = joined + after
    settings_path.write_text("".join(lines), encoding="utf-8")
    return True


_ASGI_MIDDLEWARE_MARKER = "app.add_middleware(middleware_cls)"


def ensure_asgi_middleware(asgi_path: Path) -> bool | None:
    """Ensure a project's ``asgi.py`` actually applies ``settings.MIDDLEWARE``.

    Projects scaffolded before the middleware-apply fix import ``MIDDLEWARE``
    but never install it, so ``JWTAuthMiddleware`` is inert: ``request.state.user``
    is never set and every protected ViewSet 403s even with a valid Bearer
    token. Editing ``settings.MIDDLEWARE`` alone cannot fix that — the loop that
    reads it lives in ``asgi.py``. This injects the standard apply-loop right
    before the ``for route in get_routes():`` include.

    Returns ``True`` when the loop was injected, ``False`` when it was already
    present (idempotent), and ``None`` when the file is absent or its structure
    was not recognized — in which case it is left untouched, so a hand-customized
    ``asgi.py`` is never corrupted.
    """
    if not asgi_path.exists():
        return None
    content = asgi_path.read_text(encoding="utf-8")
    if _ASGI_MIDDLEWARE_MARKER in content:
        return False  # already applies MIDDLEWARE
    if "MIDDLEWARE" not in content:
        return None  # no MIDDLEWARE symbol to apply — unrecognized layout
    lines = content.splitlines(keepends=True)
    anchor = next(
        (i for i, ln in enumerate(lines) if re.match(r"^\s*for route in get_routes\(\):", ln)),
        None,
    )
    if anchor is None:
        return None  # unrecognized create_app() body — do not touch
    indent = re.match(r"^(\s*)", lines[anchor]).group(1)
    block = [
        f"{indent}# Apply MIDDLEWARE from settings (CORS is handled explicitly above).\n",
        f"{indent}# Without this, entries like JWTAuthMiddleware are never installed and\n",
        f"{indent}# request.state.user stays unset for every protected ViewSet.\n",
        f"{indent}import importlib\n",
        "\n",
        f"{indent}for middleware_path in reversed(MIDDLEWARE):\n",
        f'{indent}    if middleware_path == "zeeb_api.middleware.CORSMiddleware":\n',
        f"{indent}        continue\n",
        f'{indent}    module_name, _, class_name = middleware_path.rpartition(".")\n',
        f"{indent}    middleware_cls = getattr(importlib.import_module(module_name), class_name)\n",
        f"{indent}    app.add_middleware(middleware_cls)\n",
        "\n",
    ]
    lines[anchor:anchor] = block
    asgi_path.write_text("".join(lines), encoding="utf-8")
    return True


def class_exists(content: str, class_name: str) -> bool:
    """Return True if a class named *class_name* is defined in *content*."""
    return bool(re.search(rf"^class {re.escape(class_name)}\b", content, re.MULTILINE))


def _class_block_pattern(class_name: str) -> re.Pattern[str]:
    """The span of a top-level class: its header through the next top-level line."""
    return re.compile(
        rf"^class {re.escape(class_name)}\b.*?(?=^\S|\Z)",
        re.DOTALL | re.MULTILINE,
    )


def remove_class_block(content: str, class_name: str) -> str | None:
    """Remove the whole ``class <class_name>`` block from *content*.

    The block runs to the next top-level statement, so decorators or module-level
    code following the class survive. Returns updated content, or ``None`` when
    the class is not defined (so callers can report a skip rather than a write).
    """
    if not class_exists(content, class_name):
        return None
    return _class_block_pattern(class_name).sub("", content, count=1).rstrip("\n") + "\n"


def extract_class_block(content: str, class_name: str) -> str | None:
    """Return the source of ``class <class_name>``, or ``None`` when undefined.

    The read-side sibling of :func:`remove_class_block`, deliberately sharing its
    span so extract-then-remove round-trips exactly: what is handed to the caller
    is precisely what leaves the file. That is what lets a feature be archived
    and restored verbatim, hand edits and all, instead of being regenerated from
    a spec that never knew about them.
    """
    if not class_exists(content, class_name):
        return None
    match = _class_block_pattern(class_name).search(content)
    return match.group(0).rstrip("\n") + "\n" if match else None


def remove_method_from_class(content: str, class_name: str, method_name: str) -> str | None:
    """Remove one method (and its decorators) from *class_name*'s body.

    Scoped to the target class so a same-named method on another class in the
    file survives — which matters because a feature's endpoint methods sit
    alongside other features' in the same ``views.py``.

    Returns updated content, or ``None`` when the class or the method is absent.
    """
    block = extract_class_block(content, class_name)
    if block is None:
        return None
    pattern = re.compile(
        rf"\n(?:[ \t]+@[^\n]*\n)*[ \t]+(?:async\s+)?def {re.escape(method_name)}\b"
        r".*?(?=\n[ \t]*(?:@|(?:async\s+)?def )|\Z)",
        re.DOTALL,
    )
    if not pattern.search(block):
        return None
    trimmed = pattern.sub("", block, count=1).rstrip("\n") + "\n"
    return content.replace(block, trimmed, 1)


def _function_block_pattern(function_name: str) -> re.Pattern[str]:
    """The span of a top-level function, including any decorators above it."""
    return re.compile(
        rf"^(?:@[^\n]*\n)*(?:async\s+)?def {re.escape(function_name)}\b.*?(?=^\S|\Z)",
        re.DOTALL | re.MULTILINE,
    )


def function_exists(content: str, function_name: str) -> bool:
    """Return True if a top-level function named *function_name* is defined."""
    return bool(
        re.search(
            rf"^(?:async\s+)?def {re.escape(function_name)}\b",
            content,
            re.MULTILINE,
        )
    )


def extract_function_block(content: str, function_name: str) -> str | None:
    """Return the source of a top-level function with its decorators, or ``None``.

    Decorators are part of the block because for a route handler the decorator
    *is* the registration — extracting the body alone would archive code that no
    longer means anything.
    """
    if not function_exists(content, function_name):
        return None
    match = _function_block_pattern(function_name).search(content)
    return match.group(0).rstrip("\n") + "\n" if match else None


def remove_route_function(content: str, function_name: str) -> str | None:
    """Remove a top-level function and its decorators from *content*.

    The counterpart to :func:`~zeeb_agents.routes.create_route`. Only the handler
    goes: the module-level ``router`` and the app's ``urls.py`` include stay put,
    because other handlers — including ones belonging to other features in the
    same app — hang off them.

    Returns updated content, or ``None`` when the function is not defined.
    """
    if not function_exists(content, function_name):
        return None
    return _function_block_pattern(function_name).sub("", content, count=1).rstrip("\n") + "\n"


def class_has_field(content: str, class_name: str, field_name: str) -> bool:
    """Return ``True`` if *field_name* is already assigned in *class_name*'s body.

    Used to make ``add_field`` idempotent: adding a field that already exists is
    a no-op, not a second definition. Scans only the target class body (up to the
    next top-level definition) so a same-named field on another model doesn't
    count.
    """
    lines = content.splitlines()
    class_start: int | None = None
    for i, line in enumerate(lines):
        if re.match(rf"^class {re.escape(class_name)}\b", line):
            class_start = i
            break
    if class_start is None:
        return False

    indent = "    "
    for line in lines[class_start + 1 :]:
        stripped = line.strip()
        if stripped and not line.startswith(indent) and not stripped.startswith("#"):
            break  # next top-level definition → end of class body
        if re.match(rf"\s+{re.escape(field_name)}\s*[:=]", line):
            return True
    return False


def add_field_to_class(content: str, class_name: str, field_line: str) -> str | None:
    """Insert *field_line* into the body of *class_name*.

    The field is inserted just before ``class Meta:`` if present, otherwise at
    the end of the class body.  Returns the updated content, or ``None`` if the
    class was not found.
    """
    lines = content.splitlines(keepends=True)
    class_start: int | None = None
    for i, line in enumerate(lines):
        if re.match(rf"^class {re.escape(class_name)}\b", line):
            class_start = i
            break
    if class_start is None:
        return None

    indent = "    "
    insert_at: int | None = None
    class_end = len(lines)

    for i in range(class_start + 1, len(lines)):
        line = lines[i]
        stripped = line.strip()
        # Next top-level definition → end of class
        if stripped and not line.startswith(indent) and not stripped.startswith("#"):
            class_end = i
            break
        if re.match(r"\s+class Meta\b", line):
            insert_at = i
            break

    if insert_at is None:
        insert_at = class_end

    lines.insert(insert_at, f"{indent}{field_line}\n")
    return "".join(lines)


def replace_field_in_class(
    content: str, class_name: str, field_name: str, field_line: str
) -> str | None:
    """Replace ``field_name``'s assignment line inside *class_name* with *field_line*.

    Generated field definitions are always a single line, so the whole
    assignment is one physical line; a hand-edited multi-line definition is not
    matched (the caller reports ``field_not_found`` rather than corrupting it).
    Returns updated content, or ``None`` when the class or the field is absent.
    """
    lines = content.splitlines(keepends=True)
    class_start: int | None = None
    for i, line in enumerate(lines):
        if re.match(rf"^class {re.escape(class_name)}\b", line):
            class_start = i
            break
    if class_start is None:
        return None

    indent = "    "
    field_pattern = re.compile(rf"^{re.escape(indent)}{re.escape(field_name)}\s*=")
    for i in range(class_start + 1, len(lines)):
        line = lines[i]
        stripped = line.strip()
        if stripped and not line.startswith(indent) and not stripped.startswith("#"):
            break  # next top-level definition → end of class body
        if field_pattern.match(line):
            lines[i] = f"{indent}{field_line}\n"
            return "".join(lines)
    return None


def remove_import_name(content: str, name: str) -> str:
    """Drop *name* from every ``from ... import ...`` line in *content*.

    Deleting a generated class without dropping the imports that reference it
    leaves the module raising ``ImportError`` at startup — the whole app stops
    serving. Lines left with no names are removed entirely; unrelated lines and
    any other imported names are preserved verbatim.
    """
    pattern = re.compile(r"^(from\s+[\w.]+\s+import\s+)(.+)$")
    out: list[str] = []
    for line in content.splitlines(keepends=True):
        match = pattern.match(line.rstrip("\n"))
        if not match:
            out.append(line)
            continue
        names = [n.strip() for n in match.group(2).split(",")]
        kept = [n for n in names if n and n.split(" as ")[0].strip() != name]
        if kept == names:
            out.append(line)
        elif kept:
            out.append(f"{match.group(1)}{', '.join(kept)}\n")
    return "".join(out)


def remove_field_from_class(content: str, class_name: str, field_name: str) -> str | None:
    """Remove the line containing ``field_name = fields.X(...)`` from *class_name*.

    Returns updated content or ``None`` if class/field was not found.
    """
    lines = content.splitlines(keepends=True)
    class_start: int | None = None
    for i, line in enumerate(lines):
        if re.match(rf"^class {re.escape(class_name)}\b", line):
            class_start = i
            break
    if class_start is None:
        return None

    indent = "    "
    field_pattern = re.compile(rf"^{re.escape(indent)}{re.escape(field_name)}\s*=")
    removed = False
    new_lines: list[str] = []

    in_class = False
    for i, line in enumerate(lines):
        if i == class_start:
            in_class = True
            new_lines.append(line)
            continue
        if in_class:
            stripped = line.strip()
            if stripped and not line.startswith(indent) and not stripped.startswith("#"):
                in_class = False
            elif not removed and field_pattern.match(line):
                removed = True
                continue
        new_lines.append(line)

    return "".join(new_lines) if removed else None


# ---------------------------------------------------------------------------
# Model parsing (for list_models)
# ---------------------------------------------------------------------------


def extract_model_names(content: str) -> list[str]:
    """Return class names of all ``Model``/``AbstractUser`` subclasses in *content*."""
    # AbstractUser must match too: custom user models are generated as
    # ``class User(AbstractUser):`` and would otherwise vanish from
    # list_models / schema inspection.
    pattern = re.compile(
        r"^class (\w+)\(.*?(?:Model|AbstractUser).*?\):", re.MULTILINE
    )
    return [m.group(1) for m in pattern.finditer(content)]


def extract_field_names(content: str, class_name: str) -> list[str]:
    """Return field names defined in *class_name* (``name = fields.X``)."""
    return list(extract_field_types(content, class_name))


def extract_field_types(content: str, class_name: str) -> dict[str, str]:
    """Return ``{field name: native field type}`` for *class_name*.

    Same scan as :func:`extract_field_names` (which delegates here), keeping the
    declaration order, but also capturing the field class — the type is what
    lets a spec-vs-disk diff tell "this field is new" from "this field changed
    type", which name-only inventory cannot.
    """
    lines = content.splitlines()
    in_class = False
    indent = "    "
    field_pattern = re.compile(r"^\s{4}(\w+)\s*=\s*fields\.(\w+)")
    fields: dict[str, str] = {}

    for line in lines:
        if re.match(rf"^class {re.escape(class_name)}\b", line):
            in_class = True
            continue
        if in_class:
            if line.strip() and not line.startswith(indent) and not line.strip().startswith("#"):
                break
            m = field_pattern.match(line)
            if m:
                fields[m.group(1)] = m.group(2)

    return fields
