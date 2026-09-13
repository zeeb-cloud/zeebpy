"""Removal and in-place editing for the custom logic a FeatureSpec's ``functions``
block generates.

A feature declares its business logic as functions — endpoint actions, standalone
routes, model hooks, background tasks, permission rules — and the compiler turns
each into the same code the per-object tools write.  This module is the other
direction: one call that removes any of them, and one call that replaces the
body of any of them, whichever file the kind happens to live in.

They exist as single tools rather than five each (``delete_route``,
``remove_viewset_action``, ``edit_permission_class``, …) because the spec
already names these things one way. Removal and editing that speak the same
vocabulary are one fact to know instead of ten.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.code_gen import (
    ensure_import,
    remove_class_block,
    remove_method_from_class,
    remove_route_function,
    replace_function_body,
)
from zeeb_agents._utils.errors import AgentError, close_matches, fail
from zeeb_agents._utils.project import get_app_path
from zeeb_agents._utils.validation import ensure_app_exists

#: Function kind → the file its code lives in, relative to ``apps/<app>/``.
FUNCTION_FILES = {
    "action": "views.py",
    "endpoint": "views.py",
    "hook": "signals.py",
    "task": "tasks.py",
    "rule": "permissions.py",
}


@agent_function
async def delete_function(
    app: str,
    name: str,
    kind: str = "action",
    entity: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Remove one generated function — action, endpoint, hook, task, or rule.

    The inverse of a FeatureSpec ``functions`` entry. Each kind is removed from
    wherever that kind lives, and nothing else in the file is disturbed: an
    ``action`` is cut from its own ViewSet's body, so a same-named action on
    another entity in the same ``views.py`` survives.

    Args:
        app: App directory name.
        name: The function name (for ``kind="rule"``, the permission class
            name).
        kind: One of ``action``, ``endpoint``, ``hook``, ``task``, ``rule``.
            Defaults to ``action``.
        entity: The entity whose endpoint the action belongs to — required for
            ``kind="action"``, ignored otherwise.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        app (str): the app directory name.
        name (str): the function that was removed.
        kind (str): the kind that was removed.
        file (str): project-relative file it was removed from.
        removed (bool): ``False`` when it was already gone — a skip, not a
            failure, so re-runs stay idempotent.

    Notes:
        - Fails with ``invalid_input`` for an unknown ``kind``, or for
          ``kind="action"`` without ``entity``.
        - Fails with ``app_not_found`` when the app does not exist.
        - A missing file or missing function is reported as
          ``removed: false`` with ``success: true``.
    """
    root = project_root
    if kind not in FUNCTION_FILES:
        return fail(
            f"Unknown function kind '{kind}'.",
            code="invalid_input",
            suggestions=close_matches(kind, sorted(FUNCTION_FILES)) or sorted(FUNCTION_FILES),
        )
    ensure_app_exists(app, root)
    if kind == "action" and not entity:
        return fail(
            "Removing an action needs 'entity' — the entity whose endpoint "
            "defines it.",
            code="invalid_input",
        )

    filename = FUNCTION_FILES[kind]
    path = get_app_path(app, root) / filename
    rel = f"apps/{app}/{filename}"

    def _remove() -> bool:
        if not path.is_file():
            return False
        content = path.read_text(encoding="utf-8")
        if kind == "action":
            updated = remove_method_from_class(content, f"{entity}ViewSet", name)
        elif kind == "rule":
            updated = remove_class_block(content, name)
        else:
            updated = remove_route_function(content, name)
        if updated is None:
            return False
        path.write_text(updated, encoding="utf-8")
        return True

    removed = await asyncio.to_thread(_remove)
    return AgentResult(
        success=True,
        message=(
            f"Removed {kind} '{name}' from {rel}"
            if removed
            else f"{kind.capitalize()} '{name}' was not defined in {rel}; nothing to do"
        ),
        data={
            "app": app,
            "name": name,
            "kind": kind,
            "file": rel,
            "removed": removed,
        },
    )


