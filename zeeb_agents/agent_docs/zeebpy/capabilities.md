# Zeeb Agent Capabilities

`zeeb_agents` is a standalone async Python library that exposes all Zeeb
scaffolding and management operations as importable async functions.  It has
no MCP dependency — plug it into any MCP server, CLI, or AI agent.

> **New here?** Read `mcp://docs/principles` first — it covers the
> `AgentResult` contract, return-shape conventions, the security model, the
> documented special cases, and the framework concepts (auth, URL
> registration, the generation flow). Call `{prefix}list_capabilities()` for a
> machine-readable inventory of every tool with its signature, structured
> params, parsed return shape, and docstring.

> **The inventory below is auto-generated** from the installed code (a test
> keeps it in sync), so the signatures are always accurate. For the full,
> structured contract of any tool — argument types, defaults, and the exact
> `Returns data:` keys — call
> `{prefix}list_capabilities(include_docstrings=True)` (optionally filtered by
> `module="viewsets"` etc.).

> **`project_id`**: Every tool that operates on a project takes a trailing
> `project_id` — the opaque id the host assigns to a project — as its last
> argument. It is **omitted from the signatures below for brevity**; always
> pass it when calling. A missing id fails with `no_project_id`; an unknown one
> with `project_not_found`. (Project creation is the one exception: see
> `{prefix}create_project`.)

> **Tool name prefix**: Every tool name is written as `{prefix}function_name`.
> When served via MCP the `{prefix}` placeholder is replaced with the prefix
> your MCP server uses (e.g. `zeeb_`, `myapp_`, or an empty string for no
> prefix).

## Return value

Every tool returns an `AgentResult`:

```
success : bool        — True on success, False on error
message : str         — human-readable summary
data    : dict|None   — structured output (see per-tool docs)
```

## Tool Inventory

<!-- BEGIN AUTO-GENERATED INVENTORY -->

### Project & App Management

| Tool | Description |
|---|---|
| `{prefix}create_app(name, wire=True)` | Create a new app inside an existing Zeeb project — wired and served. |
| `{prefix}create_project(name, framework="zeebpy", directory=".")` | Create a new Zeeb project and record its framework. |
| `{prefix}delete_app(name)` | Delete an existing app directory from the project. |
| `{prefix}describe_project()` | Return one computed, cross-checked snapshot of the whole project state. |
| `{prefix}get_project_info()` | Return a summary of the current project structure and settings. |
| `{prefix}get_project_structure(max_depth=3)` | Return a nested directory tree for the project. |
| `{prefix}install_app(app)` | Register an app in ``INSTALLED_APPS`` so migrations see its models. |
| `{prefix}list_apps()` | Return all app directory names found under ``apps/`` in the project. |
| `{prefix}rename_app(old_name, new_name)` | Rename an app directory from *old_name* to *new_name*. |
| `{prefix}wire_app_urls(app, prefix=None)` | Include an app's router in the project ``urls.py`` so its routes are served. |

### Model Management

| Tool | Description |
|---|---|
| `{prefix}add_field(app, model_name, field)` | Add a field to an existing model. |
| `{prefix}add_relationship(app, model_name, rel)` | Add a relationship field to an existing model. |
| `{prefix}create_model(app, model_name, fields, meta=None, if_exists="error")` | Append a new ``Model`` subclass to ``apps/<app>/models.py``. |
| `{prefix}delete_field(app, model_name, field_name)` | Drop a single field from a model — alias of :func:`remove_field`. |
| `{prefix}delete_model(app, model_name)` | Remove a ``Model`` subclass from ``apps/<app>/models.py``. |
| `{prefix}list_models()` | Return all models defined across all apps. |
| `{prefix}remove_field(app, model_name, field_name)` | Remove a field from an existing model. |
| `{prefix}replace_model_fields(app, model_name, fields)` | Replace **all** fields in a model with a new set. |
| `{prefix}update_model(app, model_name, rename_to=None, meta_changes=None)` | Rename a model class and/or update its ``class Meta`` attributes. |

### Serializers

