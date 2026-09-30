"""Idempotent project-wiring helpers: register apps and include their routers.

These perform the two edits that make a scaffolded app actually *served*:

- :func:`ensure_installed_app` appends ``"apps.<app>"`` to ``INSTALLED_APPS`` in
  the project ``settings.py`` — without it ``make_migrations`` never sees the
  app's models (``_register_models`` walks ``INSTALLED_APPS`` only).
- :func:`ensure_app_urls_included` imports the app router and includes it in the
  project ``urls.py`` — without it every ``router.register(...)`` an app makes is
  never routed and every endpoint 404s.

The app router is included with **no prefix**: ``register_route`` already mounts
each ViewSet under its own URL segment (the pluralized lowercase model name by
default, or an explicit ``url_prefix``), and :meth:`DefaultRouter.include`
*nests* prefixes — so adding a prefix here would double it (``/posts/posts/``).

Both functions are safe to re-run (grep-before-write, mirroring the
``_ensure_middleware`` pattern in ``auth_scaffold``) and return ``True`` only
when they actually changed the file.

The implementation lives in :mod:`zeeb_orm.scaffold.wiring` so the CLI
(``zeeb startapp``) and the agent layer share one code path. This module is the
adapter that re-raises the scaffolding layer's :class:`ScaffoldError` as an
:class:`AgentError` with the identical ``code`` and payload.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

from zeeb_agents._utils.errors import AgentError
from zeeb_orm.scaffold import wiring as _wiring
from zeeb_orm.scaffold.errors import ScaffoldError

__all__ = [
    "append_router_include",
    "remove_app_urls",
    "remove_installed_app",
    "ensure_app_urls_included",
    "ensure_installed_app",
    "find_project_package",
]


def _agent_facing(fn):
    """Re-raise :class:`ScaffoldError` as :class:`AgentError`, code intact."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ScaffoldError as exc:
            raise AgentError(str(exc), code=exc.code, **exc.data) from exc

    return wrapper


find_project_package = _agent_facing(_wiring.find_project_package)
ensure_installed_app = _agent_facing(_wiring.ensure_installed_app)
append_router_include = _agent_facing(_wiring.append_router_include)
ensure_app_urls_included = _agent_facing(_wiring.ensure_app_urls_included)

# Private helpers a few callers and tests still reach for.
_installed_apps_body_span = _agent_facing(_wiring._installed_apps_body_span)
_code_before_comment = _wiring._code_before_comment
_STANDARD_URLS_TEMPLATE = _wiring.STANDARD_URLS_TEMPLATE


# ---------------------------------------------------------------------------
# Unwiring — the reverse of ``ensure_installed_app`` + ``ensure_app_urls_included``.
# Lives in the agent layer: only ``delete_app`` needs it.
# ---------------------------------------------------------------------------


def _is_app_entry(value: object, app: str) -> bool:
    module = f"apps.{app}"
    return isinstance(value, str) and (value == module or value.startswith(f"{module}."))


def _drop_lines(text: str, spans: list[tuple[int, int]]) -> str:
    """Remove the 1-based inclusive line *spans* from *text*."""
    drop = {n for first, last in spans for n in range(first, last + 1)}
    lines = text.splitlines(keepends=True)
    return "".join(line for number, line in enumerate(lines, start=1) if number not in drop)


def remove_installed_app(root: Path, app: str) -> bool:
    """Take ``"apps.<app>"`` (and ``"apps.<app>.*"``) out of ``INSTALLED_APPS``.

    Located through the AST. An entry on a line of its own is removed with its
    line, leaving every other line — comments included — as it was; a list
    whose entries share lines is re-rendered from the remaining entries' own
    source. Returns whether ``settings.py`` changed. Raises :class:`AgentError`
    (``invalid_input``) when ``settings.py`` does not parse.
    """
    from zeeb_agents._utils.code_gen import write_source

    settings_path = find_project_package(root) / "settings.py"
    text = settings_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise AgentError(
            f"{settings_path.name} does not parse (line {exc.lineno}: {exc.msg}); "
            "INSTALLED_APPS could not be updated.",
            code="invalid_input",
        ) from exc
    node = next(
        (
            n
            for n in tree.body
            if isinstance(n, ast.Assign)
            and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == "INSTALLED_APPS"
            and isinstance(n.value, (ast.List, ast.Tuple))
        ),
        None,
    )
    if node is None:
        return False
    elts = node.value.elts
    doomed = [e for e in elts if isinstance(e, ast.Constant) and _is_app_entry(e.value, app)]
    if not doomed:
        return False
    kept = [e for e in elts if e not in doomed]

    def _own_lines(element: ast.expr) -> bool:
        """Whether *element* shares none of its lines with a bracket or another entry."""
        first, last = element.lineno, element.end_lineno or element.lineno
        if first == node.value.lineno or last == (node.value.end_lineno or last):
            return False
        return not any(
            other is not element
            and other.lineno <= last
            and (other.end_lineno or other.lineno) >= first
            for other in elts
        )

    if all(_own_lines(e) for e in doomed):
        updated = _drop_lines(text, [(e.lineno, e.end_lineno or e.lineno) for e in doomed])
    else:
        entries = "".join(f"    {ast.get_source_segment(text, e)},\n" for e in kept)
        opener, closer = ("[", "]") if isinstance(node.value, ast.List) else ("(", ")")
        rendered = f"INSTALLED_APPS = {opener}\n{entries}{closer}\n"
        lines = text.splitlines(keepends=True)
        updated = "".join(
            [*lines[: node.lineno - 1], rendered, *lines[node.end_lineno or node.lineno :]]
        )
    write_source(settings_path, updated)
    return True


def remove_app_urls(root: Path, app: str) -> bool:
    """Remove the app's router import and ``router.include(...)`` from project ``urls.py``.

    AST-located: the ``from apps.<app>.urls import router as <alias>`` import
    and every top-level ``<x>.include(<alias>, …)`` of a name it bound. Returns
    whether ``urls.py`` changed; a missing or unparseable ``urls.py`` is left
    alone (``False``).
    """
    from zeeb_agents._utils.code_gen import write_source

    try:
        urls_path = find_project_package(root) / "urls.py"
    except AgentError:
        return False
    if not urls_path.exists():
        return False
    text = urls_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    module = f"apps.{app}"
    spans: list[tuple[int, int]] = []
    aliases: set[str] = {f"{app}_router"}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and (
            node.module == module or node.module.startswith(f"{module}.")
        ):
            aliases.update(alias.asname or alias.name for alias in node.names)
            spans.append((node.lineno, node.end_lineno or node.lineno))
    for node in tree.body:
        call = node.value if isinstance(node, ast.Expr) else None
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "include"
            and call.args
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id in aliases
        ):
            spans.append((node.lineno, node.end_lineno or node.lineno))
    if not spans:
        return False
    write_source(urls_path, _drop_lines(text, spans))
    return True
