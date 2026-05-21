"""Agent functions for model management."""

from __future__ import annotations

import asyncio
from pathlib import Path

from zeeb_agents._utils import AgentResult
from zeeb_agents._utils.code_gen import (
    add_field_to_class,
    append_block,
    class_exists,
    ensure_import,
    extract_field_names,
    extract_model_names,
    remove_field_from_class,
    render_model_class,
)
from zeeb_agents._utils.field_types import render_field_line
from zeeb_agents._utils.project import (
    get_app_path,
    list_apps,
    require_project_root,
    to_class_name,
    to_table_name,
)


def _models_file(app: str, root: Path) -> Path:
    return get_app_path(app, root) / "models.py"


async def create_model(
    app: str,
    model_name: str,
    fields: list[dict],
    meta: dict | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Append a new ``Model`` subclass to ``apps/<app>/models.py``.

    Args:
        app: App directory name (e.g. ``"blog"``).
        model_name: PascalCase class name (e.g. ``"Post"``).
        fields: List of field spec dicts.  Each must have ``"name"`` and
            ``"type"`` keys; additional keys are passed as constructor kwargs.
        meta: Optional ``class Meta`` attributes dict
            (e.g. ``{"table_name": "blog_posts", "ordering": ["-created_at"]}``).
        project_root: Path to project root.  Auto-detected if ``None``.

    Example field specs::

        [
            {"name": "title", "type": "CharField", "max_length": 200},
            {"name": "body",  "type": "TextField"},
            {"name": "published", "type": "BooleanField", "default": False},
            {"name": "author", "type": "ForeignKey", "to": "User", "on_delete": "CASCADE"},
        ]
    """
    try:
        root = require_project_root(project_root)
        path = _models_file(app, root)
        if not path.exists():
            return AgentResult(success=False, message=f"models.py not found at {path}")

        def _write() -> None:
            content = path.read_text(encoding="utf-8")
            if class_exists(content, model_name):
                raise ValueError(f"Model '{model_name}' already exists in {path}")

            resolved_meta = meta or {"table_name": to_table_name(app, model_name)}
            class_code = render_model_class(model_name, fields, resolved_meta)
            ensure_import(path, "from zeeb_orm import Model, fields")
            append_block(path, class_code)

        await asyncio.to_thread(_write)
        return AgentResult(
            success=True,
            message=f"Model '{model_name}' created in apps/{app}/models.py",
            data={"app": app, "model": model_name, "fields": [f["name"] for f in fields]},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def delete_model(
    app: str,
    model_name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Remove a ``Model`` subclass from ``apps/<app>/models.py``."""
    try:
        root = require_project_root(project_root)
        path = _models_file(app, root)
        if not path.exists():
            return AgentResult(success=False, message=f"models.py not found at {path}")

        def _remove() -> None:
            content = path.read_text(encoding="utf-8")
            if not class_exists(content, model_name):
                raise ValueError(f"Model '{model_name}' not found in {path}")
            import re
            pattern = re.compile(
                rf"^(class {re.escape(model_name)}\b.*?)(?=\nclass |\Z)",
                re.MULTILINE | re.DOTALL,
            )
            new_content = pattern.sub("", content).rstrip("\n") + "\n"
            path.write_text(new_content, encoding="utf-8")

        await asyncio.to_thread(_remove)
        return AgentResult(
            success=True,
            message=f"Model '{model_name}' removed from apps/{app}/models.py",
            data={"app": app, "model": model_name},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def list_models(project_root: Path | None = None) -> AgentResult:
    """Return all models defined across all apps."""
    try:
        root = require_project_root(project_root)

        def _scan() -> list[dict]:
            result = []
            for app in list_apps(root):
                models_path = _models_file(app, root)
                if not models_path.exists():
                    continue
                content = models_path.read_text(encoding="utf-8")
                for model_name in extract_model_names(content):
                    result.append({
                        "app": app,
                        "model": model_name,
                        "fields": extract_field_names(content, model_name),
                    })
            return result

        models = await asyncio.to_thread(_scan)
        return AgentResult(
            success=True,
            message=f"Found {len(models)} model(s)",
            data={"models": models},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def add_field(
    app: str,
    model_name: str,
    field: dict,
    project_root: Path | None = None,
) -> AgentResult:
    """Add a field to an existing model.

    Args:
        field: Field spec dict with ``"name"`` and ``"type"`` keys.
    """
    try:
        root = require_project_root(project_root)
        path = _models_file(app, root)
        if not path.exists():
            return AgentResult(success=False, message=f"models.py not found at {path}")

        def _insert() -> None:
            content = path.read_text(encoding="utf-8")
            field_line = render_field_line(field)
            new_content = add_field_to_class(content, model_name, field_line)
            if new_content is None:
                raise ValueError(f"Model '{model_name}' not found in {path}")
            path.write_text(new_content, encoding="utf-8")

        await asyncio.to_thread(_insert)
        return AgentResult(
            success=True,
            message=f"Field '{field['name']}' added to {model_name}",
            data={"app": app, "model": model_name, "field": field["name"]},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def remove_field(
    app: str,
    model_name: str,
    field_name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Remove a field from an existing model."""
    try:
        root = require_project_root(project_root)
        path = _models_file(app, root)
        if not path.exists():
            return AgentResult(success=False, message=f"models.py not found at {path}")

        def _delete() -> None:
            content = path.read_text(encoding="utf-8")
            new_content = remove_field_from_class(content, model_name, field_name)
            if new_content is None:
                raise ValueError(
                    f"Field '{field_name}' not found in model '{model_name}' in {path}"
                )
            path.write_text(new_content, encoding="utf-8")

        await asyncio.to_thread(_delete)
        return AgentResult(
            success=True,
            message=f"Field '{field_name}' removed from {model_name}",
            data={"app": app, "model": model_name, "field": field_name},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def add_relationship(
    app: str,
    model_name: str,
    rel: dict,
    project_root: Path | None = None,
) -> AgentResult:
    """Add a relationship field to an existing model.

    Args:
        rel: Dict with ``"name"``, ``"type"`` (ForeignKey / OneToOneField /
            ManyToManyField), ``"to"`` (target model name), and optional
            ``"on_delete"`` (default ``"CASCADE"``).

    Example::

        await add_relationship("blog", "Post", {
            "name": "author",
            "type": "ForeignKey",
            "to": "User",
            "on_delete": "CASCADE",
        })
    """
    rel = dict(rel)
    rel.setdefault("on_delete", "CASCADE")
    return await add_field(app, model_name, rel, project_root)


async def update_model(
    app: str,
    model_name: str,
    rename_to: str | None = None,
    meta_changes: dict | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Rename a model class and/or update its ``class Meta`` attributes.

    Use :func:`add_field` / :func:`remove_field` to manage individual fields.
    """
    try:
        root = require_project_root(project_root)
        path = _models_file(app, root)
        if not path.exists():
            return AgentResult(success=False, message=f"models.py not found at {path}")

        def _update() -> list[str]:
            import re
            content = path.read_text(encoding="utf-8")
            changes: list[str] = []

            if rename_to and rename_to != model_name:
                new_content = re.sub(
                    rf"\bclass {re.escape(model_name)}\b",
                    f"class {rename_to}",
                    content,
                )
                content = new_content
                changes.append(f"renamed to '{rename_to}'")

            if meta_changes:
                for key, val in meta_changes.items():
                    if isinstance(val, str):
                        rendered = f'"{val}"'
                    elif isinstance(val, list):
                        rendered = "[" + ", ".join(f'"{v}"' for v in val) + "]"
                    else:
                        rendered = str(val)
                    # Replace existing meta key or note it needs manual addition
                    pattern = re.compile(rf"(^\s+{re.escape(key)}\s*=\s*).*$", re.MULTILINE)
                    if pattern.search(content):
                        content = pattern.sub(rf"\g<1>{rendered}", content)
                        changes.append(f"meta.{key} updated")
                    else:
                        changes.append(f"meta.{key} not found (add manually)")

            path.write_text(content, encoding="utf-8")
            return changes

        applied = await asyncio.to_thread(_update)
        target = rename_to or model_name
        return AgentResult(
            success=True,
            message=f"Model '{target}' updated: {', '.join(applied) if applied else 'no changes'}",
            data={"app": app, "model": target, "changes": applied},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))
