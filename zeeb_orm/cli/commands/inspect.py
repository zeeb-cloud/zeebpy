"""showurls, inspect and frontend-brief — what this project actually is.

These answer the three questions anyone picking up a generated project asks
first, and they answer them from the live code rather than from a checked-in
inventory. A committed manifest is stale the moment a model is edited; a
command is correct every time it runs.

    showurls        what can I call?
    inspect         what is in here? (JSON, for tooling and agents)
    frontend-brief  what does a client need to talk to this? (paste into a
                    frontend generator such as Lovable, v0 or Bolt)

Two rules the implementation holds to:

**Never leak a secret.** The output of these commands lands in transcripts and
issue trackers. ``SECRET_KEY``, ``JWT_SECRET_KEY`` and any ``*_SECRET`` are
never emitted, only whether they are set; a database URL is stripped of its
credentials.

**``inspect`` never hard-fails.** A project that will not import is exactly when
orientation is worth the most. When ``create_app()`` raises, it falls back to
reading ``settings.py`` and the app tree off disk, sets ``degraded: true``, puts
the exception in ``errors`` and still exits 0 with parseable JSON.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

from zeeb_orm.cli.output import fail, no_project, ok
from zeeb_orm.scaffold.harness import find_settings_module
from zeeb_orm.scaffold.naming import find_project_root

#: Never printed, whatever a caller asks for. Matched case-insensitively as a
#: substring, so a project's own FOO_CLIENT_SECRET is covered too.
_SECRET_MARKERS = ("secret", "password", "token", "private_key")


def _redact_url(url: str) -> str:
    """Return *url* with any embedded credentials replaced.

    ``postgresql+asyncpg://user:pw@host/db`` → ``postgresql+asyncpg://***@host/db``
    """
    return re.sub(r"://[^/@\s]+@", "://***@", url or "")


def _is_secret(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS)


def _prepare(project_root: Path) -> str | None:
    """Put the project on ``sys.path`` and return its settings module name."""
    root = str(project_root)
    if root not in sys.path:
        sys.path.insert(0, root)
    os.chdir(project_root)
    return find_settings_module(project_root)


def _collect_routes(app) -> list[dict]:
    """Every path the app serves, once each.

    Read from ``app.routes`` rather than from ``app.openapi()``: ``/health`` and
    ``/ready`` are deliberately excluded from the schema, and schema generation
    can raise on an unusual model and take the whole command down with it.

    The trailing-slash aliases the router adds are dropped — they answer the
    same endpoint, and listing both would double every line.
    """
    from starlette.routing import Route as StarletteRoute

    paths = {getattr(route, "path", None) for route in app.routes}
    # One entry per path: FastAPI registers a separate route object per HTTP
    # method, and listing /posts/{id} four times says nothing extra.
    merged: dict[str, dict] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        if not isinstance(route, StarletteRoute) and not hasattr(route, "endpoint"):
            continue
        # "/posts/" alongside "/posts" is the alias, not a second endpoint.
        if len(path) > 1 and path.endswith("/") and path[:-1] in paths:
            continue
        verbs = {m for m in methods if m not in ("HEAD", "OPTIONS")}
        if not verbs:
            continue
        entry = merged.setdefault(
            path, {"path": path, "methods": set(), "name": getattr(route, "name", None)}
        )
        entry["methods"] |= verbs
    return [
        {**entry, "methods": sorted(entry["methods"])}
        for entry in sorted(merged.values(), key=lambda e: e["path"])
    ]


def _collect_models(installed_apps: list[str]) -> dict[str, list[dict]]:
    """``{app: [{name, table, fields}]}`` from zeeb_orm's own model registry.

    A model enters the registry when its module is imported, so each installed
    app's ``models`` module is imported first. The registry is the same source
    the migration engine reads, which is why this cannot drift from the schema.
    """
    import importlib

    from zeeb_orm.models.base import _model_registry

    for dotted in installed_apps:
        try:
            importlib.import_module(f"{dotted}.models")
        except ModuleNotFoundError:
            continue
        except Exception:  # a broken app must not hide the healthy ones
            continue

    # Attributed by defining module, not by import order: create_app() has
    # usually imported everything already, so an import-delta would find
    # nothing.
    by_app: dict[str, list[dict]] = {}
    for name, model in sorted(_model_registry.items()):
        module = getattr(model, "__module__", "")
        if not module.startswith("apps."):
            continue
        app_name = module.split(".")[1]
        meta = getattr(model, "_meta", None)
        by_app.setdefault(app_name, []).append(
            {
                "name": name,
                "table": getattr(meta, "db_table", None),
                "fields": sorted(
                    field.name for field in getattr(meta, "local_fields", []) or []
                ),
            }
        )
    return by_app


def _read_settings_from_disk(project_root: Path, package: str) -> dict:
    """Best-effort settings read for a project that will not import.

    Only literal assignments are recovered — the same AST shapes
    :mod:`zeeb_orm.scaffold.wiring` already parses. It is enough to tell an
    agent which apps exist and where the URLs are mounted.
    """
    path = project_root / package / "settings.py"
    recovered: dict = {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return recovered
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not target.id.isupper():
            continue
        if _is_secret(target.id):
            continue
        try:
            recovered[target.id] = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            continue
    return recovered


def _snapshot(project_root: Path) -> dict:
    """Build the inspect payload, degrading rather than raising."""
    from zeeb_orm.scaffold.project import zeeb_version

    settings_module = _prepare(project_root)
    package = settings_module.split(".")[0] if settings_module else None
    snapshot: dict = {
        "project": {
            "name": project_root.name,
            "root": str(project_root),
            "package": package,
            "settings_module": settings_module,
            "zeebpy_version": zeeb_version(),
        },
        "degraded": False,
        "errors": [],
    }
    if settings_module is None:
        snapshot["degraded"] = True
        snapshot["errors"].append("No settings.py found in any project subdirectory.")
        return snapshot

    app = None
    try:
        from zeeb_api import create_app
        from zeeb_api.conf import get_settings

        app = create_app(settings_module)
        settings = get_settings()
        values = {
            key: getattr(settings, key)
            for key in ("DEBUG", "API_PREFIX", "API_VERSION", "AUTH_USER_MODEL",
                        "INSTALLED_APPS", "MIDDLEWARE", "DATABASE", "SECRET_KEY")
            if hasattr(settings, key)
        }
    except Exception as exc:
        snapshot["degraded"] = True
        snapshot["errors"].append(f"{type(exc).__name__}: {exc}")
        values = _read_settings_from_disk(project_root, package)

    database = values.get("DATABASE") or {}
    installed = list(values.get("INSTALLED_APPS") or [])
    snapshot["settings"] = {
        "debug": values.get("DEBUG"),
        "api_prefix": values.get("API_PREFIX"),
        "api_version": values.get("API_VERSION"),
        "auth_user_model": values.get("AUTH_USER_MODEL"),
        "installed_apps": installed,
        "middleware": list(values.get("MIDDLEWARE") or []),
        "database": {
            "dialect": (database.get("url", "").split("://", 1)[0] or None),
            "url_redacted": _redact_url(database.get("url", "")) or None,
        },
        # Never the value. Whether it is set is all a caller can act on.
        "secret_key_set": bool(values.get("SECRET_KEY")),
    }

    snapshot["routes"] = _collect_routes(app) if app is not None else []

    models = _collect_models(installed) if installed else {}
    apps_dir = project_root / "apps"
    snapshot["apps"] = [
        {
            "name": directory.name,
            "installed": f"apps.{directory.name}" in installed,
            "has_urls": (directory / "urls.py").exists(),
            "models": models.get(directory.name, []),
        }
        for directory in sorted(apps_dir.iterdir())
        if directory.is_dir() and (directory / "__init__.py").exists()
    ] if apps_dir.exists() else []

    try:
        from zeeb_orm.migrations.state import get_migration_state

        state = get_migration_state(project_root)
        snapshot["migrations"] = {
            "total": state.total_migrations,
            "applied": state.applied_migrations,
            "pending": state.pending_migrations,
            "current": state.current_revision,
        }
    except Exception as exc:
        snapshot["migrations"] = {"readable": False}
        snapshot["errors"].append(f"migrations: {exc}")

    return snapshot


def run_inspect(json_output: bool = True) -> int:
    """Report the project's live apps, models, routes and settings."""
    project_root = find_project_root()
    if project_root is None:
        return no_project("inspect", json_output=json_output)

    origin = Path.cwd()
    try:
        snapshot = _snapshot(project_root)
    finally:
        os.chdir(origin)

    counts = (
        f"{len(snapshot.get('apps', []))} app(s), "
        f"{sum(len(a['models']) for a in snapshot.get('apps', []))} model(s), "
        f"{len(snapshot.get('routes', []))} route(s)"
    )
    lines = [f"Project: {snapshot['project']['name']}  ({counts})"]
    for app in snapshot.get("apps", []):
        marker = " " if app["installed"] else "!"
        models = ", ".join(model["name"] for model in app["models"]) or "no models"
        lines.append(f" {marker} {app['name']}: {models}")
    if snapshot["degraded"]:
        lines.append("")
        lines.append("Degraded — the project did not import cleanly:")
        lines += [f"  {error}" for error in snapshot["errors"]]

    return ok(
        f"{snapshot['project']['name']}: {counts}"
        + (" (degraded)" if snapshot["degraded"] else ""),
        json_output=json_output,
        lines=tuple(lines),
        **snapshot,
    )


