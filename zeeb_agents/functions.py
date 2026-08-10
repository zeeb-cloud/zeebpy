"""Removal for the custom logic a FeatureSpec's ``functions`` block generates.

A feature declares its business logic as functions — endpoint actions, standalone
routes, model hooks, background tasks, permission rules — and the compiler turns
each into the same code the per-object tools write.  This module is the other
direction: one call that removes any of them, whichever file the kind happens to
live in.

It exists as a single tool rather than five (``delete_route``,
``remove_viewset_action``, ``delete_permission_class``, …) because the spec
already names these things one way. Removal that speaks the same vocabulary is
one fact to know instead of five.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.code_gen import (
    remove_class_block,
    remove_method_from_class,
    remove_route_function,
)
from zeeb_agents._utils.errors import close_matches, fail
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
