"""Agent functions for API schema introspection and export."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.errors import AgentError, fail
from zeeb_agents._utils.project import get_app_path, list_apps

# Map zeeb_orm field types → JSON Schema types (covers every canonical field
# class in zeeb_agents._utils.field_types — a unit test keeps them in sync)
_FIELD_TYPE_MAP: dict[str, dict[str, Any]] = {
    "CharField": {"type": "string"},
    "TextField": {"type": "string"},
    "EmailField": {"type": "string", "format": "email"},
    "URLField": {"type": "string", "format": "uri"},
    "SlugField": {"type": "string"},
    "UUIDField": {"type": "string", "format": "uuid"},
    "IntegerField": {"type": "integer"},
    "SmallIntegerField": {"type": "integer"},
    "BigIntegerField": {"type": "integer"},
    "PositiveIntegerField": {"type": "integer", "minimum": 0},
    "PositiveSmallIntegerField": {"type": "integer", "minimum": 0},
    "PositiveBigIntegerField": {"type": "integer", "minimum": 0},
    "AutoField": {"type": "integer", "readOnly": True},
    "BigAutoField": {"type": "integer", "readOnly": True},
    "UUIDAutoField": {"type": "string", "format": "uuid", "readOnly": True},
    "FloatField": {"type": "number"},
    "DecimalField": {"type": "string", "format": "decimal"},
    "BooleanField": {"type": "boolean"},
    "DateTimeField": {"type": "string", "format": "date-time"},
    "DateField": {"type": "string", "format": "date"},
    "TimeField": {"type": "string", "format": "time"},
    "DurationField": {"type": "string", "format": "duration"},
    "BinaryField": {"type": "string", "contentEncoding": "base64"},
    "GenericIPAddressField": {"type": "string"},
    "ForeignKey": {"type": "integer", "description": "FK id"},
    "OneToOneField": {"type": "integer", "description": "FK id"},
    "ManyToManyField": {"type": "array", "items": {"type": "integer"}},
    "JSONField": {"type": "object"},
}

_DEFAULT_SCHEMA_TYPE: dict[str, Any] = {"type": "string"}

# Patterns for route scanning
_VIEWSET_RE = re.compile(r'^[ \t]*router\.register\(\s*["\']([^"\']+)["\']\s*,\s*(\w+)', re.MULTILINE)
_ROUTE_RE = re.compile(r'^[ \t]*@router\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']', re.MULTILINE)
# Field lines are single-line in generated code — capture params to EOL so
# nested parens/brackets (choices, validators) don't truncate the match.
_FIELD_DEF_RE = re.compile(r"^\s{4}(\w+)\s*=\s*fields\.(\w+)\s*\((.*)$", re.MULTILINE)
_NULLABLE_RE = re.compile(r"null\s*=\s*True")
_MAX_LENGTH_RE = re.compile(r"max_length\s*=\s*(\d+)")
_HELP_TEXT_RE = re.compile(r'help_text\s*=\s*"([^"]*)"')
_DEFAULT_RE = re.compile(r"default\s*=\s*([\w.\"']+)")


def _extract_choices(params: str) -> list[Any] | None:
    """Return choice *values* from a ``choices=[...]`` kwarg (best-effort)."""
    import ast

    idx = params.find("choices=")
    if idx == -1:
        return None
    start = params.find("[", idx)
    if start == -1:
        return None
    depth = 0
    for end in range(start, len(params)):
        depth += params[end] in "([{"
        depth -= params[end] in ")]}"
        if depth == 0:
            break
    else:
        return None
    try:
        choices = ast.literal_eval(params[start : end + 1])
        return [c[0] if isinstance(c, (list, tuple)) else c for c in choices]
    except (ValueError, SyntaxError, TypeError, IndexError):
        return None


def _extract_default(params: str) -> Any:
    """Return a scalar ``default=`` value (best-effort), or ``None``."""
    import ast

    m = _DEFAULT_RE.search(params)
    if not m:
        return None
    try:
        return ast.literal_eval(m.group(1))
    except (ValueError, SyntaxError):
        return None  # callable/expression defaults are not representable


def _parse_model_fields(source: str, model_name: str) -> dict[str, Any]:
    """Extract fields from a model class block and return a JSON Schema properties dict."""
    class_pattern = re.compile(
        rf"^class {re.escape(model_name)}\b.*?(?=\nclass |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = class_pattern.search(source)
    if not match:
        return {}

    block = match.group(0)
    properties: dict[str, Any] = {"id": {"type": "integer", "readOnly": True}}
    required: list[str] = []

    for field_match in _FIELD_DEF_RE.finditer(block):
        fname, ftype, params = field_match.group(1), field_match.group(2), field_match.group(3)
        if fname in ("id",):
            continue

        schema_type = dict(_FIELD_TYPE_MAP.get(ftype, _DEFAULT_SCHEMA_TYPE))

        # Foreign keys read back as "<name>" but are written as "<name>_id" in
        # create/update bodies (the bare "<name>" is also accepted). Spell that
        # out — a static "FK id" leaves clients guessing the write key. An
        # explicit help_text still overrides this below.
        if ftype in ("ForeignKey", "OneToOneField"):
            schema_type["description"] = (
                f"Foreign key. Write as '{fname}_id' in create/update bodies "
                f"('{fname}' also accepted); responses return '{fname}'."
            )

        # max_length?
        ml = _MAX_LENGTH_RE.search(params)
        if ml and schema_type.get("type") == "string":
            schema_type["maxLength"] = int(ml.group(1))

        # choices → enum
        choices = _extract_choices(params)
        if choices:
            schema_type["enum"] = choices

        # help_text → description
        ht = _HELP_TEXT_RE.search(params)
        if ht:
            schema_type["description"] = ht.group(1)

        # scalar default
        default = _extract_default(params)
        if default is not None:
            schema_type["default"] = default

        # Nullable?
        if _NULLABLE_RE.search(params):
            schema_type = {"oneOf": [schema_type, {"type": "null"}]}
        else:
            required.append(fname)

        properties[fname] = schema_type

    return {"properties": properties, "required": required}


@agent_function
async def get_model_json_schema(
    app: str,
    model_name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Return a JSON Schema object for a zeeb_orm model.

    Useful for generating BaaS client SDKs, API documentation, or frontend
    form validation.

    Args:
        app: App directory name.
        model_name: PascalCase model class name.
        project_id: The host-assigned project id (required).

    Example::

        result = await get_model_json_schema("blog", "Post")
        print(result.data["schema"])

    Returns data (on success):
        app (str): echoes *app*
        model (str): echoes *model_name*
        schema (dict): a JSON Schema object (``$schema``, ``title``, ``type``,
            ``properties``, ``required``)

    Notes:
        - If ``models.py`` is missing, returns ``success=False`` with
          ``data=None``.
        - If the model is not found, fails with
          ``error_code="model_not_found"`` and close-match ``suggestions``.
        - Fields are parsed statically from source text, not by importing the
          model; an ``id`` integer property is always added.  ``choices``
          map to ``enum``, ``help_text`` to ``description``, and scalar
          ``default`` values are included.
    """
    models_path = get_app_path(app, project_root) / "models.py"
    if not models_path.exists():
        return fail(
            f"models.py not found at {models_path}.", code="file_not_found", missing="models.py"
        )

    def _build() -> dict[str, Any]:
        from zeeb_agents._utils.code_gen import extract_model_names
        from zeeb_agents._utils.errors import close_matches

        source = models_path.read_text(encoding="utf-8")
        fields_info = _parse_model_fields(source, model_name)
        if not fields_info:
            names = extract_model_names(source)
            suggestions = close_matches(model_name, names)
            hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
            raise AgentError(
                f"Model '{model_name}' not found in apps/{app}/models.py.{hint}",
                code="model_not_found",
                suggestions=suggestions,
                models=names,
            )
        schema: dict[str, Any] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": model_name,
            "type": "object",
            "properties": fields_info.get("properties", {}),
            "required": fields_info.get("required", []),
        }
        return schema

    schema = await asyncio.to_thread(_build)
    return AgentResult(
        success=True,
        message=f"JSON Schema generated for {app}.{model_name}.",
        data={"app": app, "model": model_name, "schema": schema},
    )


