"""Agent functions for model management."""

from __future__ import annotations

import asyncio
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
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
from zeeb_agents._utils.errors import AgentError, close_matches, did_you_mean
from zeeb_agents._utils.field_types import field_extra_imports, render_field_line
from zeeb_agents._utils.project import (
    list_apps,
    to_table_name,
)
from zeeb_agents._utils.validation import (
    ensure_app_exists,
    ensure_identifier,
    ensure_model_exists,
    validate_field_specs,
)


def _models_file(app: str, root: Path) -> Path:
    """Return ``apps/<app>/models.py``; fail with suggestions if the app is missing."""
    return ensure_app_exists(app, root) / "models.py"


@agent_function
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
        project_id: The host-assigned project id (required).

    Example field specs::

        [
            {"name": "title", "type": "CharField", "max_length": 200},
            {"name": "body",  "type": "TextField"},
            {"name": "published", "type": "BooleanField", "default": False},
            {"name": "author", "type": "ForeignKey", "to": "User", "on_delete": "CASCADE"},
        ]

    Relation targets (``"to"``) accept a bare model name (``"User"``), a
    Django-style dotted label (``"accounts.User"``), or ``"self"``.
    Field specs support the full zeeb_orm surface: any option renders as a
    proper Python literal (``choices=[["draft", "Draft"]]``, ``default={}``),
    relation fields validate ``on_delete``, and the reserved ``"raw"`` key
    maps kwarg names to verbatim Python source for validators/callables
    (e.g. ``{"raw": {"validators": "[validators.MinValueValidator(0)]"}}``).
    ``meta`` supports the full ``class Meta`` surface: ``table_name``,
    ``ordering``, ``unique_together``, ``index_together``, ``indexes`` /
    ``constraints`` (as list of dicts), ``abstract``, ``managed``,
    ``default_permissions``, ``app_label``.

    Returns data (on success):
        app (str): the app name.
        model (str): the model class name.
        fields (list[str]): the ``"name"`` of each field that was added.

    Notes:
        - Failures carry ``error_code`` in ``data`` (``app_not_found``,
          ``invalid_field_spec``, ``invalid_meta``, ``already_exists``, …),
          plus close-match ``suggestions`` where applicable.
        - Invalid input is rejected before anything is written.
    """
    ensure_identifier(model_name, "model name")
    validate_field_specs(fields)
    path = _models_file(app, project_root)
    if not path.exists():
        return AgentResult(success=False, message=f"models.py not found at {path}")

    def _write() -> None:
        content = path.read_text(encoding="utf-8")
        if class_exists(content, model_name):
            raise AgentError(
                f"Model '{model_name}' already exists in {path}",
                code="already_exists",
                model=model_name,
            )

        resolved_meta = meta or {"table_name": to_table_name(app, model_name)}
        class_code = render_model_class(model_name, fields, resolved_meta)
        ensure_import(path, "from zeeb_orm import Model, fields")
        for field in fields:
            for import_line in field_extra_imports(field):
                ensure_import(path, import_line)
        append_block(path, class_code)

    await asyncio.to_thread(_write)
    return AgentResult(
        success=True,
        message=f"Model '{model_name}' created in apps/{app}/models.py",
        data={"app": app, "model": model_name, "fields": [f["name"] for f in fields]},
    )


@agent_function
async def delete_model(
    app: str,
    model_name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Remove a ``Model`` subclass from ``apps/<app>/models.py``.

    Returns data (on success):
        app (str): the app name.
        model (str): the removed model class name.

    Notes:
        - ``data`` is ``None`` on failure (missing ``models.py`` or the model
          was not found).
    """
    path = _models_file(app, project_root)
    if not path.exists():
        return AgentResult(success=False, message=f"models.py not found at {path}")

    def _remove() -> None:
        content = path.read_text(encoding="utf-8")
        ensure_model_exists(content, model_name, str(path))
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


@agent_function
async def list_models(project_root: Path | None = None) -> AgentResult:
    """Return all models defined across all apps.

    Returns data (on success):
        models (list[dict]): each ``{"app": str, "model": str,
            "fields": list[str]}``.
        count (int): len(models).
    """
    root = project_root

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
        data={"models": models, "count": len(models)},
    )


@agent_function
async def add_field(
    app: str,
    model_name: str,
    field: dict,
    project_root: Path | None = None,
) -> AgentResult:
    """Add a field to an existing model.

    Args:
        field: Field spec dict with ``"name"`` and ``"type"`` keys.

    Returns data (on success):
        app (str): the app name.
        model (str): the model class name.
        field (str): the ``"name"`` of the field that was added.

    Notes:
        - ``data`` is ``None`` on failure (missing ``models.py`` or the model
          was not found).
    """
    path = _models_file(app, project_root)
    if not path.exists():
        return AgentResult(success=False, message=f"models.py not found at {path}")

    def _insert() -> None:
        content = path.read_text(encoding="utf-8")
        ensure_model_exists(content, model_name, str(path))
        field_line = render_field_line(field)
        new_content = add_field_to_class(content, model_name, field_line)
        if new_content is None:
            raise AgentError(
                f"Model '{model_name}' not found in {path}",
                code="model_not_found",
                model=model_name,
            )
        path.write_text(new_content, encoding="utf-8")
        for import_line in field_extra_imports(field):
            ensure_import(path, import_line)

    await asyncio.to_thread(_insert)
    return AgentResult(
        success=True,
        message=f"Field '{field['name']}' added to {model_name}",
        data={"app": app, "model": model_name, "field": field["name"]},
    )


