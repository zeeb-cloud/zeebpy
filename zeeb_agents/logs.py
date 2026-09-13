"""Agent functions for reading and searching project log files."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.errors import fail
from zeeb_agents._utils.project import require_project_root

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _find_log_files(root: Path) -> list[Path]:
    """Return log files from ``logs/`` subdirectory or project root."""
    candidates: list[Path] = []
    logs_dir = root / "logs"
    if logs_dir.is_dir():
        candidates.extend(sorted(logs_dir.glob("*.log")))
    candidates.extend(f for f in sorted(root.glob("*.log")) if f not in candidates)
    return candidates


def _resolve_log_file(root: Path, log_file: str | None) -> Path | None:
    if log_file:
        p = Path(log_file)
        return p if p.is_absolute() else root / p
    files = _find_log_files(root)
    return files[0] if files else None


@agent_function
async def read_logs(
    lines: int = 200,
    level: str | None = None,
    log_file: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Return the last *lines* lines from the project log file.

    Args:
        lines: Number of tail lines to return (default 200).
        level: If set, only return lines whose text contains this log level
               (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file: Path to a specific log file.  Auto-detected if ``None``.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        path (str): log file path relative to the project root
        lines (list[str]): the tail lines (after any ``level`` filter)
        total_lines (int): total matching lines before the tail was applied

    On failure (no log file found) ``data`` carries
    ``error_code="log_file_not_found"`` plus ``searched_in`` (auto-detect)
    or ``path``/``available`` (explicit ``log_file``).

    Notes:
        - ``level`` is matched as a whole token (word boundaries), so
          ``level="ERROR"`` matches ``"[ERROR]"`` / ``" ERROR "`` but not
          ``"NOTANERROR"`` or ``"ERRORCODE"``.
        - The ``level`` filter is applied first, then the last *lines* of the
          filtered result are returned.
    """
    root = project_root

    def _read() -> dict:
        path = _resolve_log_file(root, log_file)
        if path is None or not path.exists():
            return {"path": None, "lines": [], "total_lines": 0}

        content = path.read_text(errors="replace").splitlines()
        if level:
            level_re = re.compile(rf"\b{re.escape(level.upper())}\b")
            content = [ln for ln in content if level_re.search(ln)]

        tail = content[-lines:] if len(content) > lines else content
        return {
            "path": str(path.relative_to(root)),
            "lines": tail,
            "total_lines": len(content),
        }

    result = await asyncio.to_thread(_read)
    if result["path"] is None:
        resolved_root = require_project_root(root)
        if log_file:
            available = [
                str(f.relative_to(resolved_root)) for f in _find_log_files(resolved_root)
            ]
            return fail(
                f"Log file '{log_file}' not found."
                + (f" Available log files: {', '.join(available)}" if available else ""),
                code="log_file_not_found",
                path=log_file,
                available=available,
            )
        return fail(
            "No log file found in project",
            code="log_file_not_found",
            searched_in=str(root),
        )
    empty_note = " (file is empty)" if result["total_lines"] == 0 else ""
    return AgentResult(
        success=True,
        message=f"Read {len(result['lines'])} line(s) from {result['path']}{empty_note}",
        data=result,
    )


@agent_function
async def search_logs(
    pattern: str,
    log_file: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Search log file(s) for lines matching *pattern* (regex).

    Args:
        pattern: Regular expression pattern to search for (case-insensitive).
        log_file: Path to a specific log file.  Searches all log files if ``None``.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        matches (list[dict]): each ``{"file": <rel path>, "line_no": int,
            "content": str}``
        count (int): len(matches)

    An invalid regex returns ``success=False`` with ``data=None``.
    """
    root = project_root
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return fail(f"Invalid regex pattern: {exc}", code="invalid_regex")

    def _search() -> dict:
        if log_file:
            paths = [_resolve_log_file(root, log_file)]
        else:
            paths = _find_log_files(root) or []

        matches: list[dict] = []
        for path in paths:
            if path is None or not path.exists():
                continue
            rel = str(path.relative_to(root))
            for i, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if compiled.search(line):
                    matches.append({"file": rel, "line_no": i, "content": line})
        return {"matches": matches, "count": len(matches)}

    result = await asyncio.to_thread(_search)
    return AgentResult(
        success=True,
        message=f"Found {result['count']} match(es) for pattern '{pattern}'",
        data=result,
    )


@agent_function
async def clear_logs(
    log_file: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Truncate log file(s) to zero bytes.

    Args:
        log_file: Path to a specific log file.  Clears all log files if ``None``.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        cleared (list[str]): paths (relative to root) of the truncated files

    Returns ``success=False`` with ``data=None`` when no log files exist.
    """
    root = project_root

    def _clear() -> list[str]:
        if log_file:
            paths = [_resolve_log_file(root, log_file)]
        else:
            paths = _find_log_files(root)

        cleared = []
        for path in paths:
            if path and path.exists():
                path.write_text("")
                cleared.append(str(path.relative_to(root)))
        return cleared

    cleared = await asyncio.to_thread(_clear)
    if not cleared:
        return fail("No log files found to clear", code="log_file_not_found")
    return AgentResult(
        success=True,
        message=f"Cleared {len(cleared)} log file(s): {', '.join(cleared)}",
        data={"cleared": cleared},
    )
