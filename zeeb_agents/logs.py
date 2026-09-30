"""Agent functions for reading and searching project log files."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.errors import fail
from zeeb_agents._utils.paths import confine_path
from zeeb_agents._utils.project import require_project_root

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

#: Severity rank of every level spelling a log line may carry.
_SEVERITY = {
    "DEBUG": 10,
    "INFO": 20,
    "WARN": 30,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
    "FATAL": 50,
}
_LEVEL_TOKEN_RE = re.compile(r"\b(" + "|".join(_SEVERITY) + r")\b")


def _at_or_above(lines: list[str], floor: int) -> list[str]:
    """The lines of every record at *floor* severity or above.

    A line with no level of its own (a traceback frame, a wrapped message)
    belongs to the record before it and is kept or dropped with it — so an
    ERROR comes back with its traceback. Lines before the first leveled one
    are kept: there is nothing to judge them by, and hiding output is the
    worse mistake.
    """
    kept: list[str] = []
    current: int | None = None
    for line in lines:
        found = _LEVEL_TOKEN_RE.search(line)
        if found:
            current = _SEVERITY[found.group(1)]
        if current is None or current >= floor:
            kept.append(line)
    return kept


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
        # Confined like every other caller path: clear_logs truncates what this
        # returns, so an absolute or ``..`` path used to empty any file.
        return confine_path(root, log_file, kind="log_file")
    files = _find_log_files(root)
    return files[0] if files else None


@agent_function
async def read_logs(
    lines: int = 200,
    level: str | None = None,
    log_file: str | None = None,
    project_root: Path | None = None,
    min_level: str | None = None,
) -> AgentResult:
    """Return the last *lines* lines from the project log file.

    Args:
        lines: Number of tail lines to return (default 200).
        level: If set, only return lines whose text contains this log level
               (DEBUG, INFO, WARNING, ERROR, CRITICAL) — exactly that level.
        log_file: Path to a specific log file.  Auto-detected if ``None``.
        project_id: The host-assigned project id (required).
        min_level: If set, return the records at this severity **or above**
            (``"WARNING"`` → WARNING, ERROR, CRITICAL), each with its
            continuation lines (tracebacks). Accepts DEBUG, INFO, WARN/WARNING,
            ERROR, CRITICAL/FATAL. Mutually exclusive with *level*.

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
        - The ``level`` / ``min_level`` filter is applied first, then the last
          *lines* of the filtered result are returned.
        - An unknown ``min_level``, or both filters at once, fails with
          ``error_code="invalid_input"``.
    """
    root = project_root
    floor: int | None = None
    if min_level is not None:
        if level:
            return fail("Pass either level or min_level, not both.", code="invalid_input")
        floor = _SEVERITY.get(str(min_level).strip().upper())
        if floor is None:
            return fail(
                f"Unknown log level '{min_level}'.",
                code="invalid_input",
                suggestions=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            )

    def _read() -> dict:
        path = _resolve_log_file(root, log_file)
        if path is None or not path.exists():
            return {"path": None, "lines": [], "total_lines": 0}

        content = path.read_text(errors="replace").splitlines()
        if level:
            level_re = re.compile(rf"\b{re.escape(level.upper())}\b")
            content = [ln for ln in content if level_re.search(ln)]
        elif floor is not None:
            content = _at_or_above(content, floor)

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
