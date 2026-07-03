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
| `{prefix}create_project(name, directory=".")` | Scaffold a new Zeeb project |
| `{prefix}create_app(name, project_root=None)` | Create a new app inside `apps/` |
| `{prefix}delete_app(name, project_root=None)` | Remove an app directory |
| `{prefix}rename_app(old_name, new_name, project_root=None)` | Rename an app |
| `{prefix}get_project_info(project_root=None)` | Return project metadata |
| `{prefix}list_apps(project_root=None)` | List all registered apps |
| `{prefix}get_project_structure(max_depth=3, project_root=None)` | Directory tree |

### Model Management

| Tool | Description |
|---|---|
| `{prefix}create_model(app, model_name, fields, meta=None)` | Add a new ORM model |
| `{prefix}update_model(app, model_name, rename_to=None, meta_changes=None)` | Rename / update model meta |
| `{prefix}replace_model_fields(app, model_name, fields)` | Replace all fields (destructive) |
| `{prefix}delete_model(app, model_name)` | Remove a model class |
| `{prefix}list_models()` | List models across all apps |
| `{prefix}add_field(app, model_name, field)` | Add a field (`field` is a spec dict) |
| `{prefix}remove_field(app, model_name, field_name)` | Remove a field |
| `{prefix}add_relationship(app, model_name, rel)` | Add FK/O2O/M2M (`rel` is a spec dict) |

### Serializers

| Tool | Description |
|---|---|
| `{prefix}create_serializer(app, model_name, fields=None, read_only_fields=None, extra_fields=None, validate_fields=None)` | Generate serializer (declared fields, nested, method fields, validate stubs) |
| `{prefix}update_serializer(app, model_name, fields=None, read_only_fields=None)` | Update serializer fields |

### ViewSets & Routing

| Tool | Description |
|---|---|
| `{prefix}create_viewset(app, model_name, serializer_class=None, permission="IsAuthenticatedOrReadOnly", read_only=False, lookup_field=None, pagination=None, throttles=None, search_fields=None, ordering_fields=None, filterset=None)` | Generate ModelViewSet (pagination, throttling, search, ordering, FilterSet, read-only) |
| `{prefix}update_viewset(app, model_name, permission=None, lookup_field=None, pagination=None, throttles=None, search_fields=None, ordering_fields=None)` | Set/update attrs on an existing ViewSet |
| `{prefix}add_viewset_action(app, model_name, action_name, detail=True, methods=None, body=None)` | Add custom action (pass impl via `body=`) |
| `{prefix}register_route(app, model_name, url_prefix=None)` | Register ViewSet in urls.py |
| `{prefix}generate_crud(app, model_name, fields, serializer_fields=None, read_only_fields=None, permission="IsAuthenticatedOrReadOnly", meta=None, extra_fields=None, validate_fields=None, read_only=False, lookup_field=None, pagination=None, throttles=None, search_fields=None, ordering_fields=None, filterset=None)` | Full CRUD scaffold (`fields` required) |
| `{prefix}list_endpoints()` | List all registered ViewSet routes |
| `{prefix}create_route(app, path, method, function_name, response_model=None, body=None, imports=None)` | Standalone handler, auto-wired (pass impl via `body=`) |

### Auth & API Configuration

| Tool | Description |
|---|---|
| `{prefix}setup_auth(enable_registration=True, url_prefix="/auth", access_token_minutes=None, refresh_token_days=None)` | Wire JWT auth router (login/refresh/logout/me/register) |
| `{prefix}setup_oauth(provider, client_id_env=None, client_secret_env=None, scopes=None, redirect_uri=None)` | Configure OAuth provider (azure/github/google) + router |
| `{prefix}create_user_model(app, model_name="User", extra_fields=None, set_auth_user_model=True)` | Custom user model extending AbstractUser |
| `{prefix}create_filterset(app, model_name, filter_fields)` | FilterSet class for query-param filtering |
| `{prefix}configure_throttling(default_classes=None, rates=None)` | Project-wide rate limiting defaults |
| `{prefix}configure_versioning(scheme="url", default_version=None, allowed_versions=None)` | API versioning (url/header/query/accept) |

### Migrations

| Tool | Description |
|---|---|
| `{prefix}make_migrations(name=None, project_root=None)` | Detect and write migrations (`name` = optional filename suffix) |
| `{prefix}run_migrations(project_root=None)` | Apply pending migrations |
| `{prefix}get_migration_status(project_root=None)` | Show applied/pending state |
| `{prefix}rollback_migration(steps=1, project_root=None)` | Roll back the last `steps` migration(s) |

### Dev Server

| Tool | Description |
|---|---|
| `{prefix}start_server(addrport="127.0.0.1:9000", project_root=None)` | Start development server (host:port) |
| `{prefix}stop_server(project_root=None)` | Stop development server |
| `{prefix}get_server_status(project_root=None)` | Check server running state |

### Logs

| Tool | Description |
|---|---|
| `{prefix}read_logs(lines=200, level=None, log_file=None, project_root=None)` | Read recent log lines |
| `{prefix}search_logs(pattern, log_file=None, project_root=None)` | Search log content for `pattern` |
| `{prefix}clear_logs(log_file=None, project_root=None)` | Clear log files |

### Config & Environment

| Tool | Description |
|---|---|
| `{prefix}get_settings(project_root=None)` | Read all settings |
| `{prefix}manage_settings(key, value=None, read_only=False, project_root=None)` | Read/write scalar setting |
| `{prefix}get_env(project_root=None)` | Read all .env variables |
| `{prefix}set_env(key, value, project_root=None)` | Write .env variable |
| `{prefix}delete_env(key, project_root=None)` | Remove .env variable |

