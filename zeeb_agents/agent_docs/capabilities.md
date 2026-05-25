# Zeeb Agent Capabilities

`zeeb_agents` is a standalone async Python library that wraps all Zeeb
scaffolding and management operations as importable functions.  It has no
MCP dependency — plug it into any MCP server, CLI, or AI agent.

## Import

```python
from zeeb_agents import <function_name>
result = await <function_name>(...)
# result.success : bool
# result.message : str          — human-readable summary
# result.data    : dict | None  — structured output
```

## Function Inventory (80 functions)

### Project & App Management
| Function | Description |
|---|---|
| `create_project(name, directory=".")` | Scaffold a new Zeeb project |
| `create_app(app_name, project_root=None)` | Create a new app inside `apps/` |
| `delete_app(app_name, project_root=None)` | Remove an app directory |
| `rename_app(old_name, new_name, project_root=None)` | Rename an app |
| `get_project_info(project_root=None)` | Return project metadata |
| `list_apps(project_root=None)` | List all registered apps |
| `get_project_structure(max_depth=3, project_root=None)` | Directory tree |

### Model Management
| Function | Description |
|---|---|
| `create_model(app, name, fields, project_root=None)` | Add a new ORM model |
| `update_model(app, name, meta=None, project_root=None)` | Update model meta |
| `replace_model_fields(app, name, fields, project_root=None)` | Replace all fields |
| `delete_model(app, name, project_root=None)` | Remove a model class |
| `list_models(app, project_root=None)` | List models in an app |
| `add_field(app, model, field_def, project_root=None)` | Add a field |
| `remove_field(app, model, field_name, project_root=None)` | Remove a field |
| `add_relationship(app, model, rel_def, project_root=None)` | Add FK/M2M |

### Serializers
| Function | Description |
|---|---|
| `create_serializer(app, model, fields=None, project_root=None)` | Generate serializer |
| `update_serializer(app, model, fields, project_root=None)` | Update serializer fields |

### ViewSets & Routing
| Function | Description |
|---|---|
| `create_viewset(app, model, project_root=None)` | Generate ModelViewSet |
| `add_viewset_action(app, viewset, action_def, project_root=None)` | Add custom action |
| `register_route(app, prefix, viewset, project_root=None)` | Register in urls.py |
| `generate_crud(app, model, fields=None, project_root=None)` | Full CRUD scaffold |
| `list_endpoints(project_root=None)` | List all registered ViewSet routes |
| `create_route(app, path, method, function_name, project_root=None)` | Standalone handler |

### Migrations
| Function | Description |
|---|---|
| `make_migrations(app=None, project_root=None)` | Detect and write migrations |
| `run_migrations(project_root=None)` | Apply pending migrations |
| `get_migration_status(project_root=None)` | Show applied/pending state |
| `rollback_migration(app, migration_name, project_root=None)` | Rollback to target |

### Dev Server
| Function | Description |
|---|---|
| `start_server(port=8000, project_root=None)` | Start development server |
| `stop_server(project_root=None)` | Stop development server |
| `get_server_status(project_root=None)` | Check server running state |

### Logs
| Function | Description |
|---|---|
| `read_logs(lines=100, level=None, project_root=None)` | Read recent log lines |
| `search_logs(query, project_root=None)` | Search log content |
| `clear_logs(project_root=None)` | Clear log files |

### Config & Environment
| Function | Description |
|---|---|
| `get_settings(project_root=None)` | Read all settings |
| `manage_settings(key, value=None, project_root=None)` | Read/write scalar setting |
| `get_env(key=None, project_root=None)` | Read .env variable(s) |
| `set_env(key, value, project_root=None)` | Write .env variable |
| `delete_env(key, project_root=None)` | Remove .env variable |

### File System
| Function | Description |
|---|---|
| `read_file(path, project_root=None)` | Read a project file |
| `write_file(path, content, project_root=None)` | Write a project file |
| `list_files(pattern="**/*", project_root=None)` | Glob-match files |
| `search_code(query, pattern=None, project_root=None)` | Grep source files |

