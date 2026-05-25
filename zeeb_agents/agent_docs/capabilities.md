# Zeeb Agent Capabilities

`zeeb_agents` is a standalone async Python library that exposes all Zeeb
scaffolding and management operations as importable async functions.  It has
no MCP dependency — plug it into any MCP server, CLI, or AI agent.

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
| `{prefix}create_app(app_name, project_root=None)` | Create a new app inside `apps/` |
| `{prefix}delete_app(app_name, project_root=None)` | Remove an app directory |
| `{prefix}rename_app(old_name, new_name, project_root=None)` | Rename an app |
| `{prefix}get_project_info(project_root=None)` | Return project metadata |
| `{prefix}list_apps(project_root=None)` | List all registered apps |
| `{prefix}get_project_structure(max_depth=3, project_root=None)` | Directory tree |

### Model Management

| Tool | Description |
|---|---|
| `{prefix}create_model(app, name, fields, project_root=None)` | Add a new ORM model |
| `{prefix}update_model(app, name, meta=None, project_root=None)` | Update model meta |
| `{prefix}replace_model_fields(app, name, fields, project_root=None)` | Replace all fields |
| `{prefix}delete_model(app, name, project_root=None)` | Remove a model class |
| `{prefix}list_models(app, project_root=None)` | List models in an app |
| `{prefix}add_field(app, model, field_def, project_root=None)` | Add a field |
| `{prefix}remove_field(app, model, field_name, project_root=None)` | Remove a field |
| `{prefix}add_relationship(app, model, rel_def, project_root=None)` | Add FK/M2M |

### Serializers

| Tool | Description |
|---|---|
| `{prefix}create_serializer(app, model, fields=None, project_root=None)` | Generate serializer |
| `{prefix}update_serializer(app, model, fields, project_root=None)` | Update serializer fields |

### ViewSets & Routing

| Tool | Description |
|---|---|
| `{prefix}create_viewset(app, model, project_root=None)` | Generate ModelViewSet |
| `{prefix}add_viewset_action(app, viewset, action_def, project_root=None)` | Add custom action |
| `{prefix}register_route(app, prefix, viewset, project_root=None)` | Register in urls.py |
| `{prefix}generate_crud(app, model, fields=None, project_root=None)` | Full CRUD scaffold |
| `{prefix}list_endpoints(project_root=None)` | List all registered ViewSet routes |
| `{prefix}create_route(app, path, method, function_name, project_root=None)` | Standalone handler |

### Migrations

| Tool | Description |
|---|---|
| `{prefix}make_migrations(app=None, project_root=None)` | Detect and write migrations |
| `{prefix}run_migrations(project_root=None)` | Apply pending migrations |
| `{prefix}get_migration_status(project_root=None)` | Show applied/pending state |
| `{prefix}rollback_migration(app, migration_name, project_root=None)` | Rollback to target |

### Dev Server

| Tool | Description |
|---|---|
| `{prefix}start_server(port=8000, project_root=None)` | Start development server |
| `{prefix}stop_server(project_root=None)` | Stop development server |
| `{prefix}get_server_status(project_root=None)` | Check server running state |

### Logs

| Tool | Description |
|---|---|
| `{prefix}read_logs(lines=100, level=None, project_root=None)` | Read recent log lines |
| `{prefix}search_logs(query, project_root=None)` | Search log content |
| `{prefix}clear_logs(project_root=None)` | Clear log files |

### Config & Environment

| Tool | Description |
|---|---|
| `{prefix}get_settings(project_root=None)` | Read all settings |
| `{prefix}manage_settings(key, value=None, project_root=None)` | Read/write scalar setting |
| `{prefix}get_env(key=None, project_root=None)` | Read .env variable(s) |
| `{prefix}set_env(key, value, project_root=None)` | Write .env variable |
| `{prefix}delete_env(key, project_root=None)` | Remove .env variable |

### File System

| Tool | Description |
|---|---|
| `{prefix}read_file(path, project_root=None)` | Read a project file |
| `{prefix}write_file(path, content, project_root=None)` | Write a project file |
| `{prefix}list_files(pattern="**/*", project_root=None)` | Glob-match files |
| `{prefix}search_code(query, pattern=None, project_root=None)` | Grep source files |

### Database Introspection

| Tool | Description |
|---|---|
| `{prefix}list_tables(project_root=None)` | List DB tables |
| `{prefix}describe_table(table_name, project_root=None)` | Column details |
| `{prefix}run_query(sql, project_root=None)` | Execute read-only SQL |

### Testing

| Tool | Description |
|---|---|
| `{prefix}run_tests(app=None, project_root=None)` | Run pytest suite |

### Shell / Management

| Tool | Description |
|---|---|
| `{prefix}run_management_command(command, args=None, project_root=None)` | Run manage.py command |

### Seed Data

| Tool | Description |
|---|---|
| `{prefix}generate_seed_script(app, models=None, count=5, project_root=None)` | Write seed script |

### ORM Signals Scaffolding

| Tool | Description |
|---|---|
| `{prefix}create_signal_receiver(app, signal, model, project_root=None)` | Add signal handler |
| `{prefix}list_signal_receivers(app, project_root=None)` | List signal handlers |
| `{prefix}read_signal_receiver(app, name, project_root=None)` | Read handler source |
| `{prefix}edit_signal_receiver(app, name, new_body, project_root=None)` | Update handler |
| `{prefix}delete_signal_receiver(app, name, project_root=None)` | Remove handler |
| `{prefix}list_model_signals(app, model, project_root=None)` | Signals on a model |

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
| `{prefix}configure_cors(origins, methods=None, allow_credentials=True, project_root=None)` | Set CORS settings |
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

### MCP Resources

| Tool | Description |
|---|---|
| `{prefix}get_resource(uri, project_root=None)` | Dispatch by `mcp://` URI |
| `{prefix}get_capabilities_doc(project_root=None)` | This document |
| `{prefix}get_project_lifecycle_doc(project_root=None)` | Project lifecycle guide |
| `{prefix}get_backend_generation_doc(project_root=None)` | Backend generation guide |
| `{prefix}get_frontend_generation_doc(project_root=None)` | Frontend integration guide |
| `{prefix}get_deployment_doc(project_root=None)` | Deployment guide |
