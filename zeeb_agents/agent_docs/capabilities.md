# Zeeb Agent Capabilities

`zeeb_agents` is a standalone async Python library that exposes all Zeeb
scaffolding and management operations as importable async functions.  It has
no MCP dependency — plug it into any MCP server, CLI, or AI agent.

> **New here?** Read `mcp://docs/principles` first — it covers the
> `AgentResult` contract, return-shape conventions, the security model, the
> documented special cases, and the framework concepts (auth, URL
> registration, the generation flow). Call `{prefix}list_capabilities()` for a
> machine-readable inventory of every tool with its signature and docstring.

> **Signatures are authoritative via `{prefix}list_capabilities`.** The
> argument lists in the tables below are hand-maintained for quick reference;
> when a call is rejected for an unexpected argument, call
> `{prefix}list_capabilities(include_docstrings=True)` (optionally
> `module="viewsets"` etc.) to get the exact, always-in-sync signature and
> `Returns data:` shape generated from the installed code.

> **Tool name prefix**: In this document every tool name is written as
> `{prefix}function_name`.  When served via MCP the `{prefix}` placeholder is
> replaced with the prefix your MCP server uses (e.g. `zeeb_`, `myapp_`, or
> an empty string for no prefix).  All tool names below therefore resolve to
> the exact names your server registered.

## Return value

Every tool returns an `AgentResult`:

```
success : bool        — True on success, False on error
message : str         — human-readable summary
data    : dict|None   — structured output (see per-tool docs)
```

## Tool Inventory

### Project & App Management

| Tool | Description |
|---|---|
| `{prefix}create_project(name, framework="zeebpy")` | Scaffold a new managed project |
| `{prefix}create_app(name, project_id="")` | Create a new app inside `apps/` |
| `{prefix}delete_app(name, project_id="")` | Remove an app directory |
| `{prefix}rename_app(old_name, new_name, project_id="")` | Rename an app |
| `{prefix}list_apps(project_id="")` | List all registered apps |
| `{prefix}get_project_structure(project_id="")` | Directory tree |

### Model Management

| Tool | Description |
|---|---|
| `{prefix}create_model(app, model, fields, meta=None)` | Add a new ORM model |
| `{prefix}update_model(app, name, fields=None, table_name=None, ordering=None, timestamps=True)` | Update an existing model's fields / Meta |
| `{prefix}delete_model(app, model)` | Remove a model class |
| `{prefix}list_models()` | List models across all apps |
| `{prefix}add_field(app, model, field)` | Add a field (`field` is a spec dict) |
| `{prefix}remove_field(app, model, field_name)` | Remove a field |
| `{prefix}add_relationship(app, model, rel)` | Add FK/O2O/M2M (`rel` is a spec dict) |

### Serializers

| Tool | Description |
|---|---|
| `{prefix}create_serializer(app, model, fields=None, read_only_fields=None, extra_fields=None, validate_fields=None)` | Generate serializer (declared fields, nested, method fields, validate stubs) |
| `{prefix}update_serializer(app, model, fields=None, read_only_fields=None)` | Update serializer fields |

### ViewSets & Routing

| Tool | Description |
|---|---|
| `{prefix}create_viewset(app, model, name=None, serializer=None, permissions=None, read_only=False, lookup_field=None, prefix=None, pagination=None, throttles=None, search_fields=None, ordering_fields=None, filterset=None)` | Generate ModelViewSet and register its route |
| `{prefix}update_viewset(app, model, permission=None, lookup_field=None, pagination=None, throttles=None, search_fields=None, ordering_fields=None)` | Set/update attrs on an existing ViewSet |
| `{prefix}create_action(app, model, action_name, detail=True, methods=None, body=None)` | Add custom action (pass impl via `body=`) |
| `{prefix}generate_crud(app, model, fields, serializer_fields=None, read_only_fields=None, permission="IsAuthenticatedOrReadOnly", meta=None, extra_fields=None, validate_fields=None, read_only=False, lookup_field=None, pagination=None, throttles=None, search_fields=None, ordering_fields=None, filterset=None)` | Full CRUD scaffold (`fields` required) |
| `{prefix}list_endpoints()` | List all registered ViewSet routes |
| `{prefix}create_route(app, path, method, function_name, response_model=None, body=None, imports=None)` | Standalone handler, auto-wired (pass impl via `body=`) |

### Auth & API Configuration

| Tool | Description |
|---|---|
| `{prefix}setup_auth(enable_registration=True, url_prefix="/auth", access_token_minutes=None, refresh_token_days=None)` | Wire JWT auth router (login/refresh/logout/me/register) |
| `{prefix}setup_oauth(provider, client_id_env=None, client_secret_env=None, scopes=None, redirect_uri=None)` | Configure OAuth provider (azure/github/google) + router |
| `{prefix}create_user_model(app, model_name="User", extra_fields=None, set_auth_user_model=True)` | Custom user model extending AbstractUser |
| `{prefix}create_filterset(app, model, filter_fields)` | FilterSet class for query-param filtering |
| `{prefix}configure_throttling(default_classes=None, rates=None)` | Project-wide rate limiting defaults |
| `{prefix}configure_versioning(scheme="url", default_version=None, allowed_versions=None)` | API versioning (url/header/query/accept) |

### Migrations

| Tool | Description |
|---|---|
| `{prefix}create_migration(name=None, project_id="")` | Detect and write migrations (`name` = optional filename suffix) |
| `{prefix}run_migrations(project_id="")` | Apply pending migrations |
| `{prefix}get_migration_status(project_id="")` | Show applied/pending state |
| `{prefix}rollback_migration(steps=1, project_id="")` | Roll back the last `steps` migration(s) |

