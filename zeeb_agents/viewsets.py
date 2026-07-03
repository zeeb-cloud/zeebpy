"""Agent functions for ViewSet and route management."""

from __future__ import annotations

import asyncio
import re
import textwrap
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.code_gen import (
    append_block,
    class_exists,
    ensure_import,
    render_viewset_class,
    resolve_pagination,
    validate_permission,
    validate_throttles,
    viewset_option_imports,
)
from zeeb_agents._utils.errors import AgentError
from zeeb_agents._utils.project import require_project_root
from zeeb_agents._utils.validation import ensure_app_exists, ensure_identifier
from zeeb_agents.models import create_model
from zeeb_agents.serializers import create_serializer

_ACTION_METHODS = frozenset({"get", "post", "put", "patch", "delete"})


def _views_file(app: str, root: Path) -> Path:
    """Return ``apps/<app>/views.py``; fail with suggestions if the app is missing."""
    return ensure_app_exists(app, root) / "views.py"


def _urls_file(app: str, root: Path) -> Path:
    """Return ``apps/<app>/urls.py``; fail with suggestions if the app is missing."""
    return ensure_app_exists(app, root) / "urls.py"


@agent_function
async def create_viewset(
    app: str,
    model_name: str,
    serializer_class: str | None = None,
    permission: str = "IsAuthenticatedOrReadOnly",
    read_only: bool = False,
    lookup_field: str | None = None,
    pagination: str | None = None,
    throttles: list[str] | None = None,
    search_fields: list[str] | None = None,
    ordering_fields: list[str] | None = None,
    filterset: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Append a ``ModelViewSet`` subclass to ``apps/<app>/views.py``.

    Args:
        app: App directory name.
        model_name: The model this viewset exposes (e.g. ``"Post"``).
        serializer_class: Override the serializer class name.  Defaults to
            ``"<ModelName>Serializer"``.
        permission: Permission class name from ``zeeb_api.permissions``
            (``AllowAny``, ``IsAuthenticated``, ``IsAdminUser``,
            ``IsAuthenticatedOrReadOnly``, ``IsOwner``, ``IsOwnerOrReadOnly``,
            ``DjangoModelPermissions``).
            Default: ``"IsAuthenticatedOrReadOnly"``.
        read_only: Generate a ``ReadOnlyModelViewSet`` (query/list/retrieve
            only) instead of a full-CRUD ``ModelViewSet``.
        lookup_field: Field used for detail lookups (default ``"id"``), e.g.
            ``"slug"``.
        pagination: ``"page"``, ``"limit_offset"``, ``"cursor"`` (or the
            corresponding ``zeeb_api.pagination`` class name) — sets
            ``pagination_class`` and its import.
        throttles: Throttle class names from ``zeeb_api.throttling``
            (``AnonRateThrottle``, ``UserRateThrottle``, ``ScopedRateThrottle``)
            — sets ``throttle_classes`` and their imports.
        search_fields: Enables full-text search (``?search=…``) via
            ``SearchFilter`` on these fields.
        ordering_fields: Enables ``?ordering=…`` via ``OrderingFilter``,
            restricted to these fields.
        filterset: ``FilterSet`` class name to add to ``filter_backends``
            (imported from the app's ``filters.py`` — create it with
            :func:`~zeeb_agents.filters_scaffold.create_filterset`).
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        app (str): the app directory name
        model (str): the model name passed in
        viewset (str): the generated class name (``"<ModelName>ViewSet"``)
        read_only (bool): whether a ``ReadOnlyModelViewSet`` was generated
        options (list[str]): names of the optional class attributes emitted

    Notes:
        - Failures carry ``error_code`` in ``data`` (``app_not_found``,
          ``already_exists``, ``invalid_permission``, ``invalid_input``, …)
          plus close-match ``suggestions`` where applicable.
    """
    ensure_identifier(model_name, "model name")
    validate_permission(permission)
    if pagination:
        resolve_pagination(pagination)
    if throttles:
        validate_throttles(throttles)
    if filterset:
        ensure_identifier(filterset, "filterset class name")
    path = _views_file(app, project_root)
    if not path.exists():
        return AgentResult(success=False, message=f"views.py not found at {path}")

    class_name = f"{model_name}ViewSet"

    def _write() -> list[str]:
        content = path.read_text(encoding="utf-8")
        if class_exists(content, class_name):
            raise AgentError(
                f"'{class_name}' already exists in {path}",
                code="already_exists",
                viewset=class_name,
            )
        class_code = render_viewset_class(
            model_name,
            serializer_class,
            permission,
            read_only=read_only,
            lookup_field=lookup_field,
            pagination=pagination,
            throttles=throttles,
            search_fields=search_fields,
            ordering_fields=ordering_fields,
            filterset=filterset,
        )
        ensure_import(path, "from zeeb_api import viewsets, permissions")
        if not read_only:
            ensure_import(path, "from zeeb_api.viewsets import ModelViewSet")
        for import_line in viewset_option_imports(
            read_only=read_only,
            pagination=pagination,
            throttles=throttles,
            search_fields=search_fields,
            ordering_fields=ordering_fields,
            filterset=filterset,
        ):
            ensure_import(path, import_line)
        ser = serializer_class or f"{model_name}Serializer"
        ensure_import(path, f"from .models import {model_name}")
        ensure_import(path, f"from .serializers import {ser}")
        append_block(path, class_code)
        options = [
            name
            for name, given in (
                ("lookup_field", lookup_field),
                ("pagination_class", pagination),
                ("throttle_classes", throttles),
                ("search_fields", search_fields),
                ("ordering_fields", ordering_fields),
                ("filter_backends", filterset or search_fields or ordering_fields),
            )
            if given
        ]
        return options

    options = await asyncio.to_thread(_write)
    return AgentResult(
        success=True,
        message=f"'{class_name}' created in apps/{app}/views.py",
        data={
            "app": app,
            "model": model_name,
            "viewset": class_name,
            "read_only": read_only,
            "options": options,
        },
    )


@agent_function
async def add_viewset_action(
    app: str,
    model_name: str,
    action_name: str,
    detail: bool = True,
    methods: list[str] | None = None,
    body: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Append a custom ``@action`` method to an existing ViewSet.

    Adds a routed extra endpoint to the ViewSet (e.g. ``POST
    /<prefix>/{id}/publish/`` for a detail action named ``publish``). Prefer
    this over editing ``views.py`` with ``write_file``.

    Args:
        app: App directory name.
        model_name: The model whose ``<ModelName>ViewSet`` to extend.
        action_name: Snake-case method name (e.g. ``"publish"``). Also used as
            the URL segment (``/<prefix>/{id}/publish/``).
        detail: Whether the action operates on a single instance (``pk`` in URL).
        methods: HTTP methods (default: ``["get"]``).
        body: The action implementation, as Python source. Pass the real logic
            here so you **never need ``write_file`` for the action body**. The
            snippet is dedented and indented automatically (you may pass it
            flush-left or pre-indented) and should ``return`` a JSON-serializable
            value. The method receives ``self``, ``request`` and (for detail
            actions) ``pk``; use ``await self.get_object()`` to load the
            instance. When omitted, a ``pass  # TODO`` placeholder is generated.
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        app (str): the app directory name
        viewset (str): the target class name (``"<ModelName>ViewSet"``)
        action (str): the action method name added

    Notes:
        - On failure (missing ``views.py``, or the ViewSet class is not found)
          ``data`` is ``None``.

    Example::

        await add_viewset_action(
            "blog", "Post", "publish", detail=True, methods=["post"],
            body='''
                post = await self.get_object()
                post.published = True
                await post.save()
                return {"status": "published", "id": str(post.id)}
            ''',
        )
    """
    ensure_identifier(action_name, "action name")
    action_methods = [m.lower() for m in (methods or ["get"])]
    invalid = [m for m in action_methods if m not in _ACTION_METHODS]
    if invalid:
        return AgentResult(
            success=False,
            message=(
                f"Invalid method(s) {', '.join(sorted(invalid))}. "
                f"Must be one of: {', '.join(sorted(_ACTION_METHODS))}"
            ),
            data={"error_code": "invalid_input"},
        )
    path = _views_file(app, project_root)
    if not path.exists():
        return AgentResult(success=False, message=f"views.py not found at {path}")

    class_name = f"{model_name}ViewSet"

    def _insert() -> None:
        content = path.read_text(encoding="utf-8")
        if not class_exists(content, class_name):
            raise AgentError(
                f"'{class_name}' not found in {path}",
                code="model_not_found",
                viewset=class_name,
            )

        methods_repr = ", ".join(f'"{m}"' for m in action_methods)
        if body is None or not body.strip():
            body_block = f"        pass  # TODO: implement {action_name}"
        else:
            dedented = textwrap.dedent(body).strip("\n")
            body_block = textwrap.indent(dedented, "        ")
        action_code = (
            f'    @action(detail={detail}, methods=[{methods_repr}])\n'
            f'    async def {action_name}(self, request, pk=None):\n'
            f'{body_block}\n'
        )

        # Insert before the last line of the class (before next class or EOF)
        pattern = re.compile(
            rf"(class {re.escape(class_name)}\b.*?)(\nclass |\Z)",
            re.DOTALL,
        )
        def _replace(m: re.Match) -> str:
            return m.group(1) + "\n" + action_code + m.group(2)

        new_content = pattern.sub(_replace, content, count=1)
        ensure_import(path, "from zeeb_api.viewsets import action")
        path.write_text(new_content, encoding="utf-8")

    await asyncio.to_thread(_insert)
    return AgentResult(
        success=True,
        message=f"Action '{action_name}' added to '{class_name}'",
        data={"app": app, "viewset": class_name, "action": action_name},
    )


def _set_class_attr(block: str, attr: str, rendered: str) -> tuple[str, bool]:
    """Set ``attr = rendered`` inside a class *block*.

    Replaces an existing assignment, else inserts after the
    ``serializer_class`` line (falling back to the ``queryset`` line, then to
    right after the class header).  Returns ``(new_block, replaced)``.
    """
    line = f"    {attr} = {rendered}"
    pattern = re.compile(rf"^\s+{re.escape(attr)}\s*=.*$", re.MULTILINE)
    if pattern.search(block):
        return pattern.sub(line, block, count=1), True
    for anchor in (r"^\s+serializer_class\s*=.*$", r"^\s+queryset\s*=.*$", r"^class .*:$"):
        m = re.search(anchor, block, re.MULTILINE)
        if m:
            insert_at = m.end()
            return block[:insert_at] + "\n" + line + block[insert_at:], False
    return block.rstrip("\n") + "\n" + line + "\n", False


@agent_function
async def update_viewset(
    app: str,
    model_name: str,
    permission: str | None = None,
    lookup_field: str | None = None,
    pagination: str | None = None,
    throttles: list[str] | None = None,
    search_fields: list[str] | None = None,
    ordering_fields: list[str] | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Set or update class attributes on an existing ``<ModelName>ViewSet``.

    Each given option replaces the existing assignment in the class (or
    inserts it), and the matching ``zeeb_api`` imports are added.  Passing
    ``search_fields`` / ``ordering_fields`` also ensures ``SearchFilter`` /
    ``OrderingFilter`` appear in ``filter_backends``.

    Args:
        app: App directory name.
        model_name: The model whose ``<ModelName>ViewSet`` to update.
        permission: New permission class name from ``zeeb_api.permissions``.
        lookup_field: New detail-lookup field (e.g. ``"slug"``).
        pagination: ``"page"``, ``"limit_offset"``, ``"cursor"`` or a
            ``zeeb_api.pagination`` class name.
        throttles: Throttle class names from ``zeeb_api.throttling``.
        search_fields: Fields for ``?search=…`` full-text search.
        ordering_fields: Fields allowed in ``?ordering=…``.
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        app (str): the app directory name
        viewset (str): the class name (``"<ModelName>ViewSet"``)
        changes (list[str]): human-readable descriptions of each attribute set

    Notes:
        - Failures carry ``error_code`` in ``data`` (``app_not_found``,
          ``model_not_found``, ``invalid_permission``, ``invalid_input``, …)
          plus close-match ``suggestions`` where applicable.
        - With no options given, ``changes`` is empty and the file is left
          untouched.
    """
    if permission:
        validate_permission(permission)
    if pagination:
        resolve_pagination(pagination)
    if throttles:
        validate_throttles(throttles)
    path = _views_file(app, require_project_root(project_root))
    if not path.exists():
        return AgentResult(success=False, message=f"views.py not found at {path}")

    class_name = f"{model_name}ViewSet"

    def _update() -> list[str]:
        content = path.read_text(encoding="utf-8")
        block_pattern = re.compile(
            rf"^(class {re.escape(class_name)}\b.*?)(?=\nclass |\Z)",
            re.MULTILINE | re.DOTALL,
        )
        block_match = block_pattern.search(content)
        if block_match is None:
            raise AgentError(
                f"'{class_name}' not found in {path}",
                code="model_not_found",
                viewset=class_name,
            )
        block = block_match.group(1)
        changes: list[str] = []

        def quoted_list(values: list[str]) -> str:
            return "[" + ", ".join(f'"{v}"' for v in values) + "]"

        if permission:
            block, _ = _set_class_attr(
                block, "permission_classes", f"[permissions.{permission}]"
            )
            changes.append(f"permission_classes set to {permission}")
        if lookup_field:
            block, _ = _set_class_attr(block, "lookup_field", f'"{lookup_field}"')
            changes.append(f"lookup_field set to '{lookup_field}'")
        if pagination:
            block, _ = _set_class_attr(
                block, "pagination_class", resolve_pagination(pagination)
            )
            changes.append(f"pagination_class set to {resolve_pagination(pagination)}")
        if throttles:
            block, _ = _set_class_attr(
                block, "throttle_classes", "[" + ", ".join(throttles) + "]"
            )
            changes.append("throttle_classes set")
        if search_fields:
            block, _ = _set_class_attr(block, "search_fields", quoted_list(search_fields))
            changes.append("search_fields set")
        if ordering_fields:
            block, _ = _set_class_attr(
                block, "ordering_fields", quoted_list(ordering_fields)
            )
            changes.append("ordering_fields set")

        # Keep filter_backends in sync with search/ordering usage.
        needed_backends = [
            name
            for name, given in (
                ("SearchFilter", search_fields),
                ("OrderingFilter", ordering_fields),
            )
            if given
        ]
        if needed_backends:
            existing = re.search(
                r"^\s+filter_backends\s*=\s*\[(.*?)\]", block, re.MULTILINE
            )
            current = (
                [b.strip() for b in existing.group(1).split(",") if b.strip()]
                if existing
                else []
            )
            merged = current + [b for b in needed_backends if b not in current]
            block, _ = _set_class_attr(
                block, "filter_backends", "[" + ", ".join(merged) + "]"
            )
            changes.append("filter_backends updated")

        if changes:
            content = (
                content[: block_match.start(1)] + block + content[block_match.end(1):]
            )
            path.write_text(content, encoding="utf-8")
            for import_line in viewset_option_imports(
                pagination=pagination,
                throttles=throttles,
                search_fields=search_fields,
                ordering_fields=ordering_fields,
            ):
                ensure_import(path, import_line)
        return changes

    applied = await asyncio.to_thread(_update)
    return AgentResult(
        success=True,
        message=(
            f"'{class_name}' updated: {', '.join(applied) if applied else 'no changes'}"
        ),
        data={"app": app, "viewset": class_name, "changes": applied},
    )


@agent_function
async def register_route(
    app: str,
    model_name: str,
    url_prefix: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Register a ViewSet with the app's router in ``apps/<app>/urls.py``.

    Appends ``router.register("<prefix>", <ModelName>ViewSet)`` to the app's
    ``urls.py`` and ensures the ViewSet is imported there, exposing the full
    CRUD route set (plus the ``POST /<prefix>/query/`` endpoint) once the app's
    router is included by the project ``urls.py``. Prefer this over editing
    ``urls.py`` with ``write_file``.

    Args:
        app: App directory name.
        model_name: The model whose ``<ModelName>ViewSet`` to register.
        url_prefix: URL segment to mount the ViewSet under. Defaults to the app
            name when ``None``.
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        app (str): the app name.
        viewset (str): the registered ``<ModelName>ViewSet`` class name.
        prefix (str): the URL segment the ViewSet is mounted under.

    Notes:
        - Fails (``success=False``) when ``apps/<app>/urls.py`` is missing or the
          ViewSet is already registered.
    """
    path = _urls_file(app, project_root)
    if not path.exists():
        return AgentResult(success=False, message=f"urls.py not found at {path}")

    prefix = url_prefix or app
    viewset_name = f"{model_name}ViewSet"

    def _write() -> None:
        content = path.read_text(encoding="utf-8")
        register_line = f'router.register("{prefix}", {viewset_name})'
        if register_line in content:
            raise AgentError(
                f"Route for '{viewset_name}' already registered",
                code="already_exists",
                viewset=viewset_name,
                prefix=prefix,
            )
        ensure_import(path, f"from .views import {viewset_name}")
        # Append the register call after any existing router.register lines,
        # or just append to the file
        content = path.read_text(encoding="utf-8")
        content = content.rstrip("\n") + f"\n{register_line}\n"
        path.write_text(content, encoding="utf-8")

    await asyncio.to_thread(_write)
    return AgentResult(
        success=True,
        message=f"'{viewset_name}' registered at '{prefix}/'",
        data={"app": app, "viewset": viewset_name, "prefix": prefix},
    )


@agent_function
async def generate_crud(
    app: str,
    model_name: str,
    fields: list[dict],
    serializer_fields: list[str] | None = None,
    read_only_fields: list[str] | None = None,
    permission: str = "IsAuthenticatedOrReadOnly",
    meta: dict | None = None,
    extra_fields: list[dict] | None = None,
    validate_fields: list[str] | None = None,
    read_only: bool = False,
    lookup_field: str | None = None,
    pagination: str | None = None,
    throttles: list[str] | None = None,
    search_fields: list[str] | None = None,
    ordering_fields: list[str] | None = None,
    filterset: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """One-shot scaffold: create model + serializer + viewset + register route.

    Args:
        app: App directory name.
        model_name: PascalCase class name.
        fields: Field spec dicts (see :func:`~zeeb_agents.models.create_model`).
        serializer_fields: Fields to expose.  Defaults to all model field names
            plus ``"id"``.
        read_only_fields: Read-only serializer fields.
        permission: ViewSet permission class.
        meta: ``class Meta`` dict forwarded to
            :func:`~zeeb_agents.models.create_model`.
        extra_fields: Declared serializer field specs forwarded to
            :func:`~zeeb_agents.serializers.create_serializer`.
        validate_fields: ``validate_<field>`` stubs forwarded to
            :func:`~zeeb_agents.serializers.create_serializer`.
        read_only: Generate a ``ReadOnlyModelViewSet`` (forwarded to
            :func:`create_viewset`).
        lookup_field: Detail-lookup field (forwarded to :func:`create_viewset`).
        pagination: Pagination alias/class (forwarded to :func:`create_viewset`).
        throttles: Throttle class names (forwarded to :func:`create_viewset`).
        search_fields: Search fields (forwarded to :func:`create_viewset`).
        ordering_fields: Ordering fields (forwarded to :func:`create_viewset`).
        filterset: FilterSet class name (forwarded to :func:`create_viewset`).
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        steps (list[str]): human-readable description of each completed step
            (model, serializer, viewset, route registration).

    Notes:
        - Best-effort: on partial failure it returns ``success=False`` with
          ``data={"steps_completed": [...], "errors": [...]}`` so you can see
          how far it got and repair with the individual tools.
        - Migrations are *not* run — follow up with
          :func:`~zeeb_agents.migrations.make_migrations` and
          :func:`~zeeb_agents.migrations.run_migrations`.
    """
    steps: list[str] = []
    errors: list[str] = []

    root = project_root

    # 1. Model
    result = await create_model(app, model_name, fields, meta=meta, project_root=root)
    if result.success:
        steps.append(f"Created model '{model_name}'")
    else:
        errors.append(f"model: {result.message}")

    # 2. Serializer
    ser_fields = serializer_fields or (["id"] + [f["name"] for f in fields])
    result = await create_serializer(
        app,
        model_name,
        ser_fields,
        read_only_fields,
        extra_fields=extra_fields,
        validate_fields=validate_fields,
        project_root=root,
    )
    if result.success:
        steps.append(f"Created serializer '{model_name}Serializer'")
    else:
        errors.append(f"serializer: {result.message}")

    # 3. ViewSet
    result = await create_viewset(
        app,
        model_name,
        permission=permission,
        read_only=read_only,
        lookup_field=lookup_field,
        pagination=pagination,
        throttles=throttles,
        search_fields=search_fields,
        ordering_fields=ordering_fields,
        filterset=filterset,
        project_root=root,
    )
    if result.success:
        steps.append(f"Created viewset '{model_name}ViewSet'")
    else:
        errors.append(f"viewset: {result.message}")

    # 4. Route
    result = await register_route(app, model_name, project_root=root)
    if result.success:
        steps.append(f"Registered route '{app}/'")
    else:
        errors.append(f"route: {result.message}")

    if errors:
        return AgentResult(
            success=False,
            message=f"CRUD generation partially failed: {'; '.join(errors)}",
            data={"steps_completed": steps, "errors": errors},
        )
    return AgentResult(
        success=True,
        message=f"CRUD for '{model_name}' generated successfully",
        data={"steps": steps},
    )


@agent_function
async def list_endpoints(project_root: Path | None = None) -> AgentResult:
    """Return all ``router.register(...)`` calls found across all app ``urls.py`` files.

    Returns data (on success):
        endpoints (list[dict]): each ``{"app": str, "prefix": str,
            "viewset": str}``.
        count (int): len(endpoints).

    Notes:
        - This finds only ViewSet registrations. For a full route inventory
          including plain function routes, use
          :func:`~zeeb_agents.schema.list_all_routes`.
    """
    root = project_root
    from zeeb_agents._utils.project import list_apps

    def _scan() -> list[dict]:
        endpoints = []
        for app in list_apps(root):
            urls_path = _urls_file(app, root)
            if not urls_path.exists():
                continue
            content = urls_path.read_text(encoding="utf-8")
            for m in re.finditer(r'router\.register\(["\']([^"\']+)["\'],\s*(\w+)', content):
                endpoints.append({
                    "app": app,
                    "prefix": m.group(1),
                    "viewset": m.group(2),
                })
        return endpoints

    endpoints = await asyncio.to_thread(_scan)
    return AgentResult(
        success=True,
        message=f"Found {len(endpoints)} registered endpoint(s)",
        data={"endpoints": endpoints, "count": len(endpoints)},
    )
