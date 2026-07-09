"""Code generation and file manipulation helpers."""

from __future__ import annotations

import re
from pathlib import Path

from zeeb_agents._utils.errors import AgentError, close_matches
from zeeb_agents._utils.field_types import render_field_line, render_py_literal

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
        "DjangoModelPermissions",
    }
)

# Throttle classes exported by zeeb_api.throttling that work as-is on a viewset.
VIEWSET_THROTTLES = frozenset({"AnonRateThrottle", "UserRateThrottle", "ScopedRateThrottle"})

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
    permission: str = "IsAuthenticatedOrReadOnly",
    extra_actions: list[dict] | None = None,
    read_only: bool = False,
    lookup_field: str | None = None,
    pagination: str | None = None,
    throttles: list[str] | None = None,
    search_fields: list[str] | None = None,
    ordering_fields: list[str] | None = None,
    filterset: str | None = None,
) -> str:
    """Render a ``ModelViewSet`` (or ``ReadOnlyModelViewSet``) subclass definition.

    Optional attributes cover the full viewset surface: ``lookup_field``,
    ``pagination_class`` (via :data:`PAGINATION_ALIASES`), ``throttle_classes``,
    and ``filter_backends`` built from *filterset* / *search_fields* /
    *ordering_fields*.  The caller is responsible for the matching imports
    (see :func:`viewset_option_imports`).
    """
    class_name = f"{model_name}ViewSet"
    ser_class = serializer_class or f"{model_name}Serializer"
    base_class = "ReadOnlyModelViewSet" if read_only else "ModelViewSet"
    lines = [
        f"class {class_name}({base_class}):",
        f"    queryset = {model_name}.objects.all()",
        f"    serializer_class = {ser_class}",
        f"    permission_classes = [permissions.{validate_permission(permission)}]",
    ]
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


# ---------------------------------------------------------------------------
# File-level helpers
# ---------------------------------------------------------------------------


def find_settings_file(root: Path) -> Path | None:
    """Return the project package's ``settings.py`` (first match), or ``None``."""
    for item in root.iterdir():
        if item.is_dir() and (item / "settings.py").exists():
            return item / "settings.py"
    return None


def set_or_append_setting(content: str, key: str, rendered: str) -> str:
    """Replace an existing ``KEY = ...`` assignment or append it at end of file.

    Handles assignments whose value spans multiple lines (e.g. a list literal
    split across lines), replacing the whole bracketed span rather than only
    the first line — which would otherwise orphan the continuation lines and
    corrupt ``settings.py``.
    """
    new_line = f"{key} = {rendered}"
    lines = content.splitlines(keepends=True)
    key_re = re.compile(rf"^{re.escape(key)}\s*=")

    start = next((i for i, ln in enumerate(lines) if key_re.match(ln)), None)
    if start is None:
        return content.rstrip("\n") + f"\n{new_line}\n"

    # Extend the span until any opened (), [], {} brackets are balanced again,
    # so multi-line literals are fully replaced.
    depth = 0
    end = start
    for j in range(start, len(lines)):
        depth += lines[j].count("(") + lines[j].count("[") + lines[j].count("{")
        depth -= lines[j].count(")") + lines[j].count("]") + lines[j].count("}")
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


def ensure_import(path: Path, import_line: str) -> None:
    """Add *import_line* to *path* if not already present."""
    content = path.read_text(encoding="utf-8")
    if import_line in content:
        return
    lines = content.splitlines(keepends=True)
    # Insert after the last existing import block
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith(("import ", "from ")):
            insert_at = i + 1
    lines.insert(insert_at, import_line + "\n")
    path.write_text("".join(lines), encoding="utf-8")


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
    lines = content.splitlines()
    in_class = False
    indent = "    "
    field_pattern = re.compile(r"^\s{4}(\w+)\s*=\s*fields\.")
    fields: list[str] = []

    for line in lines:
        if re.match(rf"^class {re.escape(class_name)}\b", line):
            in_class = True
            continue
        if in_class:
            if line.strip() and not line.startswith(indent) and not line.strip().startswith("#"):
                break
            m = field_pattern.match(line)
            if m:
                fields.append(m.group(1))

    return fields
