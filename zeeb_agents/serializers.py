"""Agent functions for serializer management."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult
from zeeb_agents._utils.code_gen import (
    append_block,
    class_exists,
    ensure_import,
    render_serializer_class,
)
from zeeb_agents._utils.project import get_app_path, require_project_root


def _serializers_file(app: str, root: Path) -> Path:
    return get_app_path(app, root) / "serializers.py"


async def create_serializer(
    app: str,
    model_name: str,
    fields: list[str] | None = None,
    read_only_fields: list[str] | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Append a ``ModelSerializer`` subclass to ``apps/<app>/serializers.py``.

    Args:
        app: App directory name.
        model_name: The model this serializer is for (e.g. ``"Post"``).
        fields: Field names to include.  Defaults to ``["__all__"]``.
        read_only_fields: Fields that are read-only (e.g. ``["id", "created_at"]``).
        project_root: Auto-detected if ``None``.

    Example::

        await create_serializer("blog", "Post",
            fields=["id", "title", "body", "created_at"],
            read_only_fields=["id", "created_at"])
    """
    try:
        root = require_project_root(project_root)
        path = _serializers_file(app, root)
        if not path.exists():
            return AgentResult(success=False, message=f"serializers.py not found at {path}")

        class_name = f"{model_name}Serializer"

        def _write() -> None:
            content = path.read_text(encoding="utf-8")
            if class_exists(content, class_name):
                raise ValueError(f"'{class_name}' already exists in {path}")
            class_code = render_serializer_class(model_name, fields, read_only_fields)
            ensure_import(path, "from zeeb_api import serializers")
            ensure_import(path, "from zeeb_api.serializers import ModelSerializer")
            ensure_import(path, f"from .models import {model_name}")
            append_block(path, class_code)

        await asyncio.to_thread(_write)
        return AgentResult(
            success=True,
            message=f"'{class_name}' created in apps/{app}/serializers.py",
            data={"app": app, "model": model_name, "serializer": class_name},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def update_serializer(
    app: str,
    model_name: str,
    fields: list[str] | None = None,
    read_only_fields: list[str] | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Update the ``fields`` and/or ``read_only_fields`` of an existing serializer.

    Replaces the ``class Meta`` ``fields`` / ``read_only_fields`` lines in place.
    """
    try:
        root = require_project_root(project_root)
        path = _serializers_file(app, root)
        if not path.exists():
            return AgentResult(success=False, message=f"serializers.py not found at {path}")

        class_name = f"{model_name}Serializer"

        def _update() -> list[str]:
            content = path.read_text(encoding="utf-8")
            if not class_exists(content, class_name):
                raise ValueError(f"'{class_name}' not found in {path}")
            changes: list[str] = []
            if fields is not None:
                fields_repr = ", ".join(f'"{f}"' for f in fields)
                content = re.sub(
                    r"(^\s+fields\s*=\s*).*$",
                    rf"\g<1>[{fields_repr}]",
                    content,
                    flags=re.MULTILINE,
                )
                changes.append("fields updated")
            if read_only_fields is not None:
                ro_repr = ", ".join(f'"{f}"' for f in read_only_fields)
                if re.search(r"^\s+read_only_fields\s*=", content, re.MULTILINE):
                    content = re.sub(
                        r"(^\s+read_only_fields\s*=\s*).*$",
                        rf"\g<1>[{ro_repr}]",
                        content,
                        flags=re.MULTILINE,
                    )
                else:
                    # Insert after the fields line
                    content = re.sub(
                        r"(^\s+fields\s*=.*$)",
                        rf"\g<1>\n        read_only_fields = [{ro_repr}]",
                        content,
                        flags=re.MULTILINE,
                    )
                changes.append("read_only_fields updated")
            path.write_text(content, encoding="utf-8")
            return changes

        applied = await asyncio.to_thread(_update)
        return AgentResult(
            success=True,
            message=f"'{class_name}' updated: {', '.join(applied)}",
            data={"app": app, "serializer": class_name, "changes": applied},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))
