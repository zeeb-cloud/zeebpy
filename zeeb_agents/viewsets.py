"""Agent functions for ViewSet and route management."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.code_gen import (
    append_block,
    class_exists,
    ensure_import,
    pluralize,
    render_action_method,
    render_viewset_class,
    resolve_authentication,
    resolve_pagination,
    resolve_permissions,
    skip_result,
    validate_if_exists,
    validate_throttles,
    viewset_option_imports,
)
from zeeb_agents._utils.errors import AgentError, fail
from zeeb_agents._utils.project import require_project_root
from zeeb_agents._utils.validation import ensure_app_exists, ensure_identifier
from zeeb_agents._utils.wiring import ensure_app_urls_included, ensure_installed_app
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
    permission: str | list[str] = "IsAuthenticatedOrReadOnly",
    read_only: bool = False,
    lookup_field: str | None = None,
    pagination: str | None = None,
    throttles: list[str] | None = None,
    search_fields: list[str] | None = None,
    ordering_fields: list[str] | None = None,
    filterset: str | None = None,
    authentication: str | list[str] | None = None,
    if_exists: str = "error",
    register: bool = False,
    url_prefix: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Append a ``ModelViewSet`` subclass to ``apps/<app>/views.py``.

    Args:
        app: App directory name.
        model_name: The model this viewset exposes (e.g. ``"Post"``).
        serializer_class: Override the serializer class name.  Defaults to
            ``"<ModelName>Serializer"``.
        permission: Permission class(es) — a single name or a list; every
            listed class must allow the request (AND semantics). Each entry
            is a built-in ``zeeb_api.permissions`` name (``AllowAny``,
            ``IsAuthenticated``, ``IsAdminUser``,
            ``IsAuthenticatedOrReadOnly``, ``IsOwner``, ``IsOwnerOrReadOnly``,
            ``ModelPermissions``), a custom class from this app's
            ``permissions.py`` (scaffold with
            :func:`~zeeb_agents.permissions_scaffold.create_permission_class`),
            or a dotted ``apps.<app>.permissions.<Class>`` reference to
            another app's class. Matching imports are added automatically;
            for OR-logic compose a custom class instead.
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
        authentication: Authentication class(es) for this viewset — a single
            name or a list, tried in order at request time (the first class
            that recognizes the request's credentials wins). Each entry is a
            built-in ``zeeb_api.authentication`` name (``JWTAuthentication``,
            ``JWTStatelessAuthentication``, ``OAuth2BearerAuthentication``),
            a hand-written class from this app's ``authentication.py``, or a
            dotted ``apps.<app>.authentication.<Class>`` reference. Omitted
            (default), no ``authentication_classes`` attribute is emitted and
            the project's global auth middleware stays in charge. Note: a
            non-empty list takes ownership — requests only the middleware
            would authenticate are treated as anonymous on this viewset.
        if_exists: ``"error"`` (default) or ``"skip"`` (succeed and change
            nothing if the ViewSet already exists — makes retries idempotent).
        register: When true, also register the ViewSet's route in
            ``apps/<app>/urls.py`` (folds in :func:`register_route`) so a single
            call scaffolds *and* mounts it. Default false preserves the two-step
            scaffold-then-register flow; ``generate_crud`` is the recommended
            one-shot.
        url_prefix: URL segment to mount under when ``register`` is true
            (defaults to the pluralized lowercase model name, ``Company`` →
            ``companies``).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        app (str): the app directory name
        model (str): the model name passed in
        viewset (str): the generated class name (``"<ModelName>ViewSet"``)
        read_only (bool): whether a ``ReadOnlyModelViewSet`` was generated
        options (list[str]): names of the optional class attributes emitted
        registered (bool): whether the route was registered (only when
            ``register=True``)
        prefix (str): the URL segment registered under (only when
            ``register=True`` and registration succeeded)

    Notes:
        - Failures carry ``error_code`` in ``data`` (``app_not_found``,
          ``already_exists``, ``invalid_permission``, ``invalid_input``, …)
          plus close-match ``suggestions`` where applicable.
        - With ``register=True``, a failed route registration fails the whole
          call (``success=False`` with ``data["viewset_created"]`` /
          ``data["registration_error"]``): a ViewSet that exists but is not
          routed would 404 silently. The class write is kept — re-running the
          same call (idempotent) or ``register_route`` repairs it.
    """
    ensure_identifier(model_name, "model name")
    validate_if_exists(if_exists)
    if pagination:
        resolve_pagination(pagination)
    if throttles:
        validate_throttles(throttles)
    if filterset:
        ensure_identifier(filterset, "filterset class name")
    path = _views_file(app, project_root)
    if not path.exists():
        return fail(f"views.py not found at {path}", code="file_not_found", missing="views.py")
    perm_imports, perm_refs = resolve_permissions(permission, path.parent)
    auth_imports, auth_refs = (
        resolve_authentication(authentication, path.parent)
        if authentication is not None
        else ([], [])
    )

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
            perm_refs,
            read_only=read_only,
            lookup_field=lookup_field,
            pagination=pagination,
            throttles=throttles,
            search_fields=search_fields,
            ordering_fields=ordering_fields,
            filterset=filterset,
            authentication=auth_refs or None,
        )
        ensure_import(path, "from zeeb_api import viewsets, permissions")
        for import_line in (*perm_imports, *auth_imports):
            ensure_import(path, import_line)
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
                ("authentication_classes", authentication),
            )
            if given
        ]
        return options

    skipped = False
    try:
        options = await asyncio.to_thread(_write)
    except AgentError as exc:
        if if_exists == "skip" and (exc.result.data or {}).get("error_code") == "already_exists":
            options, skipped = [], True
        else:
            raise

    data: dict = {
        "app": app,
        "model": model_name,
        "viewset": class_name,
        "read_only": read_only,
        "options": options,
    }
    if skipped:
        data["skipped"] = True
    verb = "already exists; skipped" if skipped else "created"
    message = f"'{class_name}' {verb} in apps/{app}/views.py"

    if register:
        # Re-registration is idempotent under skip so a resumed create_viewset
        # still wires a route that a prior run failed to register.
        reg = await register_route(
            app,
            model_name,
            url_prefix=url_prefix,
            if_exists="skip",
            project_id=project_root,
        )
        data["registered"] = reg.success
        if reg.success:
            prefix = (reg.data or {}).get("prefix")
            data["prefix"] = prefix
            message += f"; route registered at '{prefix}/'"
        else:
            # A ViewSet that exists but is not routed is exactly the silent
            # 404 this tool is asked to prevent — surface it as a failure.
            # The class write is kept; re-running (if_exists="skip") repairs.
            data["viewset_created"] = not skipped
            data["registration_error"] = reg.message
            data["error_code"] = (reg.data or {}).get("error_code")
            data["steps_completed"] = [f"Created viewset '{class_name}'"]
            return AgentResult(
                success=False,
                message=(
                    f"'{class_name}' {verb} in apps/{app}/views.py but its route "
                    f"was not registered: {reg.message} The viewset is not served"
                    " — fix the cause and re-run (idempotent), or register with"
                    " register_route."
                ),
                data=data,
            )

    return AgentResult(success=True, message=message, data=data)