def run_showurls(json_output: bool = False) -> int:
    """List every route the project serves, with its methods."""
    project_root = find_project_root()
    if project_root is None:
        return no_project("showurls", json_output=json_output)

    origin = Path.cwd()
    try:
        settings_module = _prepare(project_root)
        if settings_module is None:
            return fail(
                "No settings.py found in any project subdirectory.",
                code="file_not_found",
                next_command="python manage.py check",
                json_output=json_output,
            )
        try:
            from zeeb_api import create_app
            from zeeb_api.conf import get_settings

            app = create_app(settings_module)
            prefix = getattr(get_settings(), "API_PREFIX", "") or ""
        except Exception as exc:
            # The same import chain runserver would fail on, so the exception
            # text is the actual diagnostic — do not swallow it.
            return fail(
                f"The project could not be loaded: {type(exc).__name__}: {exc}",
                code="invalid_input",
                next_command="python manage.py check",
                json_output=json_output,
            )
        routes = _collect_routes(app)
    finally:
        os.chdir(origin)

    width = max((len(",".join(r["methods"])) for r in routes), default=6)
    return ok(
        f"{len(routes)} route(s).",
        json_output=json_output,
        lines=tuple(
            f"{','.join(route['methods']):<{width}}  {route['path']}"
            + (f"  {route['name']}" if route["name"] else "")
            for route in routes
        ),
        routes=routes,
        count=len(routes),
        api_prefix=prefix,
    )


