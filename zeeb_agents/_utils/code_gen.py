"""Code generation and file manipulation helpers."""

from __future__ import annotations

import re
from pathlib import Path

from zeeb_agents._utils.field_types import render_field_line


# ---------------------------------------------------------------------------
# Class rendering
# ---------------------------------------------------------------------------


def render_model_class(
    model_name: str,
    fields: list[dict],
    meta: dict | None = None,
) -> str:
    """Render a complete ``Model`` subclass definition."""
    lines = [f"class {model_name}(Model):"]

    if fields:
        for f in fields:
            lines.append(f"    {render_field_line(f)}")
    else:
        lines.append("    pass")

    if meta:
        lines.append("")
        lines.append("    class Meta:")
        for key, val in meta.items():
            if isinstance(val, str):
                lines.append(f'        {key} = "{val}"')
            elif isinstance(val, list):
                items = ", ".join(f'"{i}"' for i in val)
                lines.append(f"        {key} = [{items}]")
            else:
                lines.append(f"        {key} = {val}")

    return "\n".join(lines)


def render_serializer_class(
    model_name: str,
    fields: list[str] | None = None,
    read_only_fields: list[str] | None = None,
) -> str:
    """Render a ``ModelSerializer`` subclass definition."""
    class_name = f"{model_name}Serializer"
    fields_repr = (
        ", ".join(f'"{f}"' for f in fields)
        if fields
        else '"__all__"'
    )
    lines = [
        f"class {class_name}(ModelSerializer):",
        f"    class Meta:",
        f"        model = {model_name}",
        f"        fields = [{fields_repr}]",
    ]
    if read_only_fields:
        ro_repr = ", ".join(f'"{f}"' for f in read_only_fields)
        lines.append(f"        read_only_fields = [{ro_repr}]")
    return "\n".join(lines)


def render_viewset_class(
    model_name: str,
    serializer_class: str | None = None,
    permission: str = "IsAuthenticatedOrReadOnly",
    extra_actions: list[dict] | None = None,
) -> str:
    """Render a ``ModelViewSet`` subclass definition."""
    class_name = f"{model_name}ViewSet"
    ser_class = serializer_class or f"{model_name}Serializer"
    lines = [
        f"class {class_name}(ModelViewSet):",
        f"    queryset = {model_name}.objects.all()",
        f"    serializer_class = {ser_class}",
        f"    permission_classes = [permissions.{permission}]",
    ]
    if extra_actions:
        for action in extra_actions:
            a_name = action["name"]
            a_detail = action.get("detail", True)
            a_methods = action.get("methods", ["get"])
            methods_repr = ", ".join(f'"{m}"' for m in a_methods)
            lines.append("")
            lines.append(f'    @action(detail={a_detail}, methods=[{methods_repr}])')
            lines.append(f"    async def {a_name}(self, request, pk=None):")
            lines.append(f"        pass  # TODO: implement {a_name}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File-level helpers
# ---------------------------------------------------------------------------


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
    """Return class names of all ``Model`` subclasses in *content*."""
    pattern = re.compile(r"^class (\w+)\(.*?Model.*?\):", re.MULTILINE)
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
