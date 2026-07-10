"""Agent functions for project file-system inspection and editing."""

from __future__ import annotations

import asyncio
import fnmatch
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.errors import AgentError, fail


def _resolve_path(root: Path, path: str | Path) -> Path:
    """Resolve *path* against *root*, rejecting escapes from the project root."""
    p = Path(path)
    full = p if p.is_absolute() else root / p
    resolved = full.resolve()
    if not resolved.is_relative_to(Path(root).resolve()):
        raise AgentError(
            f"Path '{path}' is outside the project root",
            code="outside_project_root",
            path=str(path),
        )
    return full


@agent_function
async def read_file(
    path: str | Path,
    project_root: Path | None = None,
) -> AgentResult:
    """Read a file from the project and return its contents.

    Args:
        path: Absolute path or path relative to ``project_root``.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        path (str): the file path, relative to the project root.
        content (str): the file contents (undecodable bytes are replaced).
        size (int): len(content), in characters.

    Notes:
        - ``data`` is ``None`` on failure (file not found, path is not a file,
          or the path escapes the project root).
    """
    root = project_root
    full = _resolve_path(root, path)

    def _read() -> str:
        return full.read_text(errors="replace")

    if not full.exists():
        return fail(f"File not found: {full}", code="file_not_found", path=str(full))
    if not full.is_file():
        return AgentResult(success=False, message=f"Path is not a file: {full}")

    content = await asyncio.to_thread(_read)
    rel = str(full.relative_to(root)) if full.is_relative_to(root) else str(full)
    return AgentResult(
        success=True,
        message=f"Read {rel} ({len(content)} chars)",
        data={"path": rel, "content": content, "size": len(content)},
    )


@agent_function
async def write_file(
    path: str | Path,
    content: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Write (or overwrite) a file in the project.

    Parent directories are created automatically.

    Args:
        path: Absolute path or path relative to ``project_root``.
        content: File content to write.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        path (str): the file path, relative to the project root.
        action (str): ``"created"`` if the file was new, ``"updated"`` if it
            already existed.
        size (int): len(content), in characters.

    Notes:
        - ``data`` is ``None`` on failure (e.g. the path escapes the project
          root).
    """
    root = project_root
    full = _resolve_path(root, path)

    def _write() -> bool:
        existed = full.exists()
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        return existed

    existed = await asyncio.to_thread(_write)
    rel = str(full.relative_to(root)) if full.is_relative_to(root) else str(full)
    action = "Updated" if existed else "Created"
    return AgentResult(
        success=True,
        message=f"{action} {rel} ({len(content)} chars)",
        data={"path": rel, "action": action.lower(), "size": len(content)},
    )


@agent_function
async def list_files(
    directory: str | Path = ".",
    pattern: str = "*",
    project_root: Path | None = None,
) -> AgentResult:
    """List files in a project directory, optionally filtered by a glob pattern.

    Args:
        directory: Directory relative to ``project_root`` (default: project root).
        pattern: Glob pattern to filter files (e.g. ``"*.py"``).  Matches
                 file names only, not full paths.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        directory (str): the listed directory, relative to the project root.
        pattern (str): the glob that was applied.
        entries (list[dict]): each ``{"name": str, "path": str (rel),
            "type": "file"|"dir", "size": int|None}``.
        count (int): len(entries).

    Notes:
        - Lists a single directory level (not recursive). Paths escaping the
          project root are rejected with an error.
    """
    root = project_root
    target = _resolve_path(root, directory)

    if not target.exists():
        return fail(f"Directory not found: {target}", code="file_not_found")
    if not target.is_dir():
        return AgentResult(success=False, message=f"Path is not a directory: {target}")

    def _list() -> list[dict]:
        entries = []
        for item in sorted(target.iterdir()):
            if not fnmatch.fnmatch(item.name, pattern):
                continue
            rel = str(item.relative_to(root))
            entries.append({
                "name": item.name,
                "path": rel,
                "type": "dir" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
            })
        return entries

    entries = await asyncio.to_thread(_list)
    rel_dir = str(target.relative_to(root)) if target.is_relative_to(root) else str(target)
    return AgentResult(
        success=True,
        message=f"Found {len(entries)} item(s) in {rel_dir}",
        data={
            "directory": rel_dir,
            "pattern": pattern,
            "entries": entries,
            "count": len(entries),
        },
    )


@agent_function
async def search_code(
    pattern: str,
    glob: str = "**/*.py",
    project_root: Path | None = None,
) -> AgentResult:
    """Search for a regex pattern across project source files.

    Args:
        pattern: Regular expression to search for.
        glob: Glob pattern for files to search (default: ``"**/*.py"``).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        pattern (str): the search pattern that was used.
        glob (str): the file glob that was applied.
        files (list[dict]): one entry per file with at least one match, each
            ``{"file": str (rel), "matches": list[{"line_no": int,
            "content": str}], "count": int}``.
        total_matches (int): sum of ``count`` across all files.

    Notes:
        - The search is case-insensitive.
        - An invalid regex returns ``success=False`` with ``data=None``.
    """
    root = project_root
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return fail(f"Invalid regex pattern: {exc}", code="invalid_regex")

    def _search() -> list[dict]:
        matches = []
        for filepath in sorted(root.glob(glob)):
            if not filepath.is_file():
                continue
            try:
                lines = filepath.read_text(errors="replace").splitlines()
            except OSError:
                continue
            file_matches = []
            for i, line in enumerate(lines, 1):
                if compiled.search(line):
                    file_matches.append({"line_no": i, "content": line})
            if file_matches:
                rel = str(filepath.relative_to(root))
                matches.append({"file": rel, "matches": file_matches, "count": len(file_matches)})
        return matches

    results = await asyncio.to_thread(_search)
    total = sum(r["count"] for r in results)
    return AgentResult(
        success=True,
        message=f"Found {total} match(es) in {len(results)} file(s) for pattern '{pattern}'",
        data={"pattern": pattern, "glob": glob, "files": results, "total_matches": total},
    )
