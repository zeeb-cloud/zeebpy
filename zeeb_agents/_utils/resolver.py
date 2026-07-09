"""Vendor-pluggable ``project_id`` → filesystem-path resolution.

The MCP surface addresses projects by an opaque **``project_id``** owned by the
hosting MCP server (the "vendor"). ``zeeb_agents`` itself keeps no registry: it
exposes a single seam the vendor wires once at startup::

    import zeeb_agents
    zeeb_agents.configure(project_resolver=lambda pid: Path("/srv/projects") / pid)

Every ``@agent_function`` tool then takes ``project_id`` publicly; the decorator
translates it to a ``pathlib.Path`` through :func:`resolve_project_id` before the
function body runs (the body still works with a resolved ``project_root: Path``).

Resolution rules (:func:`resolve_project_id`):

- A ``Path`` is returned unchanged — this lets internal code forward an
  already-resolved root to sibling tools without re-resolving.
- ``None`` / empty ``str`` → :class:`AgentError` ``no_project_id``.
- Any other ``str`` is passed to the configured resolver (or the built-in
  default). A missing/None result, a resolver exception, or (when
  ``must_exist``) a non-existent path → :class:`AgentError` ``project_not_found``.

The built-in default resolver maps ``project_id`` under the ``ZEEB_WORKSPACE_DIR``
environment directory (``<ZEEB_WORKSPACE_DIR>/<project_id>``). It exists so the
library is usable out-of-the-box (local dev, tests); production vendors override
it via :func:`configure`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from zeeb_agents._utils.errors import AgentError

# Type of a vendor resolver: maps an opaque project_id to a filesystem path
# (or ``None`` when the id is unknown).
ProjectResolver = Callable[[str], "Path | str | None"]

_resolver: ProjectResolver | None = None


def _default_resolver(project_id: str) -> Path | None:
    """Resolve *project_id* under ``$ZEEB_WORKSPACE_DIR``; ``None`` if unset."""
    base = os.environ.get("ZEEB_WORKSPACE_DIR")
    if not base:
        return None
    return Path(base) / project_id


def set_project_resolver(resolver: ProjectResolver | None) -> None:
    """Register the vendor's ``project_id`` → path resolver (``None`` resets)."""
    global _resolver
    _resolver = resolver


def get_project_resolver() -> ProjectResolver:
    """Return the active resolver (vendor-registered or the built-in default)."""
    return _resolver or _default_resolver


def configure(*, project_resolver: ProjectResolver | None = None) -> None:
    """Configure library-level hooks. Currently the ``project_id`` resolver.

    Intended to be called once by the hosting MCP server at startup::

        zeeb_agents.configure(project_resolver=my_lookup)
    """
    if project_resolver is not None:
        set_project_resolver(project_resolver)


def resolve_project_id(project_id: object, *, must_exist: bool = True) -> Path:
    """Resolve a ``project_id`` (or pass a ``Path`` through) to a project root.

    Raises :class:`AgentError` with ``no_project_id`` (missing) or
    ``project_not_found`` (unknown id / resolver failure / absent path).
    """
    if isinstance(project_id, Path):
        return project_id
    if project_id is None or (isinstance(project_id, str) and not project_id.strip()):
        raise AgentError(
            "No project_id provided. Pass the project_id assigned by the host.",
            code="no_project_id",
        )
    pid = str(project_id)
    resolver = get_project_resolver()
    try:
        resolved = resolver(pid)
    except Exception as exc:  # a faulty vendor resolver must not crash the tool
        raise AgentError(
            f"Could not resolve project_id '{pid}': {type(exc).__name__}: {exc}",
            code="project_not_found",
            project_id=pid,
        ) from exc
    if resolved is None:
        raise AgentError(
            f"Unknown project_id '{pid}'. No project is registered under it.",
            code="project_not_found",
            project_id=pid,
        )
    path = Path(resolved)
    if must_exist and not path.exists():
        raise AgentError(
            f"Project for id '{pid}' does not exist at {path}.",
            code="project_not_found",
            project_id=pid,
        )
    return path