_BRIEF = """\
# API integration brief — @PROJECT@

Base URL: @BASE_URL@
OpenAPI:  @BASE_URL@/openapi.json   (interactive docs at /docs)

Paste this into your frontend generator. Everything below is read from the
running project, so it is what the API actually serves right now.

## Authentication

JSON Web Tokens over the `Authorization` header — no cookies, no session.

    POST @PREFIX@/auth/register  {"email": "...", "password": "..."}
    POST @PREFIX@/auth/login     {"email": "...", "password": "..."}
      -> {"access_token": "...", "refresh_token": "...", "token_type": "bearer"}
    POST @PREFIX@/auth/refresh   {"refresh_token": "..."}
    GET  @PREFIX@/auth/me        Authorization: Bearer <access_token>

Send `Authorization: Bearer <access_token>` on every authenticated request.
When a call returns 401, refresh once and retry; if the refresh also fails,
send the user back to the login screen.

## Errors

Every error — validation, auth, permissions, 404, throttling, crashes — uses
one envelope. Branch on `error.code`, which is stable; never on `message`,
which is English debug text.

    {"success": false,
     "error": {"code": "FIELD_REQUIRED", "message": "...", "details": {...}}}

Code families: `AUTH_*` (sign in again), `PERM_*` (hide the control),
`FIELD_*` (show it on the form field), `RESOURCE_*` (404 view),
`QUERY_*` (bad filter), `SERVER_*` (retry or report).

## Versioning

Send `X-API-Version: @VERSION@`. An unknown version is rejected with 400.

## CORS

The API answers cross-origin requests from the origins configured in `.env`
(`CORS_ALLOW_ORIGINS`, or `CORS_ALLOW_ORIGIN_REGEX` for preview deployments
whose hostname changes per build). Add your frontend's origin there before
wiring it up, or every browser request fails preflight.

## Endpoints

@ROUTES@

Collection paths have no trailing slash (`@PREFIX@/posts`); a trailing slash is
also served. Detail routes take the resource id: `@PREFIX@/posts/{id}`.
"""


def run_frontend_brief(json_output: bool = False) -> int:
    """Emit an integration brief to hand to a frontend or an AI app builder."""
    project_root = find_project_root()
    if project_root is None:
        return no_project("frontend-brief", json_output=json_output)

    origin = Path.cwd()
    try:
        settings_module = _prepare(project_root)
        if settings_module is None:
            return fail(
                "No settings.py found in any project subdirectory.",
                code="file_not_found",
                next_command="python manage.py check",
                json_output=json_output,
            )
        try:
            from zeeb_api import create_app
            from zeeb_api.conf import get_settings

            app = create_app(settings_module)
            settings = get_settings()
        except Exception as exc:
            return fail(
                f"The project could not be loaded: {type(exc).__name__}: {exc}",
                code="invalid_input",
                next_command="python manage.py check",
                json_output=json_output,
            )
        routes = _collect_routes(app)
        prefix = getattr(settings, "API_PREFIX", "") or ""
        version = getattr(settings, "DEFAULT_VERSION", "1.0")
    finally:
        os.chdir(origin)

    base_url = os.environ.get("ZEEB_PREVIEW_URL") or "http://127.0.0.1:9000"
    table = "\n".join(
        f"    {','.join(route['methods']):<22} {route['path']}" for route in routes
    )
    brief = (
        _BRIEF.replace("@PROJECT@", project_root.name)
        .replace("@BASE_URL@", base_url)
        .replace("@PREFIX@", prefix)
        .replace("@VERSION@", str(version))
        .replace("@ROUTES@", table)
    )

    return ok(
        f"Integration brief for {project_root.name} ({len(routes)} route(s)).",
        json_output=json_output,
        lines=(brief,),
        brief=brief,
        base_url=base_url,
        api_prefix=prefix,
        api_version=version,
        routes=routes,
    )
