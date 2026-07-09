"""Platform-managed runtime references (preview URLs, live OpenAPI).

Zeeb projects run on an always-on, platform-managed preview runtime — an agent
does **not** start a dev server. Instead it fetches the live URLs the platform
publishes for a project and builds/reads against them.

The platform injects these values into the project's ``.env`` (or the process
environment); these tools surface them:

    ZEEB_PROJECT_ID          → data["project_id"]
    ZEEB_PREVIEW_URL         → data["preview_url"]
    ZEEB_RUNTIME_API_BASE_URL→ data["runtime_api_base_url"]
    ZEEB_OPENAPI_URL         → data["openapi_url"]

No network calls are made — these read configuration only.
"""

from __future__ import annotations

import os
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.errors import fail

_PREVIEW_KEYS = {
    "project_id": "ZEEB_PROJECT_ID",
    "preview_url": "ZEEB_PREVIEW_URL",
    "runtime_api_base_url": "ZEEB_RUNTIME_API_BASE_URL",
    "openapi_url": "ZEEB_OPENAPI_URL",
}


def _runtime_env(project_root: Path) -> dict[str, str]:
    """Merge the project's ``.env`` over the process env for the runtime keys."""
    from zeeb_agents.config import _find_env_file, _parse_env_file

    values: dict[str, str] = {}
    try:
        dotenv = _parse_env_file(_find_env_file(project_root))
    except Exception:
        dotenv = {}
    for env_key in _PREVIEW_KEYS.values():
        val = dotenv.get(env_key) or os.environ.get(env_key)
        if val:
            values[env_key] = val
    return values


@agent_function
async def get_project_reference(project_root: Path | None = None) -> AgentResult:
    """Return the platform-published runtime references for the project.

    Reads the URLs the always-on preview runtime exposes — build and test the
    frontend against these rather than starting a local server.

    Returns data (on success):
        project_id (str | None): the platform project id, if published.
        preview_url (str): the browsable preview URL.
        runtime_api_base_url (str): base URL of the live API.
        openapi_url (str): URL of the live OpenAPI document.

    Notes:
        - When the runtime values are not configured (the platform has not
          published them yet) this fails with
          ``error_code="runtime_not_configured"``.
    """
    assert project_root is not None  # resolved by @agent_function
    env = _runtime_env(project_root)
    if not env.get("ZEEB_PREVIEW_URL") and not env.get("ZEEB_RUNTIME_API_BASE_URL"):
        return fail(
            "Runtime references not configured for this project. The platform "
            "publishes ZEEB_PREVIEW_URL / ZEEB_RUNTIME_API_BASE_URL / "
            "ZEEB_OPENAPI_URL once the preview runtime is live.",
            code="runtime_not_configured",
        )
    data = {key: env.get(env_key) for key, env_key in _PREVIEW_KEYS.items()}
    return AgentResult(
        success=True,
        message="Runtime references ready.",
        data=data,
    )


@agent_function
async def get_openapi_url(project_root: Path | None = None) -> AgentResult:
    """Return the live OpenAPI document URL for the project.

    The preview runtime serves an always-current OpenAPI spec; prefer this over
    ``export_openapi`` (which writes a static snapshot) when you need the live
    contract to build a client against.

    Returns data (on success):
        openapi_url (str): URL of the live OpenAPI document.

    Notes:
        - Fails with ``error_code="runtime_not_configured"`` when the platform
          has not published ``ZEEB_OPENAPI_URL`` yet.
    """
    assert project_root is not None  # resolved by @agent_function
    env = _runtime_env(project_root)
    openapi_url = env.get("ZEEB_OPENAPI_URL")
    if not openapi_url:
        return fail(
            "Live OpenAPI URL not configured. The platform publishes "
            "ZEEB_OPENAPI_URL once the preview runtime is live.",
            code="runtime_not_configured",
        )
    return AgentResult(
        success=True,
        message="Live OpenAPI URL ready.",
        data={"openapi_url": openapi_url},
    )
