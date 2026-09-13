"""Agent functions for project file-system inspection and editing."""

from __future__ import annotations

import asyncio
import fnmatch
import re
from pathlib import Path, PurePosixPath

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


@agent_function
async def edit_file(
    path: str | Path,
    find: str,
    replace: str,
    count: int = 1,
    project_root: Path | None = None,
) -> AgentResult:
    """Replace an exact text span in a project file — the surgical alternative to ``write_file``.

    For the fixes no structural tool expresses: a broken import line, a wrong
    constant, a typo in a module that is not generated. ``find`` must occur in
    the file exactly ``count`` times; any other number is refused rather than
    guessed at, so a too-short needle can never rewrite the wrong place.

    Args:
        path: File path relative to the project root (``apps/blog/services.py``,
            ``requirements.txt``).
        find: The exact text to replace (whitespace included).
        replace: The replacement text.
        count: How many occurrences to expect and replace (default 1).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        path (str): the file path, relative to the project root.
        replacements (int): occurrences replaced (== ``count``).
        size (int): len(new content), in characters.

    Notes:
        - Fails with ``file_not_found`` when the file does not exist and
          ``invalid_input`` when ``find`` is empty or occurs a different number
          of times than ``count`` — the message says how many were found.
        - Prefer ``edit_function`` / ``set_class_method`` /
          ``set_class_attribute`` for code inside a generated class: they
          locate the span by parsing, so they cannot cut in the wrong place.
    """
    root = project_root
    if not find:
        return fail("find must not be empty.", code="invalid_input")
    if count < 1:
        return fail("count must be at least 1.", code="invalid_input")
    full = _resolve_path(root, path)
    if not full.exists():
        return fail(f"File not found: {path}", code="file_not_found", path=str(path))
    if not full.is_file():
        return fail(f"Path is not a file: {path}", code="invalid_input", path=str(path))

    def _edit() -> tuple[int, int]:
        content = full.read_text(encoding="utf-8", errors="replace")
        occurrences = content.count(find)
        if occurrences != count:
            raise AgentError(
                f"Found {occurrences} occurrence(s) of the text in {path}, expected {count} — "
                + (
                    "make find more specific, or pass count to replace every occurrence."
                    if occurrences
                    else "read the file and copy the exact text."
                ),
                code="invalid_input",
                occurrences=occurrences,
            )
        updated = content.replace(find, replace)
        full.write_text(updated, encoding="utf-8")
        return occurrences, len(updated)

    replacements, size = await asyncio.to_thread(_edit)
    rel = str(full.relative_to(root)) if full.is_relative_to(root) else str(full)
    return AgentResult(
        success=True,
        message=f"Replaced {replacements} occurrence(s) in {rel}",
        data={"path": rel, "replacements": replacements, "size": size},
    )


#: Files whose deletion breaks the project or loses its recorded state.
_PROTECTED_FILES = frozenset({"manage.py", "pyproject.toml", ".env"})
_PROTECTED_DIRS = frozenset({".zeeb", ".git"})
_PROTECTED_PACKAGE_FILES = frozenset({"settings.py", "urls.py", "asgi.py"})


def _is_protected(rel: PurePosixPath) -> bool:
    parts = rel.parts
    if str(rel) in _PROTECTED_FILES or rel.name == "__init__.py":
        return True
    if parts and parts[0] in _PROTECTED_DIRS:
        return True
    # <project>/settings.py, urls.py, asgi.py — the settings package sits at the
    # top level next to apps/.
    return len(parts) == 2 and parts[0] != "apps" and rel.name in _PROTECTED_PACKAGE_FILES


@agent_function
async def delete_file(
    path: str | Path,
    project_root: Path | None = None,
) -> AgentResult:
    """Delete one file from the project — a stray test, a bad migration, an orphaned module.

    Files the project cannot live without are refused: ``manage.py``,
    ``pyproject.toml``, ``.env``, every ``__init__.py``, the settings package's
    ``settings.py``/``urls.py``/``asgi.py``, and anything under ``.zeeb/`` or
    ``.git/``. Generated artifacts have their own removal tools
    (``delete_model``, ``delete_viewset``, ``delete_function``, …) that also
    unwire them — prefer those; this is for files no tool owns.

    Args:
        path: File path relative to the project root.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        path (str): the file path, relative to the project root.
        deleted (bool): ``False`` when the file was already absent — a skip,
            not a failure, so re-runs stay idempotent.

    Notes:
        - Fails with ``permission_denied`` for a protected file and
          ``invalid_input`` for a directory.
        - A migration that has already been applied should be rolled back
          (``run_migrations(target=...)``) before its file is deleted.
    """
    root = project_root
    full = _resolve_path(root, path)
    rel = PurePosixPath(full.resolve().relative_to(Path(root).resolve()).as_posix())
    if _is_protected(rel):
        return fail(
            f"'{rel}' is part of the project's skeleton and cannot be deleted.",
            code="permission_denied",
            path=str(rel),
        )
    if full.is_dir():
        return fail(f"Path is a directory: {rel}", code="invalid_input", path=str(rel))

    def _delete() -> bool:
        if not full.exists():
            return False
        full.unlink()
        return True

    deleted = await asyncio.to_thread(_delete)
    return AgentResult(
        success=True,
        message=f"Deleted {rel}" if deleted else f"{rel} does not exist; nothing to do",
        data={"path": str(rel), "deleted": deleted},
    )
