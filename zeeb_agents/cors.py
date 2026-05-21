"""Agent functions for CORS configuration in zeeb_api projects."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult
from zeeb_agents._utils.project import load_project_settings, require_project_root

_CORS_KEYS = (
    "CORS_ALLOW_ORIGINS",
    "CORS_ALLOW_METHODS",
    "CORS_ALLOW_HEADERS",
    "CORS_ALLOW_CREDENTIALS",
    "CORS_EXPOSE_HEADERS",
)


def _find_settings_file(root: Path) -> Path | None:
    for item in root.iterdir():
        if item.is_dir() and (item / "settings.py").exists():
            return item / "settings.py"
    return None


def _render_list(values: list[str]) -> str:
    items = ", ".join(f'"{v}"' for v in values)
    return f"[{items}]"


def _set_or_append_setting(content: str, key: str, rendered: str) -> str:
    """Replace an existing KEY = ... line or append it at end of file."""
    pattern = re.compile(rf"^{re.escape(key)}\s*=\s*.*$", re.MULTILINE)
    new_line = f"{key} = {rendered}"
    if pattern.search(content):
        return pattern.sub(new_line, content)
    return content.rstrip("\n") + f"\n{new_line}\n"


async def configure_cors(
    origins: list[str],
    methods: list[str] | None = None,
    allow_credentials: bool = True,
    allow_headers: list[str] | None = None,
    expose_headers: list[str] | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Write CORS settings to the project's ``settings.py``.

    Sets ``CORS_ALLOW_ORIGINS``, ``CORS_ALLOW_METHODS``,
    ``CORS_ALLOW_CREDENTIALS``, and optionally ``CORS_ALLOW_HEADERS``
    and ``CORS_EXPOSE_HEADERS``.

    The ``zeeb_api.middleware.CORSMiddleware`` class reads these keys
    automatically when added to ``MIDDLEWARE`` in settings.

    Args:
        origins: List of allowed origins, e.g. ``["http://localhost:3000"]``.
            Use ``["*"]`` to allow all origins.
        methods: Allowed HTTP methods.  Defaults to ``["*"]``.
        allow_credentials: Whether to allow credentials.  Defaults to ``True``.
        allow_headers: Allowed request headers.  Defaults to ``["*"]``.
        expose_headers: Headers to expose to the browser.  Defaults to ``[]``.
        project_root: Auto-detected if ``None``.

    Example::

        await configure_cors(
            origins=["https://myapp.com", "http://localhost:3000"],
            methods=["GET", "POST", "PUT", "DELETE"],
        )
    """
    try:
        root = require_project_root(project_root)
        settings_path = await asyncio.to_thread(_find_settings_file, root)
        if not settings_path:
            return AgentResult(success=False, message="settings.py not found in project.")

        def _write() -> dict:
            content = settings_path.read_text(encoding="utf-8")

            updates = {
                "CORS_ALLOW_ORIGINS": _render_list(origins),
                "CORS_ALLOW_METHODS": _render_list(methods if methods is not None else ["*"]),
                "CORS_ALLOW_CREDENTIALS": str(allow_credentials),
                "CORS_ALLOW_HEADERS": _render_list(allow_headers if allow_headers is not None else ["*"]),
            }
            if expose_headers is not None:
                updates["CORS_EXPOSE_HEADERS"] = _render_list(expose_headers)

            for key, value in updates.items():
                content = _set_or_append_setting(content, key, value)

            settings_path.write_text(content, encoding="utf-8")
            return updates

        written = await asyncio.to_thread(_write)
        return AgentResult(
            success=True,
            message=f"CORS settings updated in {settings_path.name} ({len(written)} key(s)).",
            data={"settings_file": str(settings_path.relative_to(root)), "keys_written": list(written.keys())},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def get_cors_config(
    project_root: Path | None = None,
) -> AgentResult:
    """Read the current CORS settings from the project.

    Returns all ``CORS_*`` keys currently defined in settings.

    Args:
        project_root: Auto-detected if ``None``.
    """
    try:
        root = require_project_root(project_root)
        settings = await asyncio.to_thread(load_project_settings, root)
        cors = {k: settings.get(k) for k in _CORS_KEYS if k in settings}
        if not cors:
            return AgentResult(
                success=True,
                message="No CORS settings configured.",
                data={"cors": {}},
            )
        return AgentResult(
            success=True,
            message=f"Found {len(cors)} CORS setting(s).",
            data={"cors": cors},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))
