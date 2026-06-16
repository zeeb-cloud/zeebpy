"""Agent functions for API schema introspection and export."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.project import get_app_path, list_apps

# Map zeeb_orm field types → JSON Schema types
_FIELD_TYPE_MAP: dict[str, dict[str, Any]] = {
    "CharField": {"type": "string"},
    "TextField": {"type": "string"},
    "EmailField": {"type": "string", "format": "email"},
    "URLField": {"type": "string", "format": "uri"},
    "SlugField": {"type": "string"},
    "UUIDField": {"type": "string", "format": "uuid"},
    "IntegerField": {"type": "integer"},
    "FloatField": {"type": "number"},
    "DecimalField": {"type": "string", "format": "decimal"},
    "BooleanField": {"type": "boolean"},
    "DateTimeField": {"type": "string", "format": "date-time"},
    "DateField": {"type": "string", "format": "date"},
    "ForeignKey": {"type": "integer", "description": "FK id"},
    "OneToOneField": {"type": "integer", "description": "FK id"},
    "ManyToManyField": {"type": "array", "items": {"type": "integer"}},
    "JSONField": {"type": "object"},
}

_DEFAULT_SCHEMA_TYPE: dict[str, Any] = {"type": "string"}

# Patterns for route scanning
_VIEWSET_RE = re.compile(r'router\.register\(\s*["\']([^"\']+)["\']\s*,\s*(\w+)', re.MULTILINE)
_ROUTE_RE = re.compile(r'@router\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']', re.MULTILINE)
_FIELD_DEF_RE = re.compile(r"^\s{4}(\w+)\s*=\s*fields\.(\w+)\s*\(([^)]*)\)", re.MULTILINE)
_NULLABLE_RE = re.compile(r"null\s*=\s*True")
_MAX_LENGTH_RE = re.compile(r"max_length\s*=\s*(\d+)")


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

        # Nullable?
        if _NULLABLE_RE.search(params):
            schema_type = {"oneOf": [schema_type, {"type": "null"}]}
        else:
            required.append(fname)

        # max_length?
        ml = _MAX_LENGTH_RE.search(params)
        if ml and "type" in schema_type and schema_type["type"] == "string":
            schema_type["maxLength"] = int(ml.group(1))

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
        project_root: Auto-detected if ``None``.

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
        - If the model is not found in the source, the underlying ``ValueError``
          is wrapped by the decorator into ``success=False`` with ``data=None``.
        - Fields are parsed statically from source text, not by importing the
          model; an ``id`` integer property is always added.
    """
    models_path = get_app_path(app, project_root) / "models.py"
    if not models_path.exists():
        return AgentResult(success=False, message=f"models.py not found at {models_path}.")

    def _build() -> dict[str, Any]:
        source = models_path.read_text(encoding="utf-8")
        fields_info = _parse_model_fields(source, model_name)
        if not fields_info:
            raise ValueError(f"Model '{model_name}' not found in apps/{app}/models.py.")
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
        project_root: Auto-detected if ``None``.

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
    """Fetch the live OpenAPI spec from the running dev server and save it.

    Connects to ``http://localhost:{port}/openapi.json`` and writes the
    JSON to ``openapi.json`` in the project root (or *output_path*).

    The dev server must already be running (``zeeb runserver``).

    Args:
        output_path: Override output file path.  Relative to project root.
        port: Port the dev server is listening on.  Defaults to ``8000``.
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        path (str): output path, relative to root when inside it, else absolute
        paths_count (int): number of entries in the spec's ``paths`` object
        title (str): the spec's ``info.title`` (``""`` if absent)

    Notes:
        - Requires ``httpx`` and a running dev server; a missing dependency,
          connection error, or non-2xx response is wrapped by the decorator
          into ``success=False`` with ``data=None``.
    """
    root = project_root

    async def _fetch() -> dict[str, Any]:
        try:
            import httpx
        except ImportError:
            raise RuntimeError(
                "httpx is required for export_openapi. "
                "Install with: pip install httpx"
            )
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"http://localhost:{port}/openapi.json",
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()

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
