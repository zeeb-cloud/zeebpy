"""Static checks an agent runs after editing code: does it parse, does it import.

The runtime only reveals a broken file by refusing to start, and a startup
crash never reaches the application log. Every semantic editor in this
package therefore needs a cheap, read-only way to learn that it just broke a
file — with the file, line and column — before anything is deployed. That is
:func:`check_code`; ``verify_project`` runs it as its ``code`` check and
``diagnose_problem`` turns its findings into a root cause.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.project import require_project_root

#: The keys of one error entry, in the order they are reported.
_ERROR_KEYS = (
    "kind",
    "file",
    "line",
    "col",
    "message",
    "module",
    "exception_type",
    "missing_module",
    "traceback",
)


@agent_function
async def check_code(
    paths: list[str] | None = None,
    imports: bool = True,
    project_root: Path | None = None,
) -> AgentResult:
    """Syntax-check the project's Python files and import each project module.

    Read-only. The syntax pass compiles every file (``SyntaxError`` →
    ``file``/``line``/``col``); the import pass loads the settings, each app's
    generated modules and the project urls in a subprocess, so an
    ``ImportError`` or a crash at import time is reported with the innermost
    project frame instead of surfacing as a runtime that will not start.

    Args:
        paths: Project-relative files or directories to check. Omit for the
            whole project (``apps/``, the settings package, ``tests/``,
            ``migrations/``, top-level scripts).
        imports: Also import the project modules (default true). The import
            pass runs only once every file parses — a syntax error would fail
            every module that imports the file, so fix it first.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        ok (bool): ``True`` when no error was found.
        checked (int): number of files compiled.
        errors (list[dict]): each ``{"kind": "syntax"|"import", "file",
            "line", "col", "message", "module", "exception_type",
            "missing_module", "traceback"}`` — ``missing_module`` names the
            top-level package a ``ModuleNotFoundError`` asked for (a
            dependency to add, or a typo).
        imports_checked (bool): whether the import pass ran.

    Notes:
        - A found error does not fail the call — the report IS the result;
          ``success`` is about the check running.
        - Files under ``.venv``, ``node_modules``, ``.git`` and ``.zeeb`` are
          never checked.
    """
    root = require_project_root(project_root)

    def _run() -> dict:
        from zeeb_orm.cli.commands.check import check_code as _check_code

        return _check_code(root, imports=imports, paths=paths)

    report = await asyncio.to_thread(_run)
    errors = [
        {key: issue.get(key) for key in _ERROR_KEYS} for issue in report["issues"]
    ]
    checked = report["checked"]
    if errors:
        first = errors[0]
        where = f"{first['file']}:{first['line']}" if first.get("line") else first["file"]
        message = (
            f"{len(errors)} problem(s) in {checked} file(s) — first: {where}: {first['message']}"
        )
    else:
        message = f"{checked} file(s) parse" + (
            " and every project module imports" if report["imports_checked"] else ""
        )
    return AgentResult(
        success=True,
        message=message,
        data={
            "ok": not errors,
            "checked": checked,
            "errors": errors,
            "imports_checked": report["imports_checked"],
        },
    )