| Tool | Description |
|---|---|
| `{prefix}create_serializer(app, model_name, fields=None, read_only_fields=None, extra_fields=None, validate_fields=None, if_exists="error")` | Append a ``ModelSerializer`` subclass to ``apps/<app>/serializers.py``. |
| `{prefix}update_serializer(app, model_name, fields=None, read_only_fields=None)` | Update the ``fields`` and/or ``read_only_fields`` of an existing serializer. |

### ViewSets & Routing

| Tool | Description |
|---|---|
| `{prefix}add_viewset_action(app, model_name, action_name, detail=True, methods=None, body=None, url_path=None, request_serializer=None, response_serializer=None, request_schema=None, response_schema=None, permission=None)` | Append a custom ``@action`` method to an existing ViewSet. |
| `{prefix}create_route(app, path, method, function_name, response_model=None, body=None, imports=None, if_exists="error")` | Append a standalone FastAPI route handler to ``apps/{app}/views.py`` **and wire it up**. |
| `{prefix}create_viewset(app, model_name, serializer_class=None, permission="IsAuthenticatedOrReadOnly", read_only=False, lookup_field=None, pagination=None, throttles=None, search_fields=None, ordering_fields=None, filterset=None, authentication=None, if_exists="error", register=False, url_prefix=None)` | Append a ``ModelViewSet`` subclass to ``apps/<app>/views.py``. |
| `{prefix}generate_crud(app, model_name, fields, serializer_fields=None, read_only_fields=None, permission="IsAuthenticatedOrReadOnly", meta=None, extra_fields=None, validate_fields=None, read_only=False, lookup_field=None, pagination=None, throttles=None, search_fields=None, ordering_fields=None, filterset=None, authentication=None, url_prefix=None, migrate=False)` | One-shot scaffold: create model + serializer + viewset + register route — wired and served. |
| `{prefix}list_endpoints()` | Return all ``router.register(...)`` calls found across all app ``urls.py`` files. |
| `{prefix}register_route(app, model_name, url_prefix=None, if_exists="error")` | Register a ViewSet with the app's router in ``apps/<app>/urls.py``. |
| `{prefix}update_viewset(app, model_name, permission=None, lookup_field=None, pagination=None, throttles=None, search_fields=None, ordering_fields=None, authentication=None)` | Set or update class attributes on an existing ``<ModelName>ViewSet``. |

### Migrations

| Tool | Description |
|---|---|
| `{prefix}get_migration_status()` | Return the status of all migrations (applied / pending). |
| `{prefix}make_migrations(name=None)` | Detect model changes and write a new migration file. |
| `{prefix}rollback_migration(steps=1)` | Roll back the last *steps* migration(s). |
| `{prefix}run_migrations()` | Apply all pending migrations. |

### Platform Runtime

| Tool | Description |
|---|---|
| `{prefix}get_openapi_url()` | Return the live OpenAPI document URL for the project. |
| `{prefix}get_project_reference()` | Return the platform-published runtime references for the project. |

### Logs

| Tool | Description |
|---|---|
| `{prefix}clear_logs(log_file=None)` | Truncate log file(s) to zero bytes. |
| `{prefix}read_logs(lines=200, level=None, log_file=None)` | Return the last *lines* lines from the project log file. |
| `{prefix}search_logs(pattern, log_file=None)` | Search log file(s) for lines matching *pattern* (regex). |

### Config & Environment

| Tool | Description |
|---|---|
| `{prefix}delete_env(key)` | Remove an environment variable from the project ``.env`` file. |
| `{prefix}get_env()` | Read the project ``.env`` file and return it as a key-value dict. |
| `{prefix}get_settings()` | Return the parsed project settings as a dictionary. |
| `{prefix}manage_settings(key, value=None, read_only=False)` | Read or update a top-level setting in ``settings.py``. |
| `{prefix}set_env(key, value)` | Set (or create) an environment variable in the project ``.env`` file. |

### File System