### Database Introspection
| Function | Description |
|---|---|
| `list_tables(project_root=None)` | List DB tables |
| `describe_table(table_name, project_root=None)` | Column details |
| `run_query(sql, project_root=None)` | Execute read-only SQL |

### Testing
| Function | Description |
|---|---|
| `run_tests(app=None, project_root=None)` | Run pytest suite |

### Shell / Management
| Function | Description |
|---|---|
| `run_management_command(command, args=None, project_root=None)` | Run manage.py command |

### Seed Data
| Function | Description |
|---|---|
| `generate_seed_script(app, models=None, count=5, project_root=None)` | Write seed script |

### ORM Signals Scaffolding
| Function | Description |
|---|---|
| `create_signal_receiver(app, signal, model, project_root=None)` | Add signal handler |
| `list_signal_receivers(app, project_root=None)` | List signal handlers |
| `read_signal_receiver(app, name, project_root=None)` | Read handler source |
| `edit_signal_receiver(app, name, new_body, project_root=None)` | Update handler |
| `delete_signal_receiver(app, name, project_root=None)` | Remove handler |
| `list_model_signals(app, model, project_root=None)` | Signals on a model |

### BaaS — User Management
| Function | Description |
|---|---|
| `create_user(email, password, is_staff=False, is_superuser=False, project_root=None)` | Create user |
| `list_users(limit=50, offset=0, project_root=None)` | List users |
| `get_user(email_or_id, project_root=None)` | Fetch single user |
| `update_user(email_or_id, changes, project_root=None)` | Update user fields |
| `delete_user(email_or_id, project_root=None)` | Delete user |
| `set_user_password(email_or_id, new_password, project_root=None)` | Change password |

### BaaS — CORS
| Function | Description |
|---|---|
| `configure_cors(origins, methods=None, allow_credentials=True, project_root=None)` | Set CORS settings |
| `get_cors_config(project_root=None)` | Read CORS settings |

### BaaS — Background Tasks
| Function | Description |
|---|---|
| `create_task(app, function_name, schedule=None, project_root=None)` | Scaffold task stub |
| `list_tasks(app, project_root=None)` | List task functions |
| `delete_task(app, function_name, project_root=None)` | Remove task |

### BaaS — Health
| Function | Description |
|---|---|
| `create_health_endpoint(project_root=None)` | Scaffold /health + /ready |
| `check_system_health(project_root=None)` | Runtime health check |

### BaaS — Schema & Routes
| Function | Description |
|---|---|
| `get_model_json_schema(app, model_name, project_root=None)` | JSON Schema for model |
| `list_all_routes(project_root=None)` | All routes across all apps |
| `export_openapi(output_path=None, port=8000, project_root=None)` | Save openapi.json |

### BaaS — Deployment
| Function | Description |
|---|---|
| `generate_dockerfile(python_version="3.12", port=8000, project_root=None)` | Write Dockerfile |
| `generate_requirements(output_path="requirements.txt", project_root=None)` | Write requirements.txt |
| `check_production_readiness(project_root=None)` | Validate prod config |

### BaaS — Permissions Scaffolding
| Function | Description |
|---|---|
| `create_permission_class(app, class_name, logic="deny_all", project_root=None)` | Scaffold permission |
| `list_permission_classes(app, project_root=None)` | List permission classes |

### MCP Resources
| Function | Description |
|---|---|
| `get_resource(uri, project_root=None)` | Dispatch by `mcp://` URI |
| `get_capabilities_doc(project_root=None)` | This document |
| `get_project_lifecycle_doc(project_root=None)` | Project lifecycle guide |
| `get_backend_generation_doc(project_root=None)` | Backend generation guide |
| `get_frontend_generation_doc(project_root=None)` | Frontend integration guide |
| `get_deployment_doc(project_root=None)` | Deployment guide |