@agent_function
async def add_viewset_action(
    app: str,
    model_name: str,
    action_name: str,
    detail: bool = True,
    methods: list[str] | None = None,
    body: str | None = None,
    url_path: str | None = None,
    request_serializer: str | None = None,
    response_serializer: str | None = None,
    request_schema: str | None = None,
    response_schema: str | None = None,
    permission: str | list[str] | None = None,
    imports: list[str] | None = None,
    if_exists: str = "error",
    project_root: Path | None = None,
) -> AgentResult:
    """Append a custom ``@action`` method to an existing ViewSet.

    Adds a routed extra endpoint to the ViewSet (e.g. ``POST
    /<prefix>/{id}/publish/`` for a detail action named ``publish``). Prefer
    this over editing ``views.py`` with ``write_file``.

    Wire request/response serializers (or plain Pydantic schemas) so the
    endpoint validates its request body and documents/shapes its response —
    the router turns these into FastAPI request-body validation and the
    OpenAPI ``response_model``. Inside the action, read the validated body with
    ``self.get_action_request_body()``.

    Args:
        app: App directory name.
        model_name: The model whose ``<ModelName>ViewSet`` to extend.
        action_name: Snake-case method name (e.g. ``"publish"``). Also the
            default URL segment (``/<prefix>/{id}/publish/``) unless *url_path*
            overrides it.
        detail: Whether the action operates on a single instance (``pk`` in URL).
        methods: HTTP methods (default: ``["get"]``).
        body: The action implementation, as Python source. Pass the real logic
            here so you **never need ``write_file`` for the action body**. The
            snippet is dedented and indented automatically (you may pass it
            flush-left or pre-indented) and should ``return`` a JSON-serializable
            value. The method receives ``self``, ``request`` and (for detail
            actions) ``pk``; use ``await self.get_object()`` to load the instance
            and ``self.get_action_request_body()`` to read the validated request
            body. When omitted, a serializer-aware scaffold is generated (or a
            ``pass  # TODO`` placeholder if no serializer/schema is wired).
        url_path: Override the URL segment (defaults to *action_name*), e.g.
            ``"publish-now"``.
        request_serializer: Serializer class name (from the app's
            ``serializers.py``) used to validate the request body.
        response_serializer: Serializer class name used for the response
            model / OpenAPI schema.
        request_schema: Pydantic model name (from the app's ``serializers.py``)
            for request-body validation — an alternative to *request_serializer*.
        response_schema: Pydantic model name for the response model — an
            alternative to *response_serializer*.
        permission: Per-action permission class(es) — a single name or a
            list; every listed class must allow the request (AND semantics).
            Overrides the ViewSet's class-level permissions for this action
            only. Each entry is a built-in ``zeeb_api.permissions`` name, a
            custom class from this app's ``permissions.py``, or a dotted
            ``apps.<app>.permissions.<Class>`` reference; matching imports
            are added automatically. (Authentication is viewset-level, not
            per-action — set it via ``create_viewset``/``update_viewset``.)
        imports: Extra import lines the *body* needs (e.g. ``["from
            zeeb_api.exceptions import ResourceConflictException"]``) — added
            idempotently to ``views.py``, same as ``create_route``'s imports.
        if_exists: What to do when the ViewSet already defines a method named
            *action_name*: ``"error"`` (default) fails with
            ``already_exists``; ``"skip"`` returns success with
            ``data["skipped"]=True``. Note: previously a duplicate was
            silently appended (shadowing the original) — that was a bug.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        app (str): the app directory name
        viewset (str): the target class name (``"<ModelName>ViewSet"``)
        action (str): the action method name added
        url_path (str): the URL segment (``url_path`` or *action_name*)
        wiring (dict): the serializer/schema/permission names that were wired
            (only keys that were set — e.g. ``{"response_serializer": "..."}``)

    Notes:
        - On failure (missing ``views.py``, or the ViewSet class is not found)
          ``data`` is ``None``; invalid method/permission/identifier inputs fail
          with ``error_code`` (and ``suggestions`` for permissions).
        - Serializer/schema classes are imported from ``.serializers`` — define
          them in ``apps/<app>/serializers.py`` (or add the import yourself).

    Example::

        await add_viewset_action(
            "blog", "Post", "publish", detail=True, methods=["post"],
            response_serializer="PostSerializer", permission="IsAdminUser",
            body='''
                post = await self.get_object()
                post.published = True
                await post.save()
                return PostSerializer(post).data
            ''',
        )
    """
    ensure_identifier(action_name, "action name")
    if if_exists not in ("error", "skip"):
        return fail(
            f"if_exists must be 'error' or 'skip', got {if_exists!r}",
            code="invalid_input",
        )
    action_methods = [m.lower() for m in (methods or ["get"])]
    invalid = [m for m in action_methods if m not in _ACTION_METHODS]
    if invalid:
        return fail(
            f"Invalid method(s) {', '.join(sorted(invalid))}. "
            f"Must be one of: {', '.join(sorted(_ACTION_METHODS))}",
            code="invalid_input",
        )
    # Serializer/schema references must be importable class names.
    serializer_refs = {
        "request_serializer": request_serializer,
        "response_serializer": response_serializer,
        "request_schema": request_schema,
        "response_schema": response_schema,
    }
    for label, ref in serializer_refs.items():
        if ref:
            ensure_identifier(ref, label)

    path = _views_file(app, project_root)
    if not path.exists():
        return fail(f"views.py not found at {path}", code="file_not_found", missing="views.py")
    perm_imports, perm_refs = (
        resolve_permissions(permission, path.parent) if permission else ([], [])
    )

    class_name = f"{model_name}ViewSet"

    def _insert() -> bool:
        """Splice the action into the class; returns False on a skipped duplicate."""
        content = path.read_text(encoding="utf-8")
        if not class_exists(content, class_name):
            raise AgentError(
                f"'{class_name}' not found in {path}",
                code="model_not_found",
                viewset=class_name,
            )

        class_pattern = re.compile(
            rf"(^class {re.escape(class_name)}\b.*?)(?=^\S|\Z)",
            re.DOTALL | re.MULTILINE,
        )
        class_match = class_pattern.search(content)
        block = class_match.group(1) if class_match else ""
        if re.search(rf"^\s+(?:async\s+)?def {re.escape(action_name)}\(", block, re.MULTILINE):
            if if_exists == "skip":
                return False
            raise AgentError(
                f"'{class_name}' already defines '{action_name}' — pass "
                "if_exists='skip' to keep it, or pick a different action name",
                code="already_exists",
                viewset=class_name,
                action=action_name,
            )

        action_code = render_action_method(
            action_name,
            detail=detail,
            methods=action_methods,
            body=body,
            url_path=url_path,
            request_serializer=request_serializer,
            response_serializer=response_serializer,
            request_schema=request_schema,
            response_schema=response_schema,
            permission=perm_refs or None,
        )

        # Insert before the end of the class body (before the next top-level
        # statement or EOF). The ``^`` anchors are essential: without them the
        # pattern also matches the commented example ViewSet in the scaffolded
        # views.py and splices the action into a comment block.
        def _replace(m: re.Match) -> str:
            block = m.group(1)
            if not block.endswith("\n"):
                block += "\n"
            return block + "\n" + action_code

        new_content = class_pattern.sub(_replace, content, count=1)
        path.write_text(new_content, encoding="utf-8")
        # After writing the new content — ensure_import edits the file on disk,
        # so imports must run last or the write above clobbers them.
        ensure_import(path, "from zeeb_api.viewsets import action")
        if permission:
            ensure_import(path, "from zeeb_api import viewsets, permissions")
        for import_line in perm_imports:
            ensure_import(path, import_line)
        for import_line in imports or []:
            ensure_import(path, import_line)
        for ref in serializer_refs.values():
            if ref:
                ensure_import(path, f"from .serializers import {ref}")
        return True

    inserted = await asyncio.to_thread(_insert)
    if not inserted:
        return AgentResult(
            success=True,
            message=f"'{class_name}' already defines '{action_name}'; skipped",
            data={
                "app": app,
                "viewset": class_name,
                "action": action_name,
                "skipped": True,
            },
        )
    wiring = {label: ref for label, ref in serializer_refs.items() if ref}
    if permission:
        wiring["permission"] = permission
    return AgentResult(
        success=True,
        message=f"Action '{action_name}' added to '{class_name}'",
        data={
            "app": app,
            "viewset": class_name,
            "action": action_name,
            "url_path": url_path or action_name,
            "wiring": wiring,
        },
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
    permission: str | list[str] | None = None,
    lookup_field: str | None = None,
    pagination: str | None = None,
    throttles: list[str] | None = None,
    search_fields: list[str] | None = None,
    ordering_fields: list[str] | None = None,
    authentication: str | list[str] | None = None,
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
        permission: New permission class(es) — a single name or a list;
            every listed class must allow the request (AND semantics).
            Replaces the existing ``permission_classes``. Each entry is a
            built-in ``zeeb_api.permissions`` name, a custom class from this
            app's ``permissions.py``, or a dotted
            ``apps.<app>.permissions.<Class>`` reference; matching imports
            are added automatically.
        lookup_field: New detail-lookup field (e.g. ``"slug"``).
        pagination: ``"page"``, ``"limit_offset"``, ``"cursor"`` or a
            ``zeeb_api.pagination`` class name.
        throttles: Throttle class names from ``zeeb_api.throttling``.
        search_fields: Fields for ``?search=…`` full-text search.
        ordering_fields: Fields allowed in ``?ordering=…``.
        authentication: Authentication class(es) for this viewset — a single
            name or a list, tried in order (first match wins). Sets or
            replaces ``authentication_classes``; there is no option to remove
            the attribute once set — omit to leave it unchanged. Each entry
            is a built-in ``zeeb_api.authentication`` name
            (``JWTAuthentication``, ``JWTStatelessAuthentication``,
            ``OAuth2BearerAuthentication``), a hand-written class from this
            app's ``authentication.py``, or a dotted
            ``apps.<app>.authentication.<Class>`` reference.
        project_id: The host-assigned project id (required).

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
    if pagination:
        resolve_pagination(pagination)
    if throttles:
        validate_throttles(throttles)
    path = _views_file(app, require_project_root(project_root))
    if not path.exists():
        return fail(f"views.py not found at {path}", code="file_not_found", missing="views.py")
    perm_imports, perm_refs = (
        resolve_permissions(permission, path.parent) if permission else ([], [])
    )
    auth_imports, auth_refs = (
        resolve_authentication(authentication, path.parent)
        if authentication is not None
        else ([], [])
    )

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

        # authentication_classes is set before permission_classes: fresh
        # insertions of both anchor at the same spot, so setting permission
        # second lands it above authentication — matching create_viewset's
        # attribute order.
        if auth_refs:
            block, _ = _set_class_attr(
                block, "authentication_classes", "[" + ", ".join(auth_refs) + "]"
            )
            changes.append("authentication_classes set")
        if permission:
            block, _ = _set_class_attr(
                block, "permission_classes", "[" + ", ".join(perm_refs) + "]"
            )
            perm_names = [permission] if isinstance(permission, str) else permission
            changes.append(f"permission_classes set to {', '.join(perm_names)}")
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
            for import_line in (*perm_imports, *auth_imports):
                ensure_import(path, import_line)
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
    if_exists: str = "error",
    project_root: Path | None = None,
) -> AgentResult:
    """Register a ViewSet with the app's router in ``apps/<app>/urls.py``.

    Appends ``router.register("<prefix>", <ModelName>ViewSet)`` to the app's
    ``urls.py``, ensures the ViewSet is imported there, and then ensures the
    full serve chain — the app is in ``INSTALLED_APPS`` and the app router is
    included by the project ``urls.py`` (both idempotent) — exposing the full
    CRUD route set (plus the ``POST /<prefix>/query/`` endpoint). Prefer this
    over editing ``urls.py`` with ``write_file``.

    Args:
        app: App directory name.
        model_name: The model whose ``<ModelName>ViewSet`` to register.
        url_prefix: URL segment to mount the ViewSet under. Defaults to the
            pluralized lowercase model name (``Company`` → ``companies``,
            ``Status`` → ``statuses``) so each ViewSet in an app gets its own
            segment.
        if_exists: ``"error"`` (default) or ``"skip"`` (succeed and change
            nothing if the route is already registered — makes retries idempotent).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        app (str): the app name.
        viewset (str): the registered ``<ModelName>ViewSet`` class name.
        prefix (str): the URL segment the ViewSet is mounted under.
        installed (bool): the app is in ``INSTALLED_APPS`` (ensured by this call).
        urls_included (bool): the app router is included by the project
            ``urls.py`` (ensured by this call).

    Notes:
        - Fails (``success=False``) when ``apps/<app>/urls.py`` is missing, when
          (with the default ``if_exists="error"``) the ViewSet is already
          registered, or when the prefix is already taken by a *different*
          ViewSet (``prefix_conflict`` — pass ``url_prefix`` to mount elsewhere;
          never skippable, because the later registration would be silently
          shadowed at runtime).
    """
    validate_if_exists(if_exists)
    root = require_project_root(project_root)
    path = _urls_file(app, root)
    if not path.exists():
        return fail(f"urls.py not found at {path}", code="file_not_found", missing="urls.py")

    prefix = url_prefix or pluralize(model_name.lower())
    viewset_name = f"{model_name}ViewSet"

    def _write() -> None:
        content = path.read_text(encoding="utf-8")
        register_line = f'router.register("{prefix}", {viewset_name})'
        # Scan uncommented lines only — the app scaffold ships a commented
        # ``# router.register(...)`` example that must not count.
        active_lines = [
            line for line in content.splitlines() if not line.lstrip().startswith("#")
        ]
        if any(register_line in line for line in active_lines):
            raise AgentError(
                f"Route for '{viewset_name}' already registered",
                code="already_exists",
                viewset=viewset_name,
                prefix=prefix,
            )
        conflict_re = re.compile(
            rf'router\.register\(\s*"{re.escape(prefix)}"\s*,\s*(\w+)'
        )
        for line in active_lines:
            conflict = conflict_re.search(line)
            if conflict:
                raise AgentError(
                    f"Prefix '{prefix}' is already registered to {conflict.group(1)} "
                    f"in apps/{app}/urls.py — a second registration at the same "
                    f"prefix is silently shadowed at runtime. Pass url_prefix to "
                    f"mount {viewset_name} under a different segment.",
                    code="prefix_conflict",
                    prefix=prefix,
                    registered_viewset=conflict.group(1),
                    viewset=viewset_name,
                )
        ensure_import(path, f"from .views import {viewset_name}")
        # Append the register call after any existing router.register lines,
        # or just append to the file
        content = path.read_text(encoding="utf-8")
        content = content.rstrip("\n") + f"\n{register_line}\n"
        path.write_text(content, encoding="utf-8")

    def _ensure_serve_chain() -> None:
        # A registered route is only reachable when the app is installed and
        # its router is included by the project urls.py; ensure both (repair
        # semantics — also runs on the skip path).
        ensure_installed_app(root, app)
        ensure_app_urls_included(root, app)

    try:
        await asyncio.to_thread(_write)
    except AgentError as exc:
        if if_exists == "skip" and (exc.result.data or {}).get("error_code") == "already_exists":
            await asyncio.to_thread(_ensure_serve_chain)
            return skip_result(
                f"'{viewset_name}' already registered at '{prefix}/'; skipped",
                app=app,
                viewset=viewset_name,
                prefix=prefix,
                installed=True,
                urls_included=True,
            )
        raise
    await asyncio.to_thread(_ensure_serve_chain)
    return AgentResult(
        success=True,
        message=f"'{viewset_name}' registered at '{prefix}/'",
        data={
            "app": app,
            "viewset": viewset_name,
            "prefix": prefix,
            "installed": True,
            "urls_included": True,
        },
    )