| Tool | Description |
|---|---|
| `{prefix}list_files(directory=".", pattern="*")` | List files in a project directory, optionally filtered by a glob pattern. |
| `{prefix}read_file(path)` | Read a file from the project and return its contents. |
| `{prefix}search_code(pattern, glob="**/*.py")` | Search for a regex pattern across project source files. |
| `{prefix}write_file(path, content)` | Write (or overwrite) a file in the project. |

### Database Introspection

| Tool | Description |
|---|---|
| `{prefix}describe_table(table_name)` | Return column names and types for a database table. |
| `{prefix}list_tables()` | Return the names of all tables in the project database. |
| `{prefix}run_query(sql)` | Execute a read-only SQL query against the project database. |

### Testing

| Tool | Description |
|---|---|
| `{prefix}run_tests(path=None, verbose=False)` | Run the project test suite via pytest. |

### Shell / Management

| Tool | Description |
|---|---|
| `{prefix}run_management_command(command, args=None)` | Run a ``manage.py`` management command and return its output. |

### Seed Data

| Tool | Description |
|---|---|
| `{prefix}generate_seed_script(app, models=None, count=5, output_path=None)` | Generate a Python seed script that populates the database with sample data. |

### ORM Signals

| Tool | Description |
|---|---|
| `{prefix}create_signal_receiver(app, signal_name, model_name, function_name)` | Create an async signal receiver stub in ``{app}/signals.py``. |
| `{prefix}delete_signal_receiver(app, function_name)` | Remove a receiver function and its ``@receiver(...)`` decorator. |
| `{prefix}edit_signal_receiver(app, function_name, new_body)` | Replace the body of an existing receiver function. |
| `{prefix}list_model_signals(app, model_name)` | Return all receivers registered for a specific model. |
| `{prefix}list_signal_receivers(app)` | List all ``@receiver(...)`` decorated functions in ``{app}/signals.py``. |
| `{prefix}read_signal_receiver(app, function_name)` | Return the full source (decorator + function body) of a receiver. |
| `{prefix}update_signal_receiver(app, function_name, new_body)` | Replace a receiver's body — alias of :func:`edit_signal_receiver`. |

### Users (BaaS)

| Tool | Description |
|---|---|
| `{prefix}create_user(email, password, is_staff=False, is_superuser=False)` | Create a new user in the project's user table. |
| `{prefix}delete_user(email_or_id)` | Delete a user by email or primary-key ID. |
| `{prefix}get_user(email_or_id)` | Fetch a single user by email or primary-key ID. |
| `{prefix}list_users(limit=50, offset=0)` | Return a list of users from the project's user table. |
| `{prefix}set_user_password(email_or_id, new_password)` | Set a new password for an existing user (hashes it automatically). |
| `{prefix}update_user(email_or_id, changes)` | Update fields on an existing user. |

### CORS (BaaS)

| Tool | Description |
|---|---|
| `{prefix}configure_cors(origins, methods=None, allow_credentials=True, allow_headers=None, expose_headers=None)` | Write CORS settings to the project's ``settings.py`` — middleware included. |
| `{prefix}get_cors_config()` | Read the current CORS settings from the project. |

### Background Tasks (BaaS)

| Tool | Description |
|---|---|
| `{prefix}create_task(app, function_name, schedule=None)` | Scaffold an async task function in ``apps/{app}/tasks.py``. |
| `{prefix}delete_task(app, function_name)` | Remove an async task function from ``apps/{app}/tasks.py``. |
| `{prefix}list_tasks(app)` | Return all async task functions defined in ``apps/{app}/tasks.py``. |

### Health (BaaS)

| Tool | Description |
|---|---|
| `{prefix}check_system_health()` | Run runtime health checks against the live project. |
| `{prefix}create_health_endpoint()` | Scaffold ``/health`` and ``/ready`` route handlers. |

### Schema & Routes (BaaS)

| Tool | Description |
|---|---|
| `{prefix}export_openapi(output_path=None, port=8000)` | Fetch the OpenAPI spec from a reachable API and save a static snapshot. |
| `{prefix}get_model_json_schema(app, model_name)` | Return a JSON Schema object for a zeeb_orm model. |
| `{prefix}list_all_routes()` | Return a full inventory of API routes registered across all apps. |

