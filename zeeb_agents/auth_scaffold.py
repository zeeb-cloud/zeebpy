"""Agent functions for wiring zeeb_api authentication into a project."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.code_gen import (
    append_block,
    class_exists,
    ensure_import,
    ensure_middleware,
    find_settings_file,
    render_field_line,
    set_or_append_setting,
)
from zeeb_agents._utils.errors import AgentError, close_matches, fail
from zeeb_agents._utils.project import require_project_root
from zeeb_agents._utils.validation import (
    ensure_app_exists,
    ensure_identifier,
    validate_field_specs,
)

# Provider names zeeb_api can instantiate without a "class" dotted path
# (zeeb_api/auth/oauth/registry.py KNOWN_PROVIDER_CLASSES).
KNOWN_OAUTH_PROVIDERS = ("azure", "github", "google")


def _project_urls_file(root: Path) -> Path:
    """Return the project package's ``urls.py`` (next to ``settings.py``)."""
    settings_path = find_settings_file(root)
    if settings_path is None:
        raise AgentError(
            "settings.py not found in project",
            code="file_not_found",
        )
    urls_path = settings_path.parent / "urls.py"
    if not urls_path.exists():
        raise AgentError(
            f"urls.py not found at {urls_path}",
            code="file_not_found",
            path=str(urls_path),
        )
    return urls_path


