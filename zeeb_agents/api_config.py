"""Agent functions for project-level API configuration (throttling, versioning)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.code_gen import (
    VIEWSET_THROTTLES,
    find_settings_file,
    set_or_append_setting,
)
from zeeb_agents._utils.errors import AgentError, close_matches, fail

# Throttle rate grammar accepted by SimpleRateThrottle.parse_rate:
# "<number>/<period>" where the period's first letter is s/m/h/d.
_RATE_RE = re.compile(r"^\d+/(s|sec|second|m|min|minute|h|hour|d|day)$")

# Versioning scheme aliases → zeeb_api.versioning class dotted paths.
VERSIONING_SCHEMES = {
    "url": "zeeb_api.versioning.URLPathVersioning",
    "header": "zeeb_api.versioning.HeaderVersioning",
    "query": "zeeb_api.versioning.QueryParameterVersioning",
    "accept": "zeeb_api.versioning.AcceptHeaderVersioning",
}


def _require_settings_file(root: Path) -> Path:
    path = find_settings_file(root)
    if path is None:
        raise AgentError("settings.py not found in project", code="file_not_found")
    return path


@agent_function
async def configure_throttling(
    default_classes: list[str] | None = None,
    rates: dict[str, str] | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Configure project-wide API rate limiting in ``settings.py``.

    Writes ``DEFAULT_THROTTLE_CLASSES`` (as ``zeeb_api.throttling`` dotted
    paths) and/or ``DEFAULT_THROTTLE_RATES``.  Viewsets without an explicit
    ``throttle_classes`` fall back to these defaults.

    Args:
        default_classes: Throttle class names — any of ``AnonRateThrottle``,
            ``UserRateThrottle``, ``ScopedRateThrottle``.
        rates: Mapping of throttle scope → rate string, e.g.
            ``{"anon": "100/hour", "user": "1000/day"}``.  Rate format is
            ``"<number>/<period>"`` with period s/sec, m/min, h/hour, d/day.
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        keys_written (list[str]): the settings keys written
        default_classes (list[str] | None): dotted paths written, if any
        rates (dict | None): the rates written, if any

    Notes:
        - The default throttle cache is in-memory **per process** — multi-worker
          deployments need a custom ``BaseThrottleCache``.
        - Invalid class names / rate strings fail with
          ``error_code="invalid_input"`` and close-match ``suggestions``.
        - With neither argument given, nothing is written
          (``keys_written=[]``, still ``success=True``).
    """
    for name in default_classes or []:
        if name not in VIEWSET_THROTTLES:
            suggestions = close_matches(name, sorted(VIEWSET_THROTTLES))
            hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
            return fail(
                f"Unknown throttle class '{name}'.{hint} "
                f"Valid classes: {sorted(VIEWSET_THROTTLES)}",
                code="invalid_input",
                suggestions=suggestions,
            )
    for scope, rate in (rates or {}).items():
        if not isinstance(rate, str) or not _RATE_RE.match(rate):
            return fail(
                f"Invalid throttle rate '{rate}' for scope '{scope}'. "
                'Expected "<number>/<period>" with period s/sec, m/min, '
                'h/hour, d/day (e.g. "100/hour").',
                code="invalid_input",
                scope=scope,
            )

    root = project_root

    def _write() -> list[str]:
        written: list[str] = []
        if not default_classes and not rates:
            return written
        settings_path = _require_settings_file(root)
        content = settings_path.read_text(encoding="utf-8")
        if default_classes:
            paths = ", ".join(
                f'"zeeb_api.throttling.{name}"' for name in default_classes
            )
            content = set_or_append_setting(
                content, "DEFAULT_THROTTLE_CLASSES", f"[{paths}]"
            )
            written.append("DEFAULT_THROTTLE_CLASSES")
        if rates:
            entries = ", ".join(f'"{k}": "{v}"' for k, v in rates.items())
            content = set_or_append_setting(
                content, "DEFAULT_THROTTLE_RATES", "{" + entries + "}"
            )
            written.append("DEFAULT_THROTTLE_RATES")
        settings_path.write_text(content, encoding="utf-8")
        return written

    written = await asyncio.to_thread(_write)
    return AgentResult(
        success=True,
        message=(
            f"Throttling configured ({', '.join(written)})"
            if written
            else "Nothing to configure (no classes or rates given)"
        ),
        data={
            "keys_written": written,
            "default_classes": (
                [f"zeeb_api.throttling.{n}" for n in default_classes]
                if default_classes
                else None
            ),
            "rates": rates,
        },
    )


@agent_function
async def configure_versioning(
    scheme: str = "url",
    default_version: str | None = None,
    allowed_versions: list[str] | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Configure API versioning in ``settings.py``.

    Writes ``DEFAULT_VERSIONING_CLASS`` (and optionally ``DEFAULT_VERSION`` /
    ``ALLOWED_VERSIONS``).  ``VersioningMiddleware`` then sets
    ``request.state.version`` / ``viewset.version`` per request.

    Args:
        scheme: One of ``"url"`` (``/v1/users/``), ``"header"``
            (``X-API-Version``), ``"query"`` (``?version=``), or ``"accept"``
            (``Accept: ...; version=``).
        default_version: Version used when a request specifies none
            (writes ``DEFAULT_VERSION``).
        allowed_versions: Versions accepted; others get an
            ``API_VERSION_INVALID`` error (writes ``ALLOWED_VERSIONS``).
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        scheme (str): the scheme configured
        versioning_class (str): the dotted path written
        keys_written (list[str]): the settings keys written

    Notes:
        - An unknown *scheme* fails with ``error_code="invalid_input"`` and
          the valid schemes in the message.
    """
    versioning_class = VERSIONING_SCHEMES.get(scheme)
    if versioning_class is None:
        return fail(
            f"Unknown versioning scheme '{scheme}'. "
            f"Valid schemes: {sorted(VERSIONING_SCHEMES)}",
            code="invalid_input",
            suggestions=close_matches(scheme, sorted(VERSIONING_SCHEMES)),
        )
    root = project_root

    def _write() -> list[str]:
        settings_path = _require_settings_file(root)
        content = settings_path.read_text(encoding="utf-8")
        content = set_or_append_setting(
            content, "DEFAULT_VERSIONING_CLASS", f'"{versioning_class}"'
        )
        written = ["DEFAULT_VERSIONING_CLASS"]
        if default_version is not None:
            content = set_or_append_setting(
                content, "DEFAULT_VERSION", f'"{default_version}"'
            )
            written.append("DEFAULT_VERSION")
        if allowed_versions is not None:
            versions = ", ".join(f'"{v}"' for v in allowed_versions)
            content = set_or_append_setting(content, "ALLOWED_VERSIONS", f"[{versions}]")
            written.append("ALLOWED_VERSIONS")
        settings_path.write_text(content, encoding="utf-8")
        return written

    written = await asyncio.to_thread(_write)
    return AgentResult(
        success=True,
        message=f"Versioning configured: {scheme} ({versioning_class})",
        data={
            "scheme": scheme,
            "versioning_class": versioning_class,
            "keys_written": written,
        },
    )