### Deployment (BaaS)

| Tool | Description |
|---|---|
| `{prefix}check_production_readiness()` | Validate that the project is ready for production deployment. |
| `{prefix}generate_dockerfile(python_version="3.12", port=8000)` | Generate a production-ready ``Dockerfile`` in the project root. |
| `{prefix}generate_requirements(output_path="requirements.txt")` | Generate a ``requirements.txt`` from ``pip freeze`` output. |

### Permissions (BaaS)

| Tool | Description |
|---|---|
| `{prefix}create_permission_class(app, class_name, logic="deny_all")` | Scaffold a ``BasePermission`` subclass in ``apps/{app}/permissions.py``. |
| `{prefix}list_permission_classes(app)` | Return all ``BasePermission`` subclasses in ``apps/{app}/permissions.py``. |

### Auth Scaffolding

| Tool | Description |
|---|---|
| `{prefix}create_user_model(app, model_name="User", extra_fields=None, set_auth_user_model=True)` | Create a custom user model extending ``zeeb_api.auth.models.AbstractUser``. |
| `{prefix}setup_auth(enable_registration=True, url_prefix="/auth", access_token_minutes=None, refresh_token_days=None)` | Wire zeeb_api's JWT auth router into the project ``urls.py``. |
| `{prefix}setup_oauth(provider, client_id_env=None, client_secret_env=None, scopes=None, redirect_uri=None)` | Configure an OAuth/OIDC provider and wire the OAuth router. |

### FilterSets

| Tool | Description |
|---|---|
| `{prefix}create_filterset(app, model_name, filter_fields)` | Create a ``FilterSet`` class in ``apps/<app>/filters.py``. |

### API Configuration

| Tool | Description |
|---|---|
| `{prefix}configure_throttling(default_classes=None, rates=None)` | Configure project-wide API rate limiting in ``settings.py``. |
| `{prefix}configure_versioning(scheme="url", default_version=None, allowed_versions=None)` | Configure API versioning in ``settings.py``. |

### Discovery

| Tool | Description |
|---|---|
| `{prefix}get_started()` | Return the canonical build recipe — the one call to make first. |
| `{prefix}list_capabilities(include_docstrings=False, module=None)` | Return a machine-readable inventory of every ``zeeb_agents`` tool. |

### Docs & Resources

| Tool | Description |
|---|---|
| `{prefix}get_backend_generation_doc(tool_prefix="", framework=None)` | Return the backend code-generation guide. |
| `{prefix}get_capabilities_doc(tool_prefix="", framework=None)` | Return the full `zeeb_agents` capability reference. |
| `{prefix}get_deployment_doc(tool_prefix="", framework=None)` | Return the deployment guide. |
| `{prefix}get_error_recovery_doc(tool_prefix="", framework=None)` | Return the error-recovery playbook. |
| `{prefix}get_frontend_generation_doc(tool_prefix="", framework=None)` | Return the frontend integration guide. |
| `{prefix}get_principles_doc(tool_prefix="", framework=None)` | Return the operating-principles and special-cases guide. |
| `{prefix}get_project_lifecycle_doc(tool_prefix="", framework=None)` | Return the end-to-end project lifecycle guide. |
| `{prefix}get_recipes_doc(tool_prefix="", framework=None)` | Return the copy-paste task recipes. |
| `{prefix}get_resource(uri, tool_prefix="", framework=None)` | Fetch MCP resource content by URI. |

<!-- END AUTO-GENERATED INVENTORY -->

## MCP Resources (read via URI, not tools)

The guides below are MCP **resources**, not callable tools — read them with your
client's resource API at these URIs (all also summarised by
`{prefix}list_capabilities`):
`mcp://docs/principles` (read first), `mcp://docs/capabilities`,
`mcp://docs/project-lifecycle`, `mcp://docs/backend-generation`,
`mcp://docs/frontend-generation`, `mcp://docs/deployment`,
`mcp://docs/recipes`, `mcp://docs/error-recovery`.
