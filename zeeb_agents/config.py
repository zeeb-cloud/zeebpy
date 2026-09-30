"""Agent functions for project configuration and environment management."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.errors import AgentError, close_matches, fail
from zeeb_agents._utils.field_types import render_py_literal
from zeeb_agents._utils.project import (
    load_project_settings,
    require_loaded_settings,
    require_project_root,
)
from zeeb_agents._utils.validation import ENV_KEY_RE
from zeeb_api.conf.env import parse_env

_SCALAR_TYPES = (str, int, float, bool, type(None))


def _find_settings_file(root: Path) -> Path | None:
    """Return the path to the project settings.py."""
    for item in root.iterdir():
        if item.is_dir() and (item / "settings.py").exists():
            return item / "settings.py"
    return None


def _render_value(value: object) -> str:
    """Render a scalar Python value as source text (a complete, escaped literal)."""
    return render_py_literal(value)


def _find_env_file(root: Path) -> Path:
    """Return the primary .env file path (creates reference path even if absent)."""
    return root / ".env"


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict — exactly as the running project reads it.

    Delegates to :func:`zeeb_api.conf.env.parse_env` (quotes removed, ``export``
    prefixes and inline comments understood), so what ``get_env`` reports is
    the value the app sees, not the raw text of the line.
    """
    if not path.exists():
        return {}
    return parse_env(path.read_text(encoding="utf-8"))


