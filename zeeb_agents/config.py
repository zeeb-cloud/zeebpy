"""Agent functions for project configuration and environment management."""

from __future__ import annotations

import asyncio
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.errors import close_matches, fail
from zeeb_agents._utils.project import load_project_settings, require_project_root
from zeeb_agents._utils.validation import ENV_KEY_RE

_SCALAR_TYPES = (str, int, float, bool, type(None))


def _find_settings_file(root: Path) -> Path | None:
    """Return the path to the project settings.py."""
    for item in root.iterdir():
        if item.is_dir() and (item / "settings.py").exists():
            return item / "settings.py"
    return None


def _render_value(value: object) -> str:
    """Render a scalar Python value as source text."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if value is None:
        return "None"
    return str(value)


def _find_env_file(root: Path) -> Path:
    """Return the primary .env file path (creates reference path even if absent)."""
    return root / ".env"


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict, ignoring comments and blank lines."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def _write_env_file(path: Path, data: dict[str, str]) -> None:
    """Write a dict back to a .env file, preserving order."""
    path.write_text(
        "\n".join(f"{k}={v}" for k, v in data.items()) + "\n"
    )


@agent_function
async def get_settings(project_root: Path | None = None) -> AgentResult:
    """Return the parsed project settings as a dictionary.

    Reads from the project's ``settings.py`` / ``zeeb_settings.py``
    (whichever is detected by :func:`~zeeb_agents._utils.project.load_project_settings`).

    Args:
        project_id: The host-assigned project id (required).

    Returns data (on success):
        settings (dict): the parsed top-level settings as a dictionary.
    """
    root = project_root
    settings = await asyncio.to_thread(load_project_settings, root)
    return AgentResult(
        success=True,
        message=f"Loaded settings from {root}",
        data={"settings": settings},
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
    """
    if not ENV_KEY_RE.match(key):
        return fail(
            f"Invalid env key '{key}': must match [A-Za-z_][A-Za-z0-9_]*",
            code="invalid_input",
            key=key,
        )
    env_path = _find_env_file(project_root)

    def _write() -> bool:
        data = _parse_env_file(env_path)
        existed = key in data
        data[key] = value
        _write_env_file(env_path, data)
        return existed

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
    """
    env_path = _find_env_file(project_root)

    def _remove() -> bool:
        data = _parse_env_file(env_path)
        if key not in data:
            return False
        del data[key]
        _write_env_file(env_path, data)
        return True

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
    is_read = read_only or (value is None and not read_only)

    if is_read:
        settings = await asyncio.to_thread(load_project_settings, root)
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
        new_content = pattern.sub(rf"\g<1>{rendered}", content)
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