### File System

| Tool | Description |
|---|---|
| `{prefix}read_file(path, project_root=None)` | Read a project file |
| `{prefix}write_file(path, content, project_root=None)` | Write a project file |
| `{prefix}list_files(directory=".", pattern="*", project_root=None)` | Glob-match files under `directory` |
| `{prefix}search_code(pattern, glob="**/*.py", project_root=None)` | Grep source files (`pattern` = regex) |

### Database Introspection

| Tool | Description |
|---|---|
| `{prefix}list_tables(project_root=None)` | List DB tables |
| `{prefix}describe_table(table_name, project_root=None)` | Column details |
| `{prefix}run_query(sql, project_root=None)` | Execute read-only SQL |

### Testing

| Tool | Description |
|---|---|
| `{prefix}run_tests(path=None, verbose=False, project_root=None)` | Run pytest suite |

### Shell / Management

| Tool | Description |
|---|---|
| `{prefix}run_management_command(command, args=None, project_root=None)` | Run manage.py command |

### Seed Data

| Tool | Description |
|---|---|
| `{prefix}generate_seed_script(app, models=None, count=5, output_path=None, project_root=None)` | Write seed script |

### ORM Signals Scaffolding

| Tool | Description |
|---|---|
| `{prefix}create_signal_receiver(app, signal_name, model_name, function_name, project_root=None)` | Add signal handler |
| `{prefix}list_signal_receivers(app, project_root=None)` | List signal handlers |
| `{prefix}read_signal_receiver(app, function_name, project_root=None)` | Read handler source |
| `{prefix}edit_signal_receiver(app, function_name, new_body, project_root=None)` | Update handler |
| `{prefix}delete_signal_receiver(app, function_name, project_root=None)` | Remove handler |
| `{prefix}list_model_signals(app, model_name, project_root=None)` | Signals on a model |

### BaaS — User Management

| Tool | Description |
|---|---|
| `{prefix}create_user(email, password, is_staff=False, is_superuser=False, project_root=None)` | Create user |
| `{prefix}list_users(limit=50, offset=0, project_root=None)` | List users |
| `{prefix}get_user(email_or_id, project_root=None)` | Fetch single user |
| `{prefix}update_user(email_or_id, changes, project_root=None)` | Update user fields |
| `{prefix}delete_user(email_or_id, project_root=None)` | Delete user |
| `{prefix}set_user_password(email_or_id, new_password, project_root=None)` | Change password |

### BaaS — CORS

| Tool | Description |
|---|---|
| `{prefix}configure_cors(origins, methods=None, allow_credentials=True, allow_headers=None, expose_headers=None, project_root=None)` | Set CORS settings |
| `{prefix}get_cors_config(project_root=None)` | Read CORS settings |

### BaaS — Background Tasks

| Tool | Description |
|---|---|
| `{prefix}create_task(app, function_name, schedule=None, project_root=None)` | Scaffold task stub |
| `{prefix}list_tasks(app, project_root=None)` | List task functions |
| `{prefix}delete_task(app, function_name, project_root=None)` | Remove task |

### BaaS — Health

| Tool | Description |
|---|---|
| `{prefix}create_health_endpoint(project_root=None)` | Scaffold /health + /ready |
| `{prefix}check_system_health(project_root=None)` | Runtime health check |

### BaaS — Schema & Routes

| Tool | Description |
|---|---|
| `{prefix}get_model_json_schema(app, model_name, project_root=None)` | JSON Schema for model |
| `{prefix}list_all_routes(project_root=None)` | All routes across all apps |
| `{prefix}export_openapi(output_path=None, port=8000, project_root=None)` | Save openapi.json |

### BaaS — Deployment

| Tool | Description |
|---|---|
| `{prefix}generate_dockerfile(python_version="3.12", port=8000, project_root=None)` | Write Dockerfile |
| `{prefix}generate_requirements(output_path="requirements.txt", project_root=None)` | Write requirements.txt |
| `{prefix}check_production_readiness(project_root=None)` | Validate prod config |

### BaaS — Permissions Scaffolding

| Tool | Description |
|---|---|
| `{prefix}create_permission_class(app, class_name, logic="deny_all", project_root=None)` | Scaffold permission |
| `{prefix}list_permission_classes(app, project_root=None)` | List permission classes |

### Discovery

| Tool | Description |
|---|---|
| `{prefix}list_capabilities(include_docstrings=False, module=None)` | Machine-readable inventory of every tool, signature & docstring |

### MCP Resources

| Tool | Description |
|---|---|
| `{prefix}get_resource(uri, project_root=None, tool_prefix="")` | Dispatch by `mcp://` URI |
| `{prefix}get_principles_doc(project_root=None, tool_prefix="")` | Principles & special cases (read first) |
| `{prefix}get_capabilities_doc(project_root=None, tool_prefix="")` | This document |
| `{prefix}get_project_lifecycle_doc(project_root=None, tool_prefix="")` | Project lifecycle guide |
| `{prefix}get_backend_generation_doc(project_root=None, tool_prefix="")` | Backend generation guide |
| `{prefix}get_frontend_generation_doc(project_root=None, tool_prefix="")` | Frontend integration guide |
| `{prefix}get_deployment_doc(project_root=None, tool_prefix="")` | Deployment guide |
