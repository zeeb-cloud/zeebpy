"""Idempotent project-wiring helpers: register apps and include their routers.

These perform the two edits that make a scaffolded app actually *served*:

- :func:`ensure_installed_app` appends ``"apps.<app>"`` to ``INSTALLED_APPS`` in
  the project ``settings.py`` — without it ``make_migrations`` never sees the
  app's models (``_register_models`` walks ``INSTALLED_APPS`` only).
- :func:`ensure_app_urls_included` imports the app router and includes it in the
  project ``urls.py`` — without it every ``router.register(...)`` an app makes is
  never routed and every endpoint 404s.

The app router is included with **no prefix**: ``register_route`` already mounts
each ViewSet under its own URL segment (the app name by default, or an explicit
``url_prefix``), and :meth:`DefaultRouter.include` *nests* prefixes — so adding a
prefix here would double it (``/blog/blog/``).

Both functions are safe to re-run (grep-before-write, mirroring the
``_ensure_middleware`` pattern in ``auth_scaffold``) and return ``True`` only
when they actually changed the file.
"""

from __future__ import annotations

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


def ensure_installed_app(root: Path, app: str) -> bool:
    """Append ``"apps.<app>"`` to ``INSTALLED_APPS`` in ``settings.py``.

    Idempotent: returns ``False`` when the entry is already present
    (uncommented). Raises :class:`AgentError` when ``settings.py`` or its
    ``INSTALLED_APPS`` list cannot be found.
    """
    settings_path = find_project_package(root) / "settings.py"
    text = settings_path.read_text(encoding="utf-8")
    entry = f"apps.{app}"

    # Locate the INSTALLED_APPS list body (from "[" to the matching "]").
    m = re.search(r"INSTALLED_APPS\s*=\s*\[(.*?)\]", text, re.DOTALL)
    if m is None:
        raise AgentError(
            f"Could not find INSTALLED_APPS list in {settings_path}.",
            code="setting_not_found",
            setting="INSTALLED_APPS",
        )
    body = m.group(1)

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

    # Insert ``    "apps.<app>",\n`` just before the list's closing "]".
    insert_at = m.end(1)
    lead = "" if text[:insert_at].endswith("\n") else "\n"
    updated = f'{text[:insert_at]}{lead}{indent}"{entry}",\n{text[insert_at:]}'
    settings_path.write_text(updated, encoding="utf-8")
    return True


def ensure_app_urls_included(root: Path, app: str, prefix: str | None = None) -> bool:
    """Import the app router and include it in the project ``urls.py``.

    Adds ``from apps.<app>.urls import router as <app>_router`` and
    ``router.include(<app>_router)`` (with ``prefix=`` only when *prefix* is
    given). Idempotent: returns ``False`` when the include is already present.
    Raises :class:`AgentError` when the project ``urls.py`` cannot be found.
    """
    urls_path = find_project_package(root) / "urls.py"
    if not urls_path.exists():
        raise AgentError(
            f"Project urls.py not found at {urls_path}.",
            code="file_not_found",
            missing="urls.py",
        )
    text = urls_path.read_text(encoding="utf-8")
    router_alias = f"{app}_router"

    include_stmt = (
        f'router.include({router_alias}, prefix="{prefix}")'
        if prefix
        else f"router.include({router_alias})"
    )
    import_stmt = f"from apps.{app}.urls import router as {router_alias}"

    # Idempotent: an existing include of this app's router (any prefix) counts.
    if re.search(rf"router\.include\(\s*{re.escape(router_alias)}\b", text):
        return False

    lines = text.splitlines()
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