def _env_line_key(line: str) -> str | None:
    """The key a ``.env`` line assigns, or ``None`` for comments/blank/other lines."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].lstrip()
    key, sep, _ = stripped.partition("=")
    key = key.strip()
    return key if sep and ENV_KEY_RE.match(key) else None


def _render_env_value(key: str, value: str) -> str:
    """Render *value* so the project's ``.env`` parser reads back exactly *value*.

    A line break would end the assignment and start another (``"x\nDEBUG=True"``
    injected a second key), so multi-line values are refused. Otherwise the
    value is written bare when that round-trips, else single-quoted (taken
    verbatim by the parser), else double-quoted with escapes — each candidate
    is checked against :func:`zeeb_api.conf.env.parse_env` itself.
    """
    if any(ch in value for ch in "\n\r\x00"):
        raise AgentError(
            f"The value for {key} contains a line break or NUL; .env values are single "
            "lines. Encode it (e.g. base64), or write \\n escapes inside double quotes "
            "yourself if the consumer decodes them.",
            code="invalid_input",
            key=key,
        )
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    for candidate in (value, f"'{value}'", f'"{escaped}"'):
        if parse_env(f"{key}={candidate}").get(key) == value:
            return candidate
    raise AgentError(
        f"The value for {key} cannot be written to .env so that it reads back unchanged.",
        code="invalid_input",
        key=key,
    )


def _edit_env_file(path: Path, key: str, rendered: str | None) -> bool:
    """Set (*rendered*) or remove (``None``) *key* in the ``.env`` at *path*.

    Line-preserving: comments, blank lines, ordering and every other line stay
    exactly as they were; only the key's own line changes (a later duplicate of
    it is dropped, since it would win over the edit). A new file is created
    owner-readable only, like the one ``startproject`` writes. Returns whether
    the key was present before.
    """
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    out: list[str] = []
    existed = False
    for line in text.splitlines():
        if _env_line_key(line) != key:
            out.append(line)
            continue
        if rendered is not None and not existed:
            prefix = "export " if line.strip().startswith("export ") else ""
            out.append(f"{prefix}{key}={rendered}")
        existed = True
    if rendered is not None and not existed:
        out.append(f"{key}={rendered}")
    content = "\n".join(out) + ("\n" if out else "")
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
    else:
        path.write_text(content, encoding="utf-8")
    return existed


@agent_function
async def get_settings(project_root: Path | None = None) -> AgentResult:
    """Return the parsed project settings as a dictionary.

    Reads from the project's ``settings.py`` / ``zeeb_settings.py``
    (whichever is detected by :func:`~zeeb_agents._utils.project.load_project_settings`).

    Args:
        project_id: The host-assigned project id (required).

    Returns data (on success):
        settings (dict): the parsed top-level settings as a dictionary.

    Notes:
        - A ``settings.py`` that raises when executed fails the call with
          ``error_code="settings_error"`` (and the exception in ``data["error"]``)
          instead of returning defaults as if they were the project's settings.
    """
    root = project_root
    settings = await asyncio.to_thread(require_loaded_settings, root)
    return AgentResult(
        success=True,
        message=f"Loaded settings from {root}",
        data={"settings": dict(settings)},
    )


@agent_function
async def get_env(project_root: Path | None = None) -> AgentResult:
    """Read the project ``.env`` file and return it as a key-value dict.

    Args:
        project_id: The host-assigned project id (required).

    Returns data (always):
        path (str): the ``.env`` path (relative to root on success, absolute
            on failure).
        env (dict[str, str]): parsed variables (empty dict when missing).

    Notes:
        - A **missing** ``.env`` file is reported as ``success=False`` (with an
          empty ``env`` dict), not as an empty success.
    """
    root = project_root
    env_path = _find_env_file(root)

    def _read() -> dict[str, str]:
        return _parse_env_file(env_path)

    data = await asyncio.to_thread(_read)
    if not env_path.exists():
        return AgentResult(
            success=False,
            message=f"No .env file found at {env_path}",
            data={"path": str(env_path), "env": {}},
        )
    return AgentResult(
        success=True,
        message=f"Read {len(data)} variable(s) from .env",
        data={"path": str(env_path.relative_to(root)), "env": data},
    )


@agent_function
async def set_env(
    key: str,
    value: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Set (or create) an environment variable in the project ``.env`` file.

    Creates ``.env`` if it does not exist.

    Args:
        key: Variable name (e.g. ``"SECRET_KEY"``).
        value: Variable value.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        key (str): the variable name that was set.
        action (str): ``"added"`` if the key was new, ``"updated"`` if it
            already existed.

    Notes:
        - An invalid key (not ``[A-Za-z_][A-Za-z0-9_]*``) is rejected with
          ``error_code="invalid_input"`` — it would corrupt the ``.env`` file.
        - A value containing a line break (or NUL) is rejected with
          ``error_code="invalid_input"``: it would end the assignment and
          start another one. A value that would not read back unchanged bare
          (surrounding spaces, `` #``) is quoted so it does.
        - Only the key's own line changes: comments, blank lines and every
          other variable stay exactly as they were.
    """
    if not isinstance(key, str) or not ENV_KEY_RE.match(key):
        return fail(
            f"Invalid env key '{key}': must match [A-Za-z_][A-Za-z0-9_]*",
            code="invalid_input",
            key=key,
        )
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    rendered = _render_env_value(key, value)
    env_path = _find_env_file(project_root)

    def _write() -> bool:
        return _edit_env_file(env_path, key, rendered)

    existed = await asyncio.to_thread(_write)
    action = "Updated" if existed else "Added"
    return AgentResult(
        success=True,
        message=f"{action} {key} in .env",
        data={"key": key, "action": action.lower()},
    )