@agent_function
async def remove_field(
    app: str,
    model_name: str,
    field_name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Remove a field from an existing model.

    Returns data (on success):
        app (str): the app name.
        model (str): the model class name.
        field (str): the name of the field that was removed.

    Notes:
        - ``data`` is ``None`` on failure (missing ``models.py`` or the field
          was not found in the model).
    """
    path = _models_file(app, project_root)
    if not path.exists():
        return AgentResult(success=False, message=f"models.py not found at {path}")

    def _delete() -> None:
        content = path.read_text(encoding="utf-8")
        ensure_model_exists(content, model_name, str(path))
        new_content = remove_field_from_class(content, model_name, field_name)
        if new_content is None:
            existing = extract_field_names(content, model_name)
            hint = did_you_mean(field_name, existing)
            if not hint:
                hint = f" Fields present: {', '.join(existing) or '(none)'}."
            raise AgentError(
                f"Field '{field_name}' not found in model '{model_name}' in {path}.{hint}",
                code="field_not_found",
                suggestions=close_matches(field_name, existing),
                fields=existing,
            )
        path.write_text(new_content, encoding="utf-8")

    await asyncio.to_thread(_delete)
    return AgentResult(
        success=True,
        message=f"Field '{field_name}' removed from {model_name}",
        data={"app": app, "model": model_name, "field": field_name},
    )


@agent_function
async def add_relationship(
    app: str,
    model_name: str,
    rel: dict,
    project_root: Path | None = None,
) -> AgentResult:
    """Add a relationship field to an existing model.

    Args:
        rel: Dict with ``"name"``, ``"type"`` (ForeignKey / OneToOneField /
            ManyToManyField), ``"to"`` (target model — ``"User"``,
            ``"accounts.User"`` or ``"self"``), and optional
            ``"on_delete"`` (default ``"CASCADE"``).

    Example::

        await add_relationship("blog", "Post", {
            "name": "author",
            "type": "ForeignKey",
            "to": "User",
            "on_delete": "CASCADE",
        })

    Returns data (on success):
        app (str): the app name.
        model (str): the model class name.
        field (str): the ``"name"`` of the relationship field that was added.

    Notes:
        - Delegates to :func:`add_field`, so the data shape matches it; ``data``
          is ``None`` on failure (missing ``models.py`` or the model was not
          found).
    """
    from zeeb_agents._utils.field_types import resolve_field_type

    rel = dict(rel)
    try:
        is_m2m = resolve_field_type(rel.get("type", "")) == "ManyToManyField"
    except AgentError:
        is_m2m = False  # let add_field report the unknown type
    if not is_m2m:
        rel.setdefault("on_delete", "CASCADE")
    return await add_field(app, model_name, rel, project_root)


@agent_function
async def replace_model_fields(
    app: str,
    model_name: str,
    fields: list[dict],
    project_root: Path | None = None,
) -> AgentResult:
    """Replace **all** fields in a model with a new set.

    This is a destructive operation — existing field lines are removed and
    replaced with *fields*.  ``class Meta:`` is preserved intact.

    Use :func:`add_field` / :func:`remove_field` for incremental changes.

    Args:
        app: App directory name.
        model_name: PascalCase model class name.
        fields: New list of field spec dicts (same format as :func:`create_model`).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        app (str): the app name.
        model (str): the model class name.
        fields (list[str]): the ``"name"`` of each new field (empty list when
            *fields* was empty and the body was replaced with ``pass``).

    Notes:
        - Failures carry ``error_code`` in ``data`` (``app_not_found``,
          ``model_not_found``, ``invalid_field_spec``, …) plus close-match
          ``suggestions`` where applicable.
    """
    validate_field_specs(fields)
    path = _models_file(app, project_root)
    if not path.exists():
        return AgentResult(success=False, message=f"models.py not found at {path}")

    def _replace() -> list[str]:
        import re

        content = path.read_text(encoding="utf-8")
        ensure_model_exists(content, model_name, str(path))

        lines = content.splitlines(keepends=True)
        indent = "    "
        field_pattern = re.compile(rf"^{re.escape(indent)}\w+\s*=\s*fields\.")

        # Locate class start
        class_start: int | None = None
        for i, line in enumerate(lines):
            if re.match(rf"^class {re.escape(model_name)}\b", line):
                class_start = i
                break
        if class_start is None:
            raise ValueError(f"Model '{model_name}' not found (class_start)")

        # Find where to insert (before class Meta or at class end)
        class_end = len(lines)
        meta_start: int | None = None
        for i in range(class_start + 1, len(lines)):
            line = lines[i]
            stripped = line.strip()
            if stripped and not line.startswith(indent) and not stripped.startswith("#"):
                class_end = i
                break
            if re.match(r"\s+class Meta\b", line):
                meta_start = i
                break

        insert_before = meta_start if meta_start is not None else class_end

        # Remove existing field lines between class_start+1 and insert_before
        new_lines: list[str] = []
        for i, line in enumerate(lines):
            if class_start + 1 <= i < insert_before and field_pattern.match(line):
                continue  # drop old field
            new_lines.append(line)

        # Recompute insert position after removal
        new_insert = None
        for i, line in enumerate(new_lines):
            if i <= class_start:
                continue
            if re.match(r"\s+class Meta\b", line):
                new_insert = i
                break
            stripped = line.strip()
            if stripped and not line.startswith(indent) and not stripped.startswith("#"):
                new_insert = i
                break
        if new_insert is None:
            new_insert = len(new_lines)

        # Insert new field lines
        field_lines = [f"{indent}{render_field_line(f)}\n" for f in fields]
        if not fields:
            field_lines = [f"{indent}pass\n"]
        for fi, fl in enumerate(field_lines):
            new_lines.insert(new_insert + fi, fl)

        path.write_text("".join(new_lines), encoding="utf-8")
        return [f["name"] for f in fields]

    new_field_names = await asyncio.to_thread(_replace)
    return AgentResult(
        success=True,
        message=f"Replaced all fields in '{model_name}' ({len(new_field_names)} field(s))",
        data={"app": app, "model": model_name, "fields": new_field_names},
    )


@agent_function
async def update_model(
    app: str,
    model_name: str,
    rename_to: str | None = None,
    meta_changes: dict | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Rename a model class and/or update its ``class Meta`` attributes.

    Use :func:`add_field` / :func:`remove_field` to manage individual fields.

    Returns data (on success):
        app (str): the app name.
        model (str): the post-update model name (``rename_to`` if given, else
            the original ``model_name``).
        changes (list[str]): human-readable descriptions of each change applied
            (e.g. ``"renamed to 'X'"``, ``"meta.ordering updated"``,
            ``"meta.X not found (add manually)"``); empty if nothing changed.

    Notes:
        - Failures carry ``error_code`` in ``data`` (``app_not_found``,
          ``model_not_found``, …) plus close-match ``suggestions`` where
          applicable.
        - A meta key that is not already present in the class is reported in
          ``changes`` (``"... not found (add manually)"``) rather than added.
    """
    if rename_to:
        ensure_identifier(rename_to, "model name")
    path = _models_file(app, project_root)
    if not path.exists():
        return AgentResult(success=False, message=f"models.py not found at {path}")

    def _update() -> list[str]:
        import re

        from zeeb_agents._utils.field_types import render_py_literal

        content = path.read_text(encoding="utf-8")
        ensure_model_exists(content, model_name, str(path))
        changes: list[str] = []

        if rename_to and rename_to != model_name:
            content = re.sub(
                rf"\bclass {re.escape(model_name)}\b",
                f"class {rename_to}",
                content,
            )
            changes.append(f"renamed to '{rename_to}'")

        if meta_changes:
            # Scope replacements to this class's block so a meta key in another
            # model is never touched.
            current_name = rename_to if (rename_to and rename_to != model_name) else model_name
            block_pattern = re.compile(
                rf"^(class {re.escape(current_name)}\b.*?)(?=\nclass |\Z)",
                re.MULTILINE | re.DOTALL,
            )
            block_match = block_pattern.search(content)
            if block_match is None:
                raise AgentError(
                    f"Model '{current_name}' not found in {path}",
                    code="model_not_found",
                    model=current_name,
                )
            block = block_match.group(1)
            for key, val in meta_changes.items():
                rendered = render_py_literal(val)
                pattern = re.compile(rf"(^\s+{re.escape(key)}\s*=\s*).*$", re.MULTILINE)
                if pattern.search(block):
                    block = pattern.sub(rf"\g<1>{rendered}", block)
                    changes.append(f"meta.{key} updated")
                else:
                    changes.append(f"meta.{key} not found (add manually)")
            content = content[: block_match.start(1)] + block + content[block_match.end(1):]

        if changes:
            path.write_text(content, encoding="utf-8")
        return changes

    applied = await asyncio.to_thread(_update)
    target = rename_to or model_name
    return AgentResult(
        success=True,
        message=f"Model '{target}' updated: {', '.join(applied) if applied else 'no changes'}",
        data={"app": app, "model": target, "changes": applied},
    )
