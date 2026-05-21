"""Agent functions for project configuration and environment management."""

from __future__ import annotations

import asyncio
from pathlib import Path

from zeeb_agents._utils import AgentResult
from zeeb_agents._utils.project import load_project_settings, require_project_root

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


async def get_settings(project_root: Path | None = None) -> AgentResult:
    """Return the parsed project settings as a dictionary.

    Reads from the project's ``settings.py`` / ``zeeb_settings.py``
    (whichever is detected by :func:`~zeeb_agents._utils.project.load_project_settings`).

    Args:
        project_root: Auto-detected if ``None``.
    """
    try:
        root = require_project_root(project_root)
        settings = await asyncio.to_thread(load_project_settings, root)
        return AgentResult(
            success=True,
            message=f"Loaded settings from {root}",
            data={"settings": settings},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def get_env(project_root: Path | None = None) -> AgentResult:
    """Read the project ``.env`` file and return it as a key-value dict.

    Args:
        project_root: Auto-detected if ``None``.
    """
    try:
        root = require_project_root(project_root)
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
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


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
        project_root: Auto-detected if ``None``.
    """
    try:
        root = require_project_root(project_root)
        env_path = _find_env_file(root)

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
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def delete_env(
    key: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Remove an environment variable from the project ``.env`` file.

    Args:
        key: Variable name to remove.
        project_root: Auto-detected if ``None``.
    """
    try:
        root = require_project_root(project_root)
        env_path = _find_env_file(root)

        def _remove() -> bool:
            data = _parse_env_file(env_path)
            if key not in data:
                return False
            del data[key]
            _write_env_file(env_path, data)
            return True

        removed = await asyncio.to_thread(_remove)
        if not removed:
            return AgentResult(
                success=False,
                message=f"Key '{key}' not found in .env",
                data={"key": key},
            )
        return AgentResult(
            success=True,
            message=f"Removed '{key}' from .env",
            data={"key": key},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def manage_settings(
    key: str,
    value: object = None,
    *,
    read_only: bool = False,
    project_root: "Path | None" = None,
) -> "AgentResult":
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
        project_root: Auto-detected if ``None``.
    """
    import re

    try:
        root = require_project_root(project_root)
        is_read = read_only or (value is None and not read_only)

        if is_read:
            settings = await asyncio.to_thread(load_project_settings, root)
            if key not in settings:
                return AgentResult(
                    success=False,
                    message=f"Setting '{key}' not found in settings.py",
                    data={"key": key},
                )
            return AgentResult(
                success=True,
                message=f"Read setting '{key}'",
                data={"key": key, "value": settings[key]},
            )

        # Write mode
        if not isinstance(value, _SCALAR_TYPES):
            return AgentResult(
                success=False,
                message=(
                    f"manage_settings only supports scalar values "
                    f"(str, int, float, bool, None). "
                    f"Got {type(value).__name__}. "
                    f"Use read_file/write_file to edit settings.py directly."
                ),
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
            return AgentResult(
                success=False,
                message=f"Setting '{key}' not found in settings.py (key must already exist to update it)",
                data={"key": key},
            )
        return AgentResult(
            success=True,
            message=f"Updated setting '{key}' in settings.py",
            data={"key": key, "value": value},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))