@agent_function
async def delete_env(
    key: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Remove an environment variable from the project ``.env`` file.

    Args:
        key: Variable name to remove.
        project_id: The host-assigned project id (required).

    Returns data (always):
        key (str): the variable name that was targeted.

    Notes:
        - A key that is not present in ``.env`` is reported as
          ``success=False`` (still with ``data={"key": key}``).
        - Only the key's line(s) go: comments, blank lines and every other
          variable stay exactly as they were.
    """
    env_path = _find_env_file(project_root)

    def _remove() -> bool:
        if not env_path.exists() or not isinstance(key, str):
            return False
        text = env_path.read_text(encoding="utf-8")
        if not any(_env_line_key(line) == key for line in text.splitlines()):
            return False
        return _edit_env_file(env_path, key, None)

    removed = await asyncio.to_thread(_remove)
    if not removed:
        existing = sorted(_parse_env_file(env_path))
        suggestions = close_matches(key, existing)
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        return fail(
            f"Key '{key}' not found in .env.{hint}",
            code="env_key_not_found",
            suggestions=suggestions,
            key=key,
        )
    return AgentResult(
        success=True,
        message=f"Removed '{key}' from .env",
        data={"key": key},
    )


@agent_function
async def manage_settings(
    key: str,
    value: object = None,
    *,
    read_only: bool = False,
    project_root: Path | None = None,
) -> AgentResult:
    """Read or update a top-level setting in ``settings.py``.

    **Read mode** — pass only ``key`` (or ``read_only=True``)::

        result = await manage_settings("DEBUG")
        print(result.data["value"])   # e.g. True

    **Write mode** — pass both ``key`` and ``value``::

        await manage_settings("DEBUG", False)
        await manage_settings("SECRET_KEY", "my-new-secret")

    Write mode only supports scalar values (``str``, ``int``, ``float``,
    ``bool``, ``None``).  For complex types (``dict``, ``list``) use
    :func:`~zeeb_agents.files.read_file` / :func:`~zeeb_agents.files.write_file`
    to edit ``settings.py`` directly.

    Args:
        key: Top-level setting name (e.g. ``"DEBUG"``, ``"SECRET_KEY"``).
        value: New value for write mode.  Pass ``None`` with ``read_only=True``
            to explicitly read a ``None``-valued setting.
        read_only: Force read mode even when ``value`` is ``None``.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        key (str): the setting name.
        value (Any): the current value (read mode) or the value just written
            (write mode).

    Both modes return the same ``{"key", "value"}`` shape. When the key is
    missing, ``success=False`` and ``data={"key": key}``.

    Notes:
        - Mode is chosen by arguments, not a flag: only ``key`` (or
          ``read_only=True``) → **read**; ``key`` + a non-``None`` ``value`` →
          **write**.
        - Write mode only updates a setting that **already exists** in
          ``settings.py``; it never creates new keys.
    """
    import re

    root = project_root
    if not isinstance(key, str) or not key.isidentifier():
        return fail(
            f"Invalid setting name {key!r}: must be a Python identifier",
            code="invalid_identifier",
            key=key if isinstance(key, str) else repr(key),
        )
    is_read = read_only or (value is None and not read_only)

    if is_read:
        settings = await asyncio.to_thread(require_loaded_settings, root)
        if key not in settings:
            suggestions = close_matches(key, sorted(settings))
            hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
            return fail(
                f"Setting '{key}' not found in settings.py.{hint}",
                code="setting_not_found",
                suggestions=suggestions,
                key=key,
            )
        return AgentResult(
            success=True,
            message=f"Read setting '{key}'",
            data={"key": key, "value": settings[key]},
        )

    # Write mode
    if not isinstance(value, _SCALAR_TYPES):
        return fail(
            f"manage_settings only supports scalar values "
            f"(str, int, float, bool, None). "
            f"Got {type(value).__name__}. "
            f"Use read_file/write_file to edit settings.py directly.",
            code="invalid_input",
            key=key,
        )

    def _write() -> bool:
        settings_file = _find_settings_file(root)
        if settings_file is None:
            raise FileNotFoundError("settings.py not found in project")
        content = settings_file.read_text(encoding="utf-8")
        rendered = _render_value(value)
        pattern = re.compile(
            rf"^({re.escape(key)}\s*=\s*).*$", re.MULTILINE
        )
        if not pattern.search(content):
            return False  # key not present
        # A function replacement: the rendered literal is inserted as-is, never
        # re-read as a regex template (where its backslashes would be escapes).
        new_content = pattern.sub(lambda m: m.group(1) + rendered, content)
        settings_file.write_text(new_content, encoding="utf-8")
        return True

    found = await asyncio.to_thread(_write)
    if not found:
        settings = await asyncio.to_thread(load_project_settings, require_project_root(root))
        suggestions = close_matches(key, sorted(settings))
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        return fail(
            f"Setting '{key}' not found in settings.py "
            f"(key must already exist to update it).{hint}",
            code="setting_not_found",
            suggestions=suggestions,
            key=key,
        )
    return AgentResult(
        success=True,
        message=f"Updated setting '{key}' in settings.py",
        data={"key": key, "value": value},
    )