@agent_function
async def generate_crud(
    app: str,
    model_name: str,
    fields: list[dict],
    serializer_fields: list[str] | None = None,
    read_only_fields: list[str] | None = None,
    permission: str | list[str] = "IsAuthenticatedOrReadOnly",
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
    authentication: str | list[str] | None = None,
    url_prefix: str | None = None,
    migrate: bool = False,
    project_root: Path | None = None,
) -> AgentResult:
    """One-shot scaffold: create model + serializer + viewset + register route — wired and served.

    Args:
        app: App directory name.
        model_name: PascalCase class name.
        fields: Field spec dicts (see :func:`~zeeb_agents.models.create_model`).
        serializer_fields: Fields to expose.  Defaults to all model field names
            plus ``"id"``.
        read_only_fields: Read-only serializer fields.
        permission: ViewSet permission class(es) — a single name or a list
            (AND semantics): built-in names, custom classes from this app's
            ``permissions.py``, or dotted ``apps.<app>.permissions.<Class>``
            (see :func:`create_viewset`).
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
        authentication: Authentication class(es) for the viewset — a single
            name or a list, tried in order (first match wins); omitted, the
            project's global auth middleware stays in charge (forwarded to
            :func:`create_viewset`).
        url_prefix: URL segment to mount the ViewSet under (forwarded to
            :func:`register_route`). Defaults to the pluralized lowercase
            model name (``Company`` → ``companies``).
        migrate: When true, also run ``make_migrations`` + ``run_migrations``
            after scaffolding so the new table is live in one call. Default false
            keeps the DB-touching step explicit.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        steps (list[str]): human-readable description of each completed step
            (model, serializer, viewset, route registration, app wiring, and —
            when ``migrate=True`` — migration creation/apply).
        migrated (dict): present only when ``migrate=True`` —
            ``{"created": str | None, "applied": list[str]}``.

    Notes:
        - Resumable: each step is issued with ``if_exists="skip"``, so re-running
          after a partial failure completes the missing steps instead of walling
          on ``already_exists``.
        - Best-effort: on partial failure it returns ``success=False`` with
          ``data={"steps_completed": [...], "errors": [...]}`` so you can see
          how far it got and repair with the individual tools.
        - With ``migrate=False`` (default) migrations are *not* run — follow up
          with :func:`~zeeb_agents.migrations.make_migrations` and
          :func:`~zeeb_agents.migrations.run_migrations`.
    """
    steps: list[str] = []
    errors: list[str] = []

    root = project_root

    # 1. Model
    result = await create_model(
        app, model_name, fields, meta=meta, if_exists="skip", project_id=root
    )
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
        if_exists="skip",
        project_id=root,
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
        authentication=authentication,
        if_exists="skip",
        project_id=root,
    )
    if result.success:
        steps.append(f"Created viewset '{model_name}ViewSet'")
    else:
        errors.append(f"viewset: {result.message}")

    # 4. Route
    result = await register_route(
        app, model_name, url_prefix=url_prefix, if_exists="skip", project_id=root
    )
    if result.success:
        prefix = (result.data or {}).get("prefix", url_prefix or "")
        steps.append(f"Registered route '{prefix}/'")
    else:
        errors.append(f"route: {result.message}")

    # 5. Wiring — installed + included, or nothing above is actually served.
    # register_route already ensures both on success; repeating them here keeps
    # a partial run (e.g. the route step failed) migratable and servable.
    from zeeb_agents.project import install_app, wire_app_urls

    for wiring_call, label in (
        (install_app, "INSTALLED_APPS"),
        (wire_app_urls, "project urls.py"),
    ):
        result = await wiring_call(app, project_id=root)
        if result.success:
            if (result.data or {}).get("changed"):
                steps.append(f"Wired app '{app}' into {label}")
        else:
            errors.append(f"wiring: {result.message}")

    if errors:
        return AgentResult(
            success=False,
            message=f"CRUD generation partially failed: {'; '.join(errors)}",
            data={"steps_completed": steps, "errors": errors},
        )

    data: dict = {"steps": steps}
    if migrate:
        from zeeb_agents.migrations import make_migrations, run_migrations

        mk = await make_migrations(project_id=root)
        created = mk.data.get("created") if mk.success and mk.data else None
        applied: list[str] = []
        if mk.success:
            steps.append(
                f"Created migration '{created}'" if created else "No migration needed"
            )
            rn = await run_migrations(project_id=root)
            if rn.success:
                applied = rn.data.get("applied", []) if rn.data else []
                steps.append(f"Applied {len(applied)} migration(s)")
            else:
                errors.append(f"run_migrations: {rn.message}")
        else:
            errors.append(f"make_migrations: {mk.message}")
        data["migrated"] = {"created": created, "applied": applied}
        if errors:
            data["errors"] = errors
            return AgentResult(
                success=False,
                message=f"CRUD scaffolded but migration failed: {'; '.join(errors)}",
                data={"steps_completed": steps, **data},
            )

    return AgentResult(
        success=True,
        message=f"CRUD for '{model_name}' generated successfully",
        data=data,
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
            for m in re.finditer(r'^[ \t]*router\.register\(["\']([^"\']+)["\'],\s*(\w+)', content, re.MULTILINE):
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