def _defined_names(content: str, kind: str, entity: str | None) -> list[str]:
    """The names an ``edit_function`` call could have meant, for suggestions."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    if kind == "action":
        cls = next(
            (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == f"{entity}ViewSet"),
            None,
        )
        body = cls.body if cls else []
        return [n.name for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if kind == "rule":
        return [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    return [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


@agent_function
async def edit_function(
    app: str,
    name: str,
    body: str,
    kind: str = "action",
    entity: str | None = None,
    imports: list[str] | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Replace the body of one generated function — action, endpoint, hook, task, or rule.

    The mirror of :func:`delete_function`: the same vocabulary, the other
    direction. Only the statements change — the decorator (``@action``,
    ``@router.get``, ``@receiver``, …) and the signature stay exactly as they
    are, so an endpoint keeps its route, methods and permissions while its
    logic is fixed in place. For ``kind="rule"`` the body of the permission
    class's ``has_permission`` method is replaced.

    Prefer this over rewriting ``views.py`` with ``write_file``: it touches one
    function and leaves every other artifact in the file alone.

    Args:
        app: App directory name.
        name: The function name (for ``kind="rule"``, the permission class
            name).
        body: The new function body, at any indentation — nested blocks are
            re-indented, never flattened. Empty becomes ``pass``.
        kind: One of ``action``, ``endpoint``, ``hook``, ``task``, ``rule``.
            Defaults to ``action``.
        entity: The entity whose endpoint the action belongs to — required for
            ``kind="action"``, ignored otherwise.
        imports: Import lines the new body needs (``"from datetime import
            date"``); each is added once, at the top of the file.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        app (str): the app directory name.
        name (str): the function whose body was replaced.
        kind (str): the kind that was edited.
        file (str): project-relative file that was edited.
        replaced (bool): always ``True``.
        imports_added (list[str]): the import lines that were not already
            present.

    Notes:
        - Fails with ``invalid_input`` for an unknown ``kind``, for
          ``kind="action"`` without ``entity``, or when the file does not
          parse (repair it with ``edit_file`` first — the message names the
          line).
        - Fails with ``app_not_found`` / ``file_not_found`` when the app or
          the file does not exist, and ``function_not_found`` (with close-match
          ``suggestions``) when the function is not defined there.
    """
    root = project_root
    if kind not in FUNCTION_FILES:
        return fail(
            f"Unknown function kind '{kind}'.",
            code="invalid_input",
            suggestions=close_matches(kind, sorted(FUNCTION_FILES)) or sorted(FUNCTION_FILES),
        )
    ensure_app_exists(app, root)
    if kind == "action" and not entity:
        return fail(
            "Editing an action needs 'entity' — the entity whose endpoint defines it.",
            code="invalid_input",
        )

    filename = FUNCTION_FILES[kind]
    path = get_app_path(app, root) / filename
    rel = f"apps/{app}/{filename}"
    if not path.is_file():
        return fail(f"{rel} does not exist.", code="file_not_found", missing=filename)

    def _edit() -> list[str]:
        content = path.read_text(encoding="utf-8")
        try:
            ast.parse(content)
        except SyntaxError as exc:
            raise AgentError(
                f"{rel} does not parse (line {exc.lineno}: {exc.msg}) — repair it with "
                "edit_file before editing it structurally.",
                code="invalid_input",
                file=rel,
                line=exc.lineno,
            ) from exc
        if kind == "action":
            updated = replace_function_body(content, name, body, class_name=f"{entity}ViewSet")
        elif kind == "rule":
            updated = replace_function_body(content, "has_permission", body, class_name=name)
        else:
            updated = replace_function_body(content, name, body)
        if updated is None:
            known = _defined_names(content, kind, entity)
            raise AgentError(
                f"{kind.capitalize()} '{name}' is not defined in {rel}.",
                code="function_not_found",
                suggestions=close_matches(name, known),
            )
        path.write_text(updated, encoding="utf-8")
        added: list[str] = []
        for line in imports or []:
            before = path.read_text(encoding="utf-8")
            ensure_import(path, line)
            if path.read_text(encoding="utf-8") != before:
                added.append(line)
        return added

    imports_added = await asyncio.to_thread(_edit)
    return AgentResult(
        success=True,
        message=f"Replaced the body of {kind} '{name}' in {rel}",
        data={
            "app": app,
            "name": name,
            "kind": kind,
            "file": rel,
            "replaced": True,
            "imports_added": imports_added,
        },
    )