@agent_function
async def setup_auth(
    enable_registration: bool = True,
    url_prefix: str = "/auth",
    access_token_minutes: int | None = None,
    refresh_token_days: int | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Wire zeeb_api's JWT auth router into the project ``urls.py``.

    Adds ``router.include(create_auth_router(...))`` to the project package's
    ``urls.py``, exposing ``POST {prefix}/login``, ``/refresh``, ``/logout``,
    ``GET /me`` and (optionally) ``POST /register``.  Also installs
    ``zeeb_api.middleware.JWTAuthMiddleware`` in ``settings.MIDDLEWARE`` so that
    generated ViewSets (whose permission classes read ``request.state.user``)
    actually see the authenticated user.  Optionally updates the JWT
    token-lifetime settings.

    Args:
        enable_registration: Expose the ``POST {prefix}/register`` endpoint.
        url_prefix: URL prefix for the auth endpoints (default ``"/auth"``).
        access_token_minutes: If given, sets ``JWT_ACCESS_TOKEN_EXPIRE_MINUTES``
            in ``settings.py``.
        refresh_token_days: If given, sets ``JWT_REFRESH_TOKEN_EXPIRE_DAYS``
            in ``settings.py``.
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        prefix (str): the URL prefix the auth router is mounted under
        registration (bool): whether registration is enabled
        wired (bool): ``True`` when the include was added (``False`` when it
            was already present)
        already_wired (bool): inverse of ``wired`` — present for idempotent
            re-runs
        settings_updated (list[str]): settings keys written — includes
            ``"MIDDLEWARE"`` when the auth middleware was newly installed and
            the JWT lifetime keys when those args were given (empty when
            nothing changed)

    Notes:
        - Idempotent: an existing ``create_auth_router`` include leaves
          ``urls.py`` untouched (``already_wired=True``, still
          ``success=True``). The ``MIDDLEWARE`` install is also idempotent, so
          re-running ``setup_auth`` repairs a project whose ViewSets 403 with a
          valid token because ``JWTAuthMiddleware`` was missing.
        - Fails with ``error_code="file_not_found"`` when the project
          ``settings.py``/``urls.py`` cannot be located.
        - Remember to set a real ``JWT_SECRET_KEY`` (e.g. via
          :func:`~zeeb_agents.config.set_env`) before deploying with
          ``DEBUG=False``.
    """
    root = require_project_root(project_root)

    def _wire() -> tuple[bool, list[str]]:
        urls_path = _project_urls_file(root)
        content = urls_path.read_text(encoding="utf-8")
        already = "create_auth_router" in content
        if not already:
            ensure_import(urls_path, "from zeeb_api.auth import create_auth_router")
            include_line = (
                f'router.include(create_auth_router(prefix="{url_prefix}", '
                f"enable_registration={enable_registration}))"
            )
            content = urls_path.read_text(encoding="utf-8")
            content = content.rstrip("\n") + f"\n\n{include_line}\n"
            urls_path.write_text(content, encoding="utf-8")

        updated: list[str] = []
        settings_path = find_settings_file(root)
        if settings_path is None:
            raise AgentError("settings.py not found in project", code="file_not_found")

        # Install the JWT auth middleware. The auth router endpoints
        # (/login, /me, ...) self-authenticate via Depends(get_current_user),
        # but every generated ViewSet enforces auth through permission classes
        # that read request.state.user — which ONLY JWTAuthMiddleware sets.
        # Without this, protected ViewSets 403 even with a valid Bearer token.
        # Idempotent, so re-running setup_auth repairs a project missing it.
        if ensure_middleware(settings_path, "zeeb_api.middleware.JWTAuthMiddleware"):
            updated.append("MIDDLEWARE")

        jwt_updates = {
            "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": access_token_minutes,
            "JWT_REFRESH_TOKEN_EXPIRE_DAYS": refresh_token_days,
        }
        wanted = {k: v for k, v in jwt_updates.items() if v is not None}
        if wanted:
            settings_content = settings_path.read_text(encoding="utf-8")
            for key, value in wanted.items():
                settings_content = set_or_append_setting(settings_content, key, str(value))
                updated.append(key)
            settings_path.write_text(settings_content, encoding="utf-8")
        return already, updated

    already_wired, settings_updated = await asyncio.to_thread(_wire)
    return AgentResult(
        success=True,
        message=(
            f"Auth router already wired at '{url_prefix}'"
            if already_wired
            else f"Auth router wired at '{url_prefix}' "
            f"(registration {'enabled' if enable_registration else 'disabled'})"
        ),
        data={
            "prefix": url_prefix,
            "registration": enable_registration,
            "wired": not already_wired,
            "already_wired": already_wired,
            "settings_updated": settings_updated,
        },
    )


@agent_function
async def setup_oauth(
    provider: str,
    client_id_env: str | None = None,
    client_secret_env: str | None = None,
    scopes: list[str] | None = None,
    redirect_uri: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Configure an OAuth/OIDC provider and wire the OAuth router.

    Adds an ``OAUTH_PROVIDERS`` entry to ``settings.py`` (credentials read
    from environment variables via ``os.getenv``) and includes
    ``create_oauth_router()`` in the project ``urls.py``.

    Args:
        provider: Provider preset name — one of ``"azure"``, ``"github"``,
            ``"google"`` (zeeb_api infers the provider class from the name).
        client_id_env: Environment variable holding the client id.
            Default: ``"<PROVIDER>_CLIENT_ID"``.
        client_secret_env: Environment variable holding the client secret.
            Default: ``"<PROVIDER>_CLIENT_SECRET"``.
        scopes: Optional OAuth scopes to request.
        redirect_uri: If given, sets ``OAUTH_REDIRECT_URI`` in ``settings.py``.
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        provider (str): the provider name configured
        client_id_env (str): env var used for the client id
        client_secret_env (str): env var used for the client secret
        wired (bool): ``True`` when the router include was added (``False``
            when already present)
        settings_updated (list[str]): setting keys written

    Notes:
        - Requires the ``zeebpy[oauth]`` extra at runtime (httpx +
          pyjwt[crypto]).
        - An unknown *provider* fails with ``error_code="invalid_input"``
          and the valid preset names in ``suggestions``/message.  Custom
          providers (a ``class`` dotted path) must be added to
          ``OAUTH_PROVIDERS`` manually.
        - If ``OAUTH_PROVIDERS`` already exists in ``settings.py`` and the
          entry for *provider* cannot be inserted unambiguously, the call
          fails and asks you to edit ``settings.py`` manually.
        - Set the credential env vars (e.g. via
          :func:`~zeeb_agents.config.set_env`) before starting the server.
    """
    provider = provider.lower()
    if provider not in KNOWN_OAUTH_PROVIDERS:
        return fail(
            f"Unknown OAuth provider '{provider}'. "
            f"Valid presets: {', '.join(KNOWN_OAUTH_PROVIDERS)}. "
            "For a custom provider add an OAUTH_PROVIDERS entry with a "
            "'class' dotted path to settings.py manually.",
            code="invalid_input",
            suggestions=close_matches(provider, list(KNOWN_OAUTH_PROVIDERS)),
        )
    id_env = client_id_env or f"{provider.upper()}_CLIENT_ID"
    secret_env = client_secret_env or f"{provider.upper()}_CLIENT_SECRET"
    root = require_project_root(project_root)

    def _configure() -> tuple[bool, list[str]]:
        settings_path = find_settings_file(root)
        if settings_path is None:
            raise AgentError("settings.py not found in project", code="file_not_found")
        content = settings_path.read_text(encoding="utf-8")
        updated: list[str] = []

        entry_lines = [
            f'    "{provider}": {{',
            f'        "client_id": os.getenv("{id_env}"),',
            f'        "client_secret": os.getenv("{secret_env}"),',
        ]
        if scopes:
            scopes_repr = ", ".join(f'"{s}"' for s in scopes)
            entry_lines.append(f'        "scopes": [{scopes_repr}],')
        entry_lines.append("    },")
        entry = "\n".join(entry_lines)

        existing = re.search(r"^OAUTH_PROVIDERS\s*=\s*(.*)$", content, re.MULTILINE)
        if existing is None or existing.group(1).strip() in ("{}", "dict()"):
            # No providers configured yet — write the whole dict.
            rendered = "{\n" + entry + "\n}"
            content = set_or_append_setting(content, "OAUTH_PROVIDERS", rendered)
            updated.append("OAUTH_PROVIDERS")
        elif f'"{provider}"' in content[existing.start():]:
            raise AgentError(
                f"OAUTH_PROVIDERS already contains an entry for '{provider}' — "
                "edit settings.py manually to change it.",
                code="already_exists",
                provider=provider,
            )
        elif existing.group(1).strip() == "{":
            # Multi-line dict — insert the entry right after the opening brace.
            insert_at = existing.end()
            content = content[:insert_at] + "\n" + entry + content[insert_at:]
            updated.append("OAUTH_PROVIDERS")
        else:
            raise AgentError(
                "OAUTH_PROVIDERS exists in settings.py but its layout was not "
                "recognized — add the provider entry manually.",
                code="invalid_input",
                provider=provider,
            )

        if redirect_uri:
            content = set_or_append_setting(
                content, "OAUTH_REDIRECT_URI", f'"{redirect_uri}"'
            )
            updated.append("OAUTH_REDIRECT_URI")
        settings_path.write_text(content, encoding="utf-8")
        ensure_import(settings_path, "import os")

        urls_path = _project_urls_file(root)
        urls_content = urls_path.read_text(encoding="utf-8")
        already = "create_oauth_router" in urls_content
        if not already:
            ensure_import(urls_path, "from zeeb_api.auth.oauth import create_oauth_router")
            urls_content = urls_path.read_text(encoding="utf-8")
            urls_content = (
                urls_content.rstrip("\n") + "\n\nrouter.include(create_oauth_router())\n"
            )
            urls_path.write_text(urls_content, encoding="utf-8")
        return not already, updated

    wired, settings_updated = await asyncio.to_thread(_configure)
    return AgentResult(
        success=True,
        message=(
            f"OAuth provider '{provider}' configured "
            f"(credentials from ${id_env} / ${secret_env})"
        ),
        data={
            "provider": provider,
            "client_id_env": id_env,
            "client_secret_env": secret_env,
            "wired": wired,
            "settings_updated": settings_updated,
        },
    )


@agent_function
async def create_user_model(
    app: str,
    model_name: str = "User",
    extra_fields: list[dict] | None = None,
    set_auth_user_model: bool = True,
    project_root: Path | None = None,
) -> AgentResult:
    """Create a custom user model extending ``zeeb_api.auth.models.AbstractUser``.

    ``AbstractUser`` already provides ``email`` (USERNAME_FIELD), ``username``,
    ``first_name``/``last_name``, ``is_active``/``is_staff``/``is_superuser``,
    ``date_joined``, and password hashing — *extra_fields* adds to those.

    Args:
        app: App directory name the model goes into.
        model_name: Class name for the user model (default ``"User"``).
        extra_fields: Additional field spec dicts (same format as
            :func:`~zeeb_agents.models.create_model`).
        set_auth_user_model: Also set ``AUTH_USER_MODEL = "<app>.<model>"`` in
            ``settings.py``.
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        app (str): the app name
        model (str): the user model class name
        auth_user_model (str | None): the ``AUTH_USER_MODEL`` value written
            (``None`` when ``set_auth_user_model=False``)

    Notes:
        - Follow up with :func:`~zeeb_agents.migrations.make_migrations` /
          :func:`~zeeb_agents.migrations.run_migrations` to create the table,
          after which :func:`~zeeb_agents.users.create_user` works against it.
        - Failures carry ``error_code`` in ``data`` (``app_not_found``,
          ``already_exists``, ``invalid_field_spec``, …).
    """
    ensure_identifier(model_name, "model name")
    if extra_fields:
        validate_field_specs(extra_fields)
    root = require_project_root(project_root)
    models_path = ensure_app_exists(app, root) / "models.py"
    if not models_path.exists():
        return AgentResult(success=False, message=f"models.py not found at {models_path}")

    def _write() -> str | None:
        content = models_path.read_text(encoding="utf-8")
        if class_exists(content, model_name):
            raise AgentError(
                f"Model '{model_name}' already exists in {models_path}",
                code="already_exists",
                model=model_name,
            )
        lines = [f"class {model_name}(AbstractUser):"]
        for spec in extra_fields or []:
            lines.append(f"    {render_field_line(spec)}")
        if not extra_fields:
            lines.append("    pass")
        lines.extend(
            [
                "",
                "    class Meta:",
                f'        table_name = "{app}_{model_name.lower()}"',
            ]
        )
        ensure_import(models_path, "from zeeb_orm import Model, fields")
        ensure_import(models_path, "from zeeb_api.auth.models import AbstractUser")
        append_block(models_path, "\n".join(lines))

        if not set_auth_user_model:
            return None
        auth_model = f"{app}.{model_name}"
        settings_path = find_settings_file(root)
        if settings_path is None:
            raise AgentError(
                "settings.py not found in project — user model was created but "
                "AUTH_USER_MODEL could not be set",
                code="file_not_found",
            )
        settings_content = settings_path.read_text(encoding="utf-8")
        settings_content = set_or_append_setting(
            settings_content, "AUTH_USER_MODEL", f'"{auth_model}"'
        )
        settings_path.write_text(settings_content, encoding="utf-8")
        return auth_model

    auth_model = await asyncio.to_thread(_write)
    return AgentResult(
        success=True,
        message=(
            f"User model '{model_name}' created in apps/{app}/models.py"
            + (f" (AUTH_USER_MODEL = '{auth_model}')" if auth_model else "")
        ),
        data={"app": app, "model": model_name, "auth_user_model": auth_model},
    )