@agent_function
async def list_all_routes(
    project_root: Path | None = None,
) -> AgentResult:
    """Return a full inventory of API routes registered across all apps.

    Scans every ``apps/*/views.py`` and ``apps/*/urls.py`` for:

    - ``router.register(prefix, ViewSet)`` — class-based ViewSet registrations
    - ``@router.<method>(path)`` — standalone route decorators

    Args:
        project_id: The host-assigned project id (required).

    Returns:
        ``AgentResult`` with a ``routes`` list.  Each item has
        ``app``, ``type`` (``"viewset"`` or ``"route"``), and type-specific fields.

    Returns data (on success):
        routes (list[dict]): each has ``app``, ``file`` (``"views.py"`` or
            ``"urls.py"``), ``type``; viewset items add ``prefix`` and
            ``viewset``, route items add ``method`` and ``path``
        count (int): len(routes)
    """
    root = project_root

    def _scan() -> list[dict[str, Any]]:
        apps = list_apps(root)
        routes: list[dict[str, Any]] = []
        for app in apps:
            for fname in ("views.py", "urls.py"):
                fpath = get_app_path(app, root) / fname
                if not fpath.exists():
                    continue
                source = fpath.read_text(encoding="utf-8")
                for m in _VIEWSET_RE.finditer(source):
                    routes.append({
                        "app": app,
                        "file": fname,
                        "type": "viewset",
                        "prefix": m.group(1),
                        "viewset": m.group(2),
                    })
                for m in _ROUTE_RE.finditer(source):
                    routes.append({
                        "app": app,
                        "file": fname,
                        "type": "route",
                        "method": m.group(1).upper(),
                        "path": m.group(2),
                    })
        return routes

    routes = await asyncio.to_thread(_scan)
    return AgentResult(
        success=True,
        message=f"Found {len(routes)} route(s) across all apps.",
        data={"routes": routes, "count": len(routes)},
    )


