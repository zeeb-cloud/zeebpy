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
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from zeeb_agents._utils.errors import AgentError


def find_project_package(root: Path) -> Path:
    """Return the project package dir (the one holding ``settings.py``).

    Raises :class:`AgentError` (``file_not_found``) when no such directory
    exists — the caller is not inside a scaffolded Zeeb project.
    """
    for item in sorted(root.iterdir()):
        if item.is_dir() and (item / "settings.py").exists():
            return item
    raise AgentError(
        f"No project package with settings.py found under {root}.",
        code="file_not_found",
        missing="settings.py",
    )


def _installed_apps_body_span(text: str, settings_path: Path) -> tuple[int, int] | None:
    """Return ``(body_start, body_end)`` character offsets of the
    ``INSTALLED_APPS`` list body (between ``[`` and its matching ``]``),
    or ``None`` when the assignment is absent entirely.

    The span comes from the parsed AST, so a ``]`` inside a comment or string
    entry can't truncate it (the old regex stopped at the first ``]`` it saw).
    Raises :class:`AgentError` — ``invalid_input`` when the file doesn't parse,
    ``setting_not_found`` when the assignment exists but is not a plain list
    literal.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise AgentError(
            f"{settings_path} could not be parsed ({exc.msg}, line {exc.lineno}) — "
            "fix the syntax error before wiring apps.",
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
        ),
        None,
    )
    if node is None:
        return None
    if not isinstance(node.value, ast.List):
        raise AgentError(
            f"INSTALLED_APPS in {settings_path} is not a plain list literal — "
            "rewrite it as a list to let apps be wired automatically.",
            code="setting_not_found",
            setting="INSTALLED_APPS",
        )

    line_starts = [0]
    for line in text.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))
    value = node.value
    open_offset = line_starts[value.lineno - 1] + value.col_offset
    close_offset = line_starts[value.end_lineno - 1] + value.end_col_offset - 1
    return open_offset + 1, close_offset


def ensure_installed_app(root: Path, app: str) -> bool:
    """Append ``"apps.<app>"`` to ``INSTALLED_APPS`` in ``settings.py``.

    Idempotent: returns ``False`` when the entry is already present
    (uncommented). A missing ``INSTALLED_APPS`` assignment is created (repair
    semantics, mirroring ``ensure_middleware``). Raises :class:`AgentError`
    when ``settings.py`` cannot be found, does not parse (``invalid_input``),
    or holds a non-list ``INSTALLED_APPS`` (``setting_not_found``).
    """
    settings_path = find_project_package(root) / "settings.py"
    text = settings_path.read_text(encoding="utf-8")
    entry = f"apps.{app}"

    span = _installed_apps_body_span(text, settings_path)
    if span is None:
        # No assignment at all — create one with this app (repair semantics).
        updated = text.rstrip("\n") + f'\n\nINSTALLED_APPS = [\n    "{entry}",\n]\n'
        settings_path.write_text(updated, encoding="utf-8")
        return True
    body_start, body_end = span
    body = text[body_start:body_end]

    # Already registered (uncommented)?  Scan entries line by line so a
    # commented "# apps.foo" placeholder does not count as present.
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if entry in stripped:
            return False

    # Preserve the list's indentation (default four spaces).
    indent_match = re.search(r"\n([ \t]+)\S", body)
    indent = indent_match.group(1) if indent_match else "    "

    # Insert ``    "apps.<app>",\n`` just before the list's closing "]". If the
    # last entry has no trailing comma, add one — otherwise the new line would
    # implicitly concatenate with it into one string.
    before = text[:body_end]
    stripped = before.rstrip()
    if body.strip() and not stripped.endswith((",", "[")):
        before = stripped + "," + before[len(stripped):]
    lead = "" if before.endswith("\n") else "\n"
    updated = f'{before}{lead}{indent}"{entry}",\n{text[body_end:]}'
    settings_path.write_text(updated, encoding="utf-8")
    return True


def append_router_include(
    urls_path: Path,
    include_stmt: str,
    import_stmt: str | None = None,
    dedupe_pattern: str | None = None,
) -> bool:
    """Append *include_stmt* (plus *import_stmt*) to a project ``urls.py``.

    The single writer for ``router.include(...)`` statements: it verifies the
    file actually defines a module-level ``router`` symbol before emitting code
    that references it — a customized ``urls.py`` with a renamed router would
    otherwise crash at import time with a ``NameError``.

    - Idempotent: returns ``False`` when *dedupe_pattern* (a regex, when given)
      or the literal *include_stmt* is already present.
    - The import is inserted after the last top-level import; the include is
      inserted just before ``def get_routes`` (or appended when absent).
    - Raises :class:`AgentError` (``file_not_found``) when ``urls.py`` is
      missing and (``invalid_input``) when no ``router = ...`` symbol exists.
    """
    if not urls_path.exists():
        raise AgentError(
            f"Project urls.py not found at {urls_path}.",
            code="file_not_found",
            missing="urls.py",
        )
    text = urls_path.read_text(encoding="utf-8")

    if dedupe_pattern is not None:
        if re.search(dedupe_pattern, text):
            return False
    elif include_stmt in text:
        return False

    if re.search(r"^router\s*=", text, re.MULTILINE) is None:
        raise AgentError(
            f"{urls_path} does not define a module-level 'router' — it has a "
            f"customized layout this tool cannot wire safely. Add the include "
            f"manually: {include_stmt}",
            code="invalid_input",
        )

    lines = text.splitlines()
    if import_stmt is not None and import_stmt not in text:
        # Insert the import after the last top-level import line.
        last_import = 0
        for i, line in enumerate(lines):
            if line.startswith(("import ", "from ")):
                last_import = i
        lines.insert(last_import + 1, import_stmt)

    # Insert the include before ``def get_routes(`` (or append if absent).
    include_at = next(
        (i for i, line in enumerate(lines) if line.startswith("def get_routes")),
        len(lines),
    )
    # Drop back over any blank lines directly above the function definition.
    while include_at > 0 and lines[include_at - 1].strip() == "":
        include_at -= 1
    lines.insert(include_at, include_stmt)

    urls_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


_STANDARD_URLS_TEMPLATE = '''"""Project URL configuration.

Register your app routers here.
"""

from zeeb_api.routers import DefaultRouter

# Main router
router = DefaultRouter()


def get_routes():
    """Return all routes for the FastAPI app."""
    return router.routes
'''


def ensure_app_urls_included(root: Path, app: str, prefix: str | None = None) -> bool:
    """Import the app router and include it in the project ``urls.py``.

    Adds ``from apps.<app>.urls import router as <app>_router`` and
    ``router.include(<app>_router)`` (with ``prefix=`` only when *prefix* is
    given). Idempotent: returns ``False`` when the include is already present.
    A missing project ``urls.py`` is recreated from the scaffold-standard
    template first (repair semantics). Raises :class:`AgentError` when the
    file exists but defines no ``router`` symbol to include into (see
    :func:`append_router_include`).
    """
    urls_path = find_project_package(root) / "urls.py"
    if not urls_path.exists():
        urls_path.write_text(_STANDARD_URLS_TEMPLATE, encoding="utf-8")
    router_alias = f"{app}_router"

    include_stmt = (
        f'router.include({router_alias}, prefix="{prefix}")'
        if prefix
        else f"router.include({router_alias})"
    )
    return append_router_include(
        urls_path,
        include_stmt,
        import_stmt=f"from apps.{app}.urls import router as {router_alias}",
        # An existing include of this app's router (any prefix) counts.
        dedupe_pattern=rf"router\.include\(\s*{re.escape(router_alias)}\b",
    )
