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
    remove_class_block,
    remove_import_name,
    render_serializer_class,
    skip_result,
    validate_if_exists,
)
from zeeb_agents._utils.errors import AgentError, fail
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
    if_exists: str = "error",
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
        if_exists: ``"error"`` (default) or ``"skip"`` (succeed and change
            nothing if the serializer already exists — makes retries idempotent).
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
    validate_if_exists(if_exists)
    path = _serializers_file(app, project_root)
    if not path.exists():
        return fail(
            f"serializers.py not found at {path}", code="file_not_found", missing="serializers.py"
        )

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

    try:
        await asyncio.to_thread(_write)
    except AgentError as exc:
        if if_exists == "skip" and (exc.result.data or {}).get("error_code") == "already_exists":
            return skip_result(
                f"'{class_name}' already exists in apps/{app}/serializers.py; skipped",
                app=app,
                model=model_name,
                serializer=class_name,
            )
        raise
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
        return fail(
            f"serializers.py not found at {path}", code="file_not_found", missing="serializers.py"
        )

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


@agent_function
async def delete_serializer(
    app: str,
    model_name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Remove ``<ModelName>Serializer`` and its now-unused model import.

    The serializer half of removing an entity. Dropping the class but leaving
    ``from apps.<app>.models import <Model>`` behind breaks the module at import
    time once the model is gone, so the import name goes with it.

    Args:
        app: App directory name.
        model_name: The model whose serializer to delete (e.g. ``"Post"``).

    Returns data (on success):
        app (str): the app directory name
        serializer (str): the class name that was targeted
        skipped (bool): ``True`` when there was nothing to delete
        fields (list[str]): the ``Meta.fields`` the removed serializer
            declared, so the caller can recreate it if needed

    Notes:
        - No-ops (success, ``skipped=True``) when ``serializers.py`` or the class
          is absent, so removing an entity twice stays idempotent.
    """
    class_name = f"{model_name}Serializer"
    path = _serializers_file(app, project_root)

    def _skipped(reason: str) -> AgentResult:
        return AgentResult(
            success=True,
            message=f"'{class_name}' not deleted: {reason}",
            data={"app": app, "serializer": class_name, "skipped": True},
        )

    if not path.exists():
        return _skipped("no serializers.py")

    def _delete() -> bool:
        content = path.read_text(encoding="utf-8")
        stripped = remove_class_block(content, class_name)
        if stripped is None:
            return False
        path.write_text(remove_import_name(stripped, model_name), encoding="utf-8")
        return True

    if not await asyncio.to_thread(_delete):
        return _skipped(f"no class '{class_name}'")
    return AgentResult(
        success=True,
        message=f"Serializer '{class_name}' removed from apps/{app}/serializers.py",
        data={"app": app, "serializer": class_name, "skipped": False},
    )


#: Trailing audit columns a synced field is inserted before, so a new field lands
#: next to the model's own fields instead of after the timestamps.
_TRAILING_FIELDS = ("created_at", "updated_at")


@agent_function
async def sync_serializer_field(
    app: str,
    model_name: str,
    field_name: str,
    present: bool = True,
    project_root: Path | None = None,
) -> AgentResult:
    """Add or drop ONE field name in an existing serializer's ``Meta.fields``.

    The companion to :func:`~zeeb_agents.models.add_field` /
    :func:`~zeeb_agents.models.remove_field`: a model field that never reaches the
    serializer is invisible over the API, so writes to it are silently discarded
    (and a field removed from the model but left in ``Meta.fields`` breaks the
    endpoint). This keeps the two in lockstep.

    Deliberately surgical — it edits the single named field and leaves the rest of
    the list alone, so a hand-curated ``fields`` list survives. No-ops (rather than
    failing) when there is nothing to keep in sync: no ``serializers.py``, no such
    serializer class (e.g. a model exposed with ``expose: false``), a ``__all__``
    field list, or the field is already in the desired state.

    Args:
        app: App directory name.
        model_name: The model whose serializer to sync (e.g. ``"Post"``).
        field_name: The single field name to add or remove.
        present: ``True`` to ensure the field is listed, ``False`` to remove it.

    Returns data (on success):
        app (str): the app directory name
        serializer (str): the class name (``"<ModelName>Serializer"``)
        fields (list[str] | None): the resulting field list, ``None`` when skipped
        skipped (bool): ``True`` when there was nothing to sync
    """
    class_name = f"{model_name}Serializer"
    path = _serializers_file(app, project_root)

    def _skipped(reason: str) -> AgentResult:
        return AgentResult(
            success=True,
            message=f"'{class_name}' not synced: {reason}",
            data={"app": app, "serializer": class_name, "fields": None, "skipped": True},
        )

    if not path.exists():
        return _skipped("no serializers.py")

    def _sync() -> list[str] | None:
        content = path.read_text(encoding="utf-8")
        if not class_exists(content, class_name):
            return None
        block_pattern = re.compile(
            rf"(^class {re.escape(class_name)}\b.*?)(?=^\S|\Z)",
            re.DOTALL | re.MULTILINE,
        )
        match = block_pattern.search(content)
        if match is None:
            return None
        block = match.group(1)
        fields_match = re.search(r"^\s+fields\s*=\s*\[(.*?)\]", block, re.MULTILINE | re.DOTALL)
        if fields_match is None:
            return None
        current = re.findall(r"""["']([^"']+)["']""", fields_match.group(1))
        if "__all__" in current:
            return None

        updated = list(current)
        if present:
            if field_name in updated:
                return None
            insert_at = next(
                (i for i, name in enumerate(updated) if name in _TRAILING_FIELDS),
                len(updated),
            )
            updated.insert(insert_at, field_name)
        else:
            if field_name not in updated:
                return None
            updated = [name for name in updated if name != field_name]

        fields_repr = ", ".join(f'"{name}"' for name in updated)
        block = (
            block[: fields_match.start()]
            + re.sub(
                r"^(\s+fields\s*=\s*)\[.*?\]",
                rf"\g<1>[{fields_repr}]",
                block[fields_match.start() : fields_match.end()],
                count=1,
                flags=re.MULTILINE | re.DOTALL,
            )
            + block[fields_match.end() :]
        )
        path.write_text(
            content[: match.start(1)] + block + content[match.end(1) :], encoding="utf-8"
        )
        return updated

    result = await asyncio.to_thread(_sync)
    if result is None:
        return _skipped("nothing to sync")
    verb = "added to" if present else "removed from"
    return AgentResult(
        success=True,
        message=f"'{field_name}' {verb} {class_name}",
        data={"app": app, "serializer": class_name, "fields": result, "skipped": False},
    )