@agent_function
async def export_openapi(
    output_path: str | None = None,
    port: int = 8000,
    project_root: Path | None = None,
) -> AgentResult:
    """Fetch the OpenAPI spec from a reachable API and save a static snapshot.

    Connects to ``http://localhost:{port}/openapi.json`` and writes the
    JSON to ``openapi.json`` in the project root (or *output_path*).

    This needs a reachable API on ``localhost:{port}`` — it does **not** start
    one, and you do not (and cannot) start a dev server yourself: the preview
    runtime is always-on and platform-managed. For the live contract prefer
    :func:`~zeeb_agents.get_openapi_url` / :func:`~zeeb_agents.get_project_reference`;
    reach for ``export_openapi`` only when you specifically need a static file
    written into the project tree.

    Args:
        output_path: Override output file path.  Relative to project root.
        port: Port the API is listening on.  Defaults to ``8000``.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        path (str): output path, relative to root when inside it, else absolute
        paths_count (int): number of entries in the spec's ``paths`` object
        title (str): the spec's ``info.title`` (``""`` if absent)

    Notes:
        - Writes a *static* snapshot fetched over HTTP.  For the always-current
          contract prefer :func:`~zeeb_agents.get_openapi_url`, which returns
          the platform preview runtime's live OpenAPI URL.
        - Requires ``httpx`` and a reachable running API.  A missing dependency
          fails with ``error_code="dependency_missing"``; a connection error
          or non-2xx response with ``error_code="server_not_reachable"``
          (``data`` includes the ``port``).
    """
    root = project_root

    async def _fetch() -> dict[str, Any]:
        try:
            import httpx
        except ImportError:
            raise AgentError(
                "httpx is required for export_openapi. "
                "Install with: pip install httpx",
                code="dependency_missing",
                dependency="httpx",
            ) from None
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"http://localhost:{port}/openapi.json",
                    timeout=10.0,
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            raise AgentError(
                f"Dev server on port {port} returned HTTP "
                f"{exc.response.status_code} for /openapi.json",
                code="server_not_reachable",
                port=port,
                status=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise AgentError(
                f"API not reachable on port {port} ({exc}) — the platform "
                "preview runtime serves the live contract; use "
                "get_openapi_url() for its URL.",
                code="server_not_reachable",
                port=port,
            ) from exc

    spec = await _fetch()

    out = (
        root / output_path if output_path and not Path(output_path).is_absolute()
        else Path(output_path) if output_path
        else root / "openapi.json"
    )
    await asyncio.to_thread(out.write_text, json.dumps(spec, indent=2), "utf-8")

    rel = str(out.relative_to(root)) if out.is_relative_to(root) else str(out)
    paths_count = len(spec.get("paths", {}))
    return AgentResult(
        success=True,
        message=f"OpenAPI spec saved to {rel} ({paths_count} path(s)).",
        data={"path": rel, "paths_count": paths_count, "title": spec.get("info", {}).get("title", "")},
    )
