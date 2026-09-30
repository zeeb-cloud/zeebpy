"""Confine caller-supplied paths to the project they address.

Every tool that reads, writes or deletes a file named by its caller goes through
:func:`confine_path`, so ``../../etc/passwd``, an absolute path elsewhere, or a
symlink pointing out of the project is refused before the filesystem is
touched — not only in ``files.py`` but in logs, seeds, schema exports,
requirements, generated tests and class edits too.
"""

from __future__ import annotations

from pathlib import Path

from zeeb_agents._utils.errors import AgentError


def confine_path(root: Path, path: str | Path, *, kind: str = "Path") -> Path:
    """Return *path* anchored at *root*, refusing anything that resolves outside it.

    A relative *path* is joined to *root*; an absolute one is accepted only when
    it already lies inside *root*. The check runs on the fully resolved path
    (symlinks followed), so a link inside the project that points out of it is
    refused as well. The returned path is the joined, unresolved one — callers
    report it relative to *root*.

    Raises :class:`AgentError` ``outside_project_root`` on an escape and
    ``invalid_input`` for a value that is not a path at all (empty, NUL).
    """
    text = str(path)
    if not text.strip() or "\x00" in text:
        raise AgentError(f"{kind} {text!r} is not a usable path", code="invalid_input")
    candidate = Path(text)
    full = candidate if candidate.is_absolute() else Path(root) / candidate
    if not full.resolve().is_relative_to(Path(root).resolve()):
        raise AgentError(
            f"{kind} '{text}' is outside the project root",
            code="outside_project_root",
            path=text,
        )
    return full


def relative_to_root(root: Path, path: Path) -> str:
    """*path* relative to *root* (both resolved), as a POSIX string."""
    return path.resolve().relative_to(Path(root).resolve()).as_posix()
