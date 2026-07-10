"""Agent functions for CORS configuration in zeeb_api projects."""

from __future__ import annotations

import asyncio
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.code_gen import find_settings_file, set_or_append_setting
from zeeb_agents._utils.errors import fail
from zeeb_agents._utils.project import load_project_settings

_CORS_KEYS = (
    "CORS_ALLOW_ORIGINS",
    "CORS_ALLOW_METHODS",
    "CORS_ALLOW_HEADERS",
    "CORS_ALLOW_CREDENTIALS",
    "CORS_EXPOSE_HEADERS",
)

# Backwards-compatible aliases (canonical versions live in _utils.code_gen).
_find_settings_file = find_settings_file
_set_or_append_setting = set_or_append_setting


def _render_list(values: list[str]) -> str:
    items = ", ".join(f'"{v}"' for v in values)
    return f"[{items}]"


@agent_function
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
        project_id: The host-assigned project id (required).

    Example::

        await configure_cors(
            origins=["https://myapp.com", "http://localhost:3000"],
            methods=["GET", "POST", "PUT", "DELETE"],
        )

    Returns data (on success):
        settings_file (str): settings.py path relative to the project root
        keys_written (list[str]): the ``CORS_*`` setting keys written

    Notes:
        - On failure (no ``settings.py`` found) ``data`` is ``None``.
        - ``CORS_EXPOSE_HEADERS`` is written only when ``expose_headers`` is
          not ``None``; otherwise it is absent from ``keys_written``.
    """
    root = project_root
    settings_path = await asyncio.to_thread(_find_settings_file, root)
    if not settings_path:
        return fail(
            "settings.py not found in project.", code="file_not_found", missing="settings.py"
        )

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


@agent_function
async def get_cors_config(
    project_root: Path | None = None,
) -> AgentResult:
    """Read the current CORS settings from the project.

    Returns all ``CORS_*`` keys currently defined in settings.

    Args:
        project_id: The host-assigned project id (required).

    Returns data (always):
        cors (dict): mapping of each defined ``CORS_*`` key to its value;
            an empty dict ``{}`` when no CORS settings are configured.
    """
    settings = await asyncio.to_thread(load_project_settings, project_root)
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