### Dev Server

| Tool | Description |
|---|---|

### Logs

| Tool | Description |
|---|---|
| `{prefix}read_logs(lines=200, level=None, log_file=None, project_id="")` | Read recent log lines |
| `{prefix}search_logs(pattern, log_file=None, project_id="")` | Search log content for `pattern` |

### Config & Environment

| Tool | Description |
|---|---|
| `{prefix}get_settings(project_id="")` | Read all settings |
| `{prefix}manage_settings(key, value=None, action="read", app=None, project_id="")` | Read (`action="read"`) or write a scalar setting |
| `{prefix}get_env(project_id="")` | Read all .env variables |
| `{prefix}set_env(key, value, project_id="")` | Write .env variable |
| `{prefix}delete_env(key, project_id="")` | Remove .env variable |

### File System

| Tool | Description |
|---|---|
| `{prefix}read_file(path, project_id="")` | Read a project file |
| `{prefix}write_file(path, content, project_id="")` | Write a project file |
| `{prefix}list_files(directory=".", pattern="*", project_id="")` | Glob-match files under `directory` |
| `{prefix}search_code(pattern, glob="**/*.py", project_id="")` | Grep source files (`pattern` = regex) |

### Database Introspection

| Tool | Description |
|---|---|
| `{prefix}list_tables(project_id="")` | List DB tables |
| `{prefix}describe_table(table_name, project_id="")` | Column details |
| `{prefix}run_query(sql, project_id="")` | Execute read-only SQL |

### Testing

| Tool | Description |
|---|---|
| `{prefix}run_tests(path=None, verbose=False, project_id="")` | Run pytest suite |

### Shell / Management

| Tool | Description |
|---|---|
| `{prefix}run_management_command(command, args=None, project_id="")` | Run manage.py command |

### Seed Data

| Tool | Description |
|---|---|
| `{prefix}seed_data(app, model, count=5, field_defaults=None, project_id="")` | Insert sample rows for a model (call once per model) |

### ORM Signals Scaffolding

| Tool | Description |
|---|---|
| `{prefix}create_signal_receiver(app, signal_name, model_name, function_name, project_id="")` | Add signal handler |
| `{prefix}list_signal_receivers(app, project_id="")` | List signal handlers |
| `{prefix}edit_signal_receiver(app, function_name, new_body, project_id="")` | Update handler |
| `{prefix}delete_signal_receiver(app, function_name, project_id="")` | Remove handler |
| `{prefix}list_signal_receivers(app, model, project_id="")` | Signals on a model |

### BaaS — User Management

| Tool | Description |
|---|---|
| `{prefix}create_user(email, password, is_staff=False, is_superuser=False, project_id="")` | Create user |
| `{prefix}list_users(limit=50, offset=0, project_id="")` | List users |
| `{prefix}get_user(email_or_id, project_id="")` | Fetch single user |
| `{prefix}update_user(email_or_id, changes, project_id="")` | Update user fields |
| `{prefix}delete_user(email_or_id, project_id="")` | Delete user |
| `{prefix}set_user_password(email_or_id, new_password, project_id="")` | Change password |

### BaaS — CORS

| Tool | Description |
|---|---|
| `{prefix}configure_cors(origins, methods=None, allow_credentials=True, allow_headers=None, expose_headers=None, project_id="")` | Set CORS settings |
| `{prefix}get_cors_config(project_id="")` | Read CORS settings |

### BaaS — Background Tasks

| Tool | Description |
|---|---|
| `{prefix}create_task(app, function_name, schedule=None, project_id="")` | Scaffold task stub |
| `{prefix}list_tasks(app, project_id="")` | List task functions |
| `{prefix}delete_task(app, function_name, project_id="")` | Remove task |

### BaaS — Health

| Tool | Description |
|---|---|
| `{prefix}create_health_endpoint(project_id="")` | Scaffold /health + /ready |
| `{prefix}check_system_health(project_id="")` | Runtime health check |

### BaaS — Schema & Routes

| Tool | Description |
|---|---|
| `{prefix}get_model_json_schema(app, model, project_id="")` | JSON Schema for model |
| `{prefix}list_all_routes(project_id="")` | All routes across all apps |
| `{prefix}export_openapi(output_path=None, port=8000, project_id="")` | Save openapi.json |

### BaaS — Deployment

| Tool | Description |
|---|---|
| `{prefix}generate_dockerfile(python_version="3.12", port=8000, project_id="")` | Write Dockerfile |
| `{prefix}generate_requirements(output_path="requirements.txt", project_id="")` | Write requirements.txt |
| `{prefix}check_production_readiness(project_id="")` | Validate prod config |

### BaaS — Permissions Scaffolding

| Tool | Description |
|---|---|
| `{prefix}create_permission_class(app, class_name, logic="deny_all", project_id="")` | Scaffold permission |
| `{prefix}list_permission_classes(app, project_id="")` | List permission classes |

### Discovery

| Tool | Description |
|---|---|
| `{prefix}list_capabilities(include_docstrings=False, module=None)` | Machine-readable inventory of every tool, signature & docstring |

### MCP Resources (read via URI, not tools)

The guides below are MCP **resources**, not callable tools — read them with your
client's resource API at these URIs (all also summarised by `{prefix}list_capabilities`):
`mcp://docs/principles` (read first), `mcp://docs/capabilities`,
`mcp://docs/project-lifecycle`, `mcp://docs/backend-generation`,
`mcp://docs/frontend-generation`, `mcp://docs/deployment`.
