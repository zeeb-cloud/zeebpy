"""Agent functions for serializer management."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.code_gen import (
    append_block,
    class_exists,
    ensure_import,
    render_serializer_class,
)
from zeeb_agents._utils.errors import AgentError
from zeeb_agents._utils.validation import ensure_app_exists, ensure_identifier


def _serializers_file(app: str, root: Path) -> Path:
    """Return ``apps/<app>/serializers.py``; fail with suggestions if the app is missing."""
    return ensure_app_exists(app, root) / "serializers.py"


@agent_function
async def create_serializer(
    app: str,
    model_name: str,
    fields: list[str] | None = None,
    read_only_fields: list[str] | None = None,
    extra_fields: list[dict] | None = None,
    validate_fields: list[str] | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Append a ``ModelSerializer`` subclass to ``apps/<app>/serializers.py``.

    Args:
        app: App directory name.
        model_name: The model this serializer is for (e.g. ``"Post"``).
        fields: Field names to include.  Defaults to ``["__all__"]``.
        read_only_fields: Fields that are read-only (e.g. ``["id", "created_at"]``).
        extra_fields: Declared serializer field specs.  Each dict has ``name``,
            ``type`` (a ``zeeb_api.serializers`` field class name, or
            ``"nested"``), and any field kwargs, e.g.::

                [
                    {"name": "display_name", "type": "SerializerMethodField"},
                    {"name": "password", "type": "CharField",
                     "write_only": True, "max_length": 128},
                    {"name": "author", "type": "nested",
                     "serializer": "UserSerializer", "read_only": True},
                ]

            ``SerializerMethodField`` entries also emit a ``get_<name>`` stub.
            Nested serializer classes must exist in (or be imported into) the
            app's ``serializers.py``.  Declared names are appended to an
            explicit ``fields`` list automatically.
        validate_fields: Field names to emit ``validate_<field>(self, value)``
            stub methods for.
        project_id: The host-assigned project id (required).

    Example::

        await create_serializer("blog", "Post",
            fields=["id", "title", "body", "created_at"],
            read_only_fields=["id", "created_at"],
            validate_fields=["title"])

    Returns data (on success):
        app (str): the app directory name
        model (str): the model name passed in
        serializer (str): the generated class name (``"<ModelName>Serializer"``)
        extra_fields (list[str]): names of declared fields added (empty when
            ``extra_fields`` was not given)

    Notes:
        - Failures carry ``error_code`` in ``data`` (``app_not_found``,
          ``already_exists``, ``invalid_field_type``, ``invalid_field_spec``, …)
          plus close-match ``suggestions`` where applicable.
    """
    ensure_identifier(model_name, "model name")
    path = _serializers_file(app, project_root)
    if not path.exists():
        return AgentResult(success=False, message=f"serializers.py not found at {path}")

    class_name = f"{model_name}Serializer"

    def _write() -> None:
        content = path.read_text(encoding="utf-8")
        if class_exists(content, class_name):
            raise AgentError(
                f"'{class_name}' already exists in {path}",
                code="already_exists",
                serializer=class_name,
            )
        class_code = render_serializer_class(
            model_name, fields, read_only_fields, extra_fields, validate_fields
        )
        ensure_import(path, "from zeeb_api import serializers")
        ensure_import(path, "from zeeb_api.serializers import ModelSerializer")
        ensure_import(path, f"from .models import {model_name}")
        append_block(path, class_code)

    await asyncio.to_thread(_write)
    return AgentResult(
        success=True,
        message=f"'{class_name}' created in apps/{app}/serializers.py",
        data={
            "app": app,
            "model": model_name,
            "serializer": class_name,
            "extra_fields": [f["name"] for f in (extra_fields or [])],
        },
    )


@agent_function
async def update_serializer(
    app: str,
    model_name: str,
    fields: list[str] | None = None,
    read_only_fields: list[str] | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Update the ``fields`` and/or ``read_only_fields`` of an existing serializer.

    Replaces the ``class Meta`` ``fields`` / ``read_only_fields`` lines in place.

    Returns data (on success):
        app (str): the app directory name
        serializer (str): the class name (``"<ModelName>Serializer"``)
        changes (list[str]): human-readable change descriptions applied, e.g.
            ``["fields updated", "read_only_fields updated"]``

    Notes:
        - On failure (missing ``serializers.py``, or the class is not found)
          ``data`` is ``None``.
        - If neither ``fields`` nor ``read_only_fields`` is given, ``changes``
          is empty and the file is rewritten unchanged.
    """
    path = _serializers_file(app, project_root)
    if not path.exists():
        return AgentResult(success=False, message=f"serializers.py not found at {path}")

    class_name = f"{model_name}Serializer"

    def _update() -> list[str]:
        content = path.read_text(encoding="utf-8")
        if not class_exists(content, class_name):
            raise AgentError(
                f"'{class_name}' not found in {path}",
                code="model_not_found",
                serializer=class_name,
            )
        changes: list[str] = []
        # Scope every substitution to the target class block — a file-wide
        # re.sub would rewrite the Meta of *every* serializer in the file.
        block_pattern = re.compile(
            rf"(^class {re.escape(class_name)}\b.*?)(?=^\S|\Z)",
            re.DOTALL | re.MULTILINE,
        )
        match = block_pattern.search(content)
        if match is None:
            raise AgentError(
                f"'{class_name}' not found in {path}",
                code="model_not_found",
                serializer=class_name,
            )
        block = match.group(1)
        if fields is not None:
            fields_repr = ", ".join(f'"{f}"' for f in fields)
            block = re.sub(
                r"(^\s+fields\s*=\s*).*$",
                rf"\g<1>[{fields_repr}]",
                block,
                count=1,
                flags=re.MULTILINE,
            )
            changes.append("fields updated")
        if read_only_fields is not None:
            ro_repr = ", ".join(f'"{f}"' for f in read_only_fields)
            if re.search(r"^\s+read_only_fields\s*=", block, re.MULTILINE):
                block = re.sub(
                    r"(^\s+read_only_fields\s*=\s*).*$",
                    rf"\g<1>[{ro_repr}]",
                    block,
                    count=1,
                    flags=re.MULTILINE,
                )
            else:
                # Insert after the fields line
                block = re.sub(
                    r"(^\s+fields\s*=.*$)",
                    rf"\g<1>\n        read_only_fields = [{ro_repr}]",
                    block,
                    count=1,
                    flags=re.MULTILINE,
                )
            changes.append("read_only_fields updated")
        content = content[: match.start(1)] + block + content[match.end(1) :]
        path.write_text(content, encoding="utf-8")
        return changes

    applied = await asyncio.to_thread(_update)
    return AgentResult(
        success=True,
        message=f"'{class_name}' updated: {', '.join(applied)}",
        data={"app": app, "serializer": class_name, "changes": applied},
    )
