"""MCP resource content functions for zeeb_agents documentation.

Provides callable functions that return rich markdown documentation
suitable for serving as MCP resource content.  Each function corresponds
to one MCP resource URI:

    mcp://docs/capabilities         → get_capabilities_doc()
    mcp://docs/project-lifecycle    → get_project_lifecycle_doc()
    mcp://docs/backend-generation   → get_backend_generation_doc()
    mcp://docs/frontend-generation  → get_frontend_generation_doc()
    mcp://docs/deployment           → get_deployment_doc()

All functions return an :class:`~zeeb_agents._utils.AgentResult` with:

- ``data["content"]`` — markdown string ready to serve as resource body
- ``data["uri"]``     — the canonical ``mcp://`` URI
- ``data["mime_type"]`` — always ``"text/markdown"``

When *project_root* is supplied, live project context (apps, migration
status, readiness check) is appended to the static documentation.

Usage from an MCP server::

    from zeeb_agents import get_resource, get_capabilities_doc

    # Dispatch by URI
    result = await get_resource("mcp://docs/capabilities")
    print(result.data["content"])

    # Or call directly
    result = await get_capabilities_doc()
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from zeeb_agents._utils import AgentResult

# ---------------------------------------------------------------------------
# URI registry
# ---------------------------------------------------------------------------

RESOURCE_URIS = {
    "capabilities": "mcp://docs/capabilities",
    "project-lifecycle": "mcp://docs/project-lifecycle",
    "backend-generation": "mcp://docs/backend-generation",
    "frontend-generation": "mcp://docs/frontend-generation",
    "deployment": "mcp://docs/deployment",
}

_MIME = "text/markdown"

# ---------------------------------------------------------------------------
# Static documentation blocks
# ---------------------------------------------------------------------------

_CAPABILITIES_DOC = """\
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
"""

_PROJECT_LIFECYCLE_DOC = """\
# Zeeb Project Lifecycle

End-to-end guide for creating, developing, and running a Zeeb BaaS project
using `zeeb_agents` functions.

## Step 1 — Create the Project

```python
from zeeb_agents import create_project

result = await create_project("my_api")
# Creates: my_api/manage.py, my_api/config/settings.py, my_api/config/urls.py, etc.
```

## Step 2 — Create Apps

Each logical domain lives in its own app under `apps/`.

```python
from zeeb_agents import create_app

await create_app("users")
await create_app("blog")
await create_app("billing")
```

Register in `config/settings.py`:
```python
INSTALLED_APPS = [
    "apps.users",
    "apps.blog",
    "apps.billing",
]
```

## Step 3 — Define Models

```python
from zeeb_agents import create_model

await create_model("blog", "Post", [
    {"name": "title",      "type": "CharField",   "max_length": 200},
    {"name": "slug",       "type": "SlugField",   "unique": True},
    {"name": "body",       "type": "TextField"},
    {"name": "published",  "type": "BooleanField","default": False},
    {"name": "created_at", "type": "DateTimeField","auto_now_add": True},
])
```

Add relationships:
```python
from zeeb_agents import add_relationship

await add_relationship("blog", "Post", {
    "name": "author",
    "type": "ForeignKey",
    "to": "User",
    "on_delete": "CASCADE",
    "related_name": "posts",
})
```

## Step 4 — Run Migrations

```python
from zeeb_agents import make_migrations, run_migrations

await make_migrations()    # detect model changes → write migration files
await run_migrations()     # apply all pending migrations to DB
```

Check status:
```python
from zeeb_agents import get_migration_status

result = await get_migration_status()
# result.data["pending"] — list of unapplied migrations
# result.data["applied"] — list of applied migrations
```

## Step 5 — Generate API Layer

Create the full CRUD stack for a model in one call:

```python
from zeeb_agents import generate_crud

await generate_crud("blog", "Post")
# Creates: serializer, viewset, registers route in urls.py
```

Or build piece by piece:
```python
from zeeb_agents import create_serializer, create_viewset, register_route

await create_serializer("blog", "Post")
await create_viewset("blog", "Post")
await register_route("blog", "posts", "PostViewSet")
```

## Step 6 — Configure Auth & Permissions

```python
from zeeb_agents import create_permission_class, manage_settings

# Scaffold permission
await create_permission_class("blog", "IsPostOwner", logic="owner_only")

# Use default JWT auth (already built into zeeb_api)
await manage_settings("AUTH_USER_MODEL", "auth.User")
```

## Step 7 — Create the First User

```python
from zeeb_agents import create_user

await create_user("admin@example.com", "admin123", is_superuser=True)
```

## Step 8 — Start the Dev Server

```python
from zeeb_agents import start_server

await start_server(port=8000)
# Docs available at http://localhost:8000/docs
```

## Step 9 — Seed Sample Data

```python
from zeeb_agents import generate_seed_script

await generate_seed_script("blog", count=10)
# Writes seeds/blog_seed.py — run with: python seeds/blog_seed.py
```

## Step 10 — Iterate

```python
from zeeb_agents import add_field, make_migrations, run_migrations

# Add new field
await add_field("blog", "Post", {"name": "views", "type": "IntegerField", "default": 0})

# Apply change
await make_migrations("blog")
await run_migrations()
```

## Step 11 — Add Background Tasks

```python
from zeeb_agents import create_task

await create_task("billing", "send_invoices", schedule="0 9 1 * *")
await create_task("blog",    "cleanup_drafts")
```

## Step 12 — Monitor & Debug

```python
from zeeb_agents import read_logs, search_logs, check_system_health

result = await check_system_health()        # DB ping, settings check
result = await read_logs(lines=50)          # recent log output
result = await search_logs("ERROR")         # find error lines
result = await run_query("SELECT COUNT(*) FROM blog_post")
```

## Typical Project Layout

```
my_api/
├── manage.py
├── config/
│   ├── settings.py
│   └── urls.py
├── apps/
│   ├── blog/
│   │   ├── models.py
│   │   ├── serializers.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   ├── signals.py
│   │   ├── tasks.py
│   │   └── permissions.py
│   └── users/
│       ├── models.py
│       └── ...
├── seeds/
│   └── blog_seed.py
├── health.py
├── Dockerfile
└── requirements.txt
```
"""

_BACKEND_GENERATION_DOC = """\
# Backend Generation Guide

How to generate and iterate on all backend components using `zeeb_agents`.

## Models

### Field Types Reference

| zeeb_orm Field | JSON Schema type | Notes |
|---|---|---|
| `CharField(max_length=N)` | `string` | Required: `max_length` |
| `TextField` | `string` | Unlimited text |
| `EmailField` | `string/email` | Validates email format |
| `URLField` | `string/uri` | Validates URL format |
| `SlugField` | `string` | URL-friendly string |
| `UUIDField` | `string/uuid` | Auto-generated by default |
| `IntegerField` | `integer` | |
| `FloatField` | `number` | |
| `DecimalField(max_digits, decimal_places)` | `string/decimal` | |
| `BooleanField` | `boolean` | |
| `DateTimeField(auto_now_add, auto_now)` | `string/date-time` | |
| `DateField` | `string/date` | |
| `JSONField` | `object` | Arbitrary JSON |
| `ForeignKey(to, on_delete, related_name)` | `integer` (FK id) | |
| `OneToOneField(to, on_delete)` | `integer` | |
| `ManyToManyField(to)` | `array[integer]` | |

Add `null=True` for optional fields.

### Creating Models

```python
from zeeb_agents import create_model

await create_model("shop", "Product", [
    {"name": "name",        "type": "CharField",  "max_length": 200},
    {"name": "price",       "type": "DecimalField","max_digits": 10, "decimal_places": 2},
    {"name": "stock",       "type": "IntegerField","default": 0},
    {"name": "description", "type": "TextField",  "null": True},
    {"name": "created_at",  "type": "DateTimeField","auto_now_add": True},
])
```

### Inspecting Models

```python
from zeeb_agents import list_models, get_model_json_schema

result = await list_models("shop")
# result.data["models"] == ["Product", "Category", "Order"]

schema = await get_model_json_schema("shop", "Product")
# schema.data["schema"] — JSON Schema dict usable for validation / SDK gen
```

## Migrations

```python
from zeeb_agents import make_migrations, run_migrations, get_migration_status, rollback_migration

await make_migrations()            # auto-detect all app changes
await make_migrations("shop")      # single-app only

await run_migrations()

status = await get_migration_status()
# status.data["pending"] — list of unapplied migration names

await rollback_migration("shop", "0002_add_stock_field")
```

## Serializers

Zeeb serializers are Pydantic-like classes that validate and serialize data.

```python
from zeeb_agents import create_serializer, update_serializer

# Generate serializer from model fields (auto-detects field types)
await create_serializer("shop", "Product")

# Override which fields to expose
await create_serializer("shop", "Product", fields=["id", "name", "price"])

# Add a field later
await update_serializer("shop", "Product", fields=["id", "name", "price", "stock"])
```

## ViewSets

```python
from zeeb_agents import create_viewset, add_viewset_action

await create_viewset("shop", "Product")

# Custom action: POST /products/{id}/restock/
await add_viewset_action("shop", "ProductViewSet", {
    "name": "restock",
    "method": "post",
    "detail": True,
    "url_path": "restock",
})
```

## Standalone Routes

For non-CRUD endpoints (webhooks, computed endpoints, etc.):

```python
from zeeb_agents import create_route

await create_route("shop", "/products/featured",   "get",  "get_featured_products")
await create_route("shop", "/checkout",             "post", "checkout",
                   response_model="CheckoutResponse")
```

## Full CRUD in One Call

```python
from zeeb_agents import generate_crud

# Creates model serializer + viewset + registers route
await generate_crud("shop", "Product")
await generate_crud("shop", "Category")
await generate_crud("shop", "Order")
```

## Signals (Model Lifecycle Hooks)

```python
from zeeb_agents import create_signal_receiver, list_model_signals

# Attach a post_save hook to Product
await create_signal_receiver("shop", "post_save", "Product")
# Writes a receiver stub to apps/shop/signals.py

# See which signals are connected to a model
result = await list_model_signals("shop", "Product")
```

Signals fire automatically in `Model.save()` / `Model.delete()`:
- `pre_save` / `post_save` — before/after save
- `pre_delete` / `post_delete` — before/after delete

## Permissions

```python
from zeeb_agents import create_permission_class, list_permission_classes

await create_permission_class("shop", "IsProductOwner", logic="owner_only")
await create_permission_class("shop", "IsStaff",        logic="staff_only")
await create_permission_class("shop", "PublicRead",     logic="authenticated")

result = await list_permission_classes("shop")
# result.data["permissions"] == ["IsProductOwner", "IsStaff", "PublicRead"]
```

## Background Tasks

```python
from zeeb_agents import create_task, list_tasks

await create_task("shop", "send_order_confirmation", schedule=None)
await create_task("shop", "sync_inventory",          schedule="0 * * * *")
await create_task("billing", "generate_invoices",    schedule="0 9 1 * *")

result = await list_tasks("shop")
# result.data["tasks"] == ["send_order_confirmation", "sync_inventory"]
```

## Database Operations

```python
from zeeb_agents import list_tables, describe_table, run_query

await list_tables()                    # all tables
await describe_table("shop_product")   # columns + types

# Read-only queries only
await run_query("SELECT id, name, price FROM shop_product ORDER BY price DESC LIMIT 10")
```

## Seed Data

```python
from zeeb_agents import generate_seed_script

await generate_seed_script("shop", models=["Product", "Category"], count=20)
# Writes seeds/shop_seed.py — run independently:
# python seeds/shop_seed.py
```
"""

_FRONTEND_GENERATION_DOC = """\
# Frontend Integration Guide

How a frontend application (SPA, mobile, or 3rd-party client) integrates
with a Zeeb BaaS and how to generate the artefacts it needs.

## 1 — Configure CORS

The very first step when a frontend on a different origin calls the API:

```python
from zeeb_agents import configure_cors

await configure_cors(
    origins=["https://myapp.vercel.app", "http://localhost:5173"],
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_credentials=True,
)
```

Then register the middleware in `settings.py`:
```python
MIDDLEWARE = [
    "zeeb_api.middleware.CORSMiddleware",
    # ... other middleware
]
```

Read current CORS settings:
```python
from zeeb_agents import get_cors_config
result = await get_cors_config()
# result.data["cors"]["CORS_ALLOW_ORIGINS"] == [...]
```

## 2 — Authentication

Zeeb API ships with JWT authentication out of the box.

**Obtain tokens** — `POST /auth/token/`:
```json
{ "email": "user@example.com", "password": "secret" }
```
Response:
```json
{ "access": "<jwt_access_token>", "refresh": "<jwt_refresh_token>" }
```

**Use the token**:
```http
Authorization: Bearer <jwt_access_token>
```

**Refresh** — `POST /auth/token/refresh/`:
```json
{ "refresh": "<jwt_refresh_token>" }
```

The auth URLs are registered automatically when `zeeb_api.auth` is in `INSTALLED_APPS`.

### Create Initial Users

```python
from zeeb_agents import create_user

await create_user("admin@example.com", "admin123", is_superuser=True)
await create_user("user@example.com",  "user123")
```

## 3 — OpenAPI / Swagger Documentation

The live server exposes `/docs` (Swagger UI) and `/openapi.json`
automatically.  To save the spec for client SDK generation:

```python
from zeeb_agents import export_openapi

# Dev server must be running (zeeb runserver)
await export_openapi(port=8000, output_path="openapi.json")
```

The saved `openapi.json` can be used with:
- **openapi-generator** to generate typed clients (TypeScript, Swift, Dart…)
- **Speakeasy**, **Stainless**, or **fern** for SDK generation
- **Postman** / **Insomnia** for API testing collections

## 4 — Data Shapes (JSON Schema)

Get the exact shape of any model for frontend form generation or validation:

```python
from zeeb_agents import get_model_json_schema

result = await get_model_json_schema("blog", "Post")
schema = result.data["schema"]
# {
#   "title": "Post",
#   "type": "object",
#   "properties": {
#     "id":         {"type": "integer", "readOnly": true},
#     "title":      {"type": "string", "maxLength": 200},
#     "slug":       {"type": "string"},
#     "body":       {"type": "string"},
#     "published":  {"type": "boolean"},
#     "created_at": {"type": "string", "format": "date-time"}
#   },
#   "required": ["title", "slug", "body"]
# }
```

Use this schema directly with:
- **Zod** (TypeScript): `z.object({...})`
- **Yup** (React Hook Form)
- **Ajv** for runtime JSON validation
- **react-jsonschema-form** for auto-generated forms

## 5 — Route Inventory

Discover all available endpoints without a running server:

```python
from zeeb_agents import list_all_routes

result = await list_all_routes()
# result.data["routes"] == [
#   {"app": "blog", "type": "viewset", "prefix": "posts", "viewset": "PostViewSet"},
#   {"app": "blog", "type": "route",   "method": "GET",   "path": "/posts/featured"},
#   {"app": "users","type": "viewset", "prefix": "users", "viewset": "UserViewSet"},
# ]
```

Standard ViewSet `{prefix}` expands to these REST endpoints:
| Method | Path | Action |
|---|---|---|
| GET    | `/{prefix}/`     | list |
| POST   | `/{prefix}/`     | create |
| GET    | `/{prefix}/{id}/`| retrieve |
| PUT    | `/{prefix}/{id}/`| update |
| PATCH  | `/{prefix}/{id}/`| partial_update |
| DELETE | `/{prefix}/{id}/`| destroy |

## 6 — Health / Status Endpoints

Add health probes that frontend monitoring dashboards or load balancers use:

```python
from zeeb_agents import create_health_endpoint

await create_health_endpoint()
# Creates health.py with GET /health and GET /ready
```

Register the router in your main app and then:
```
GET /health  →  {"status": "ok"}                       # 200 always
GET /ready   →  {"status": "ready", "db": "ok"}        # 200 / 503
```

## 7 — Real-Time Considerations

Zeeb does not yet ship a built-in WebSocket layer.  For real-time features:

- Use **polling** against REST endpoints (simplest)
- Add **Broadcaster** + **starlette-websockets** manually for pub/sub
- Use an external managed service (Pusher, Ably, Supabase Realtime)

The `create_route` function can scaffold a WebSocket endpoint stub:
```python
from zeeb_agents import create_route

await create_route("chat", "/ws/messages", "websocket", "ws_messages_handler")
```

## 8 — Environment Configuration

Set frontend-relevant settings via env:

```python
from zeeb_agents import set_env, get_env

await set_env("FRONTEND_URL", "https://myapp.vercel.app")
await set_env("JWT_ACCESS_TOKEN_LIFETIME_MINUTES", "60")
await set_env("JWT_REFRESH_TOKEN_LIFETIME_DAYS", "7")
```
"""

_DEPLOYMENT_DOC = """\
# Deployment Guide

How to prepare and deploy a Zeeb BaaS project to production using
`zeeb_agents`.

## Step 1 — Production Readiness Check

Run this first to identify all issues before deploying:

```python
from zeeb_agents import check_production_readiness

result = await check_production_readiness()
# result.success == True  → ready to deploy
# result.data["issues"] — list of problems to fix
# result.data["passed"] — list of passing checks
```

Checks performed:
| Check | Requirement |
|---|---|
| `DEBUG` | Must be `False` |
| `SECRET_KEY` | Must be set and not a placeholder |
| Database | Must not be SQLite |
| `requirements.txt` | Must exist |
| `Dockerfile` | Must exist |

## Step 2 — Configure Production Settings

```python
from zeeb_agents import manage_settings, set_env

# In settings.py
await manage_settings("DEBUG", False)

# In .env (keep secrets out of settings.py)
await set_env("SECRET_KEY", "your-strong-random-secret-key-here")
await set_env("DATABASE_URL", "postgresql+asyncpg://user:pass@host/dbname")
await set_env("ALLOWED_HOSTS", "myapi.example.com")
```

Example production `settings.py`:
```python
import os

DEBUG = False
SECRET_KEY = os.environ["SECRET_KEY"]
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",")

DATABASE = {
    "url": os.environ["DATABASE_URL"],
    "pool_size": 20,
    "max_overflow": 10,
}

INSTALLED_APPS = [
    "zeeb_api.auth",
    "apps.blog",
    "apps.shop",
]

MIDDLEWARE = [
    "zeeb_api.middleware.CORSMiddleware",
]

CORS_ALLOW_ORIGINS = os.environ.get("CORS_ORIGINS", "").split(",")
CORS_ALLOW_CREDENTIALS = True
```

## Step 3 — Generate Dockerfile

```python
from zeeb_agents import generate_dockerfile

await generate_dockerfile(python_version="3.12", port=8000)
# Writes Dockerfile (multi-stage) and .dockerignore
```

Generated `Dockerfile` structure:
```dockerfile
FROM python:3.12-slim AS builder
# install dependencies into builder layer

FROM python:3.12-slim AS runner
# copy only site-packages from builder (lean image)
EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
```

For a production-grade CMD, replace with **Gunicorn + Uvicorn**:
```dockerfile
CMD ["gunicorn", "config.asgi:application", "-k", "uvicorn.workers.UvicornWorker",
     "-b", "0.0.0.0:8000", "--workers", "4"]
```

## Step 4 — Generate requirements.txt

```python
from zeeb_agents import generate_requirements

await generate_requirements()
# Runs pip freeze, filters editable installs, writes requirements.txt
```

## Step 5 — Add Health Endpoints

```python
from zeeb_agents import create_health_endpoint

await create_health_endpoint()
# GET /health  → liveness probe  (always 200)
# GET /ready   → readiness probe (200 / 503 based on DB)
```

Configure in Kubernetes / Docker Compose:
```yaml
# docker-compose.yml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
  interval: 30s
  timeout: 10s
  retries: 3
```

```yaml
# k8s deployment.yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8000
  initialDelaySeconds: 10
readinessProbe:
  httpGet:
    path: /ready
    port: 8000
  initialDelaySeconds: 5
```

## Step 6 — Apply Migrations in CI/CD

In your deployment pipeline (before starting the container):
```bash
python manage.py migrate --run-syncdb
```

Or via agent:
```python
from zeeb_agents import run_migrations

await run_migrations()
```

## Step 7 — Docker Build & Push

```bash
docker build -t myapi:latest .
docker push registry.example.com/myapi:latest
```

## Step 8 — Runtime Health Check

After deployment, verify with:
```python
from zeeb_agents import check_system_health

result = await check_system_health()
# result.data["checks"]["db"]      == "ok"
# result.data["checks"]["settings"] == "ok"
# result.data["checks"]["overall"]  == "healthy"
```

## Recommended Stack

| Layer | Technology |
|---|---|
| ASGI server | Uvicorn / Gunicorn+Uvicorn |
| Database | PostgreSQL 15+ |
| Container | Docker |
| Orchestration | Kubernetes or Fly.io |
| Reverse proxy | Nginx or Caddy |
| Secrets | HashiCorp Vault / AWS Secrets Manager / .env |
| Monitoring | Prometheus + Grafana |
| Logging | stdout → Loki / CloudWatch |

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | ✅ | Django-style secret key |
| `DATABASE_URL` | ✅ | Async DB URL (e.g. `postgresql+asyncpg://...`) |
| `DEBUG` | — | Default `False` in prod |
| `ALLOWED_HOSTS` | — | Comma-separated hostnames |
| `CORS_ORIGINS` | — | Comma-separated allowed origins |
| `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` | — | Default 60 |
| `JWT_REFRESH_TOKEN_LIFETIME_DAYS` | — | Default 7 |
"""


# ---------------------------------------------------------------------------
# Dynamic project context helpers
# ---------------------------------------------------------------------------

async def _build_project_context(project_root: Path) -> str:
    """Return a markdown section with live project state."""
    from zeeb_agents.project import list_apps as _list_apps, get_project_info
    from zeeb_agents.migrations import get_migration_status

    lines: list[str] = ["\n---\n\n## Current Project Context\n"]

    try:
        info = await get_project_info(project_root)
        if info.success and info.data:
            lines.append(f"**Project root:** `{info.data.get('root', project_root)}`\n\n")
    except Exception:
        pass

    try:
        apps_result = await _list_apps(project_root)
        if apps_result.success and apps_result.data:
            apps = apps_result.data.get("apps", [])
            if apps:
                lines.append(f"**Apps ({len(apps)}):** " + ", ".join(f"`{a}`" for a in apps) + "\n\n")
    except Exception:
        pass

    try:
        mig = await get_migration_status(project_root)
        if mig.success and mig.data:
            pending = mig.data.get("pending", [])
            applied = mig.data.get("applied", [])
            lines.append(f"**Migrations:** {len(applied)} applied, {len(pending)} pending")
            if pending:
                lines.append(" ⚠️ — run `await run_migrations()` to apply")
            lines.append("\n\n")
    except Exception:
        pass

    return "".join(lines) if len(lines) > 1 else ""


async def _build_deployment_context(project_root: Path) -> str:
    """Return readiness check as markdown."""
    from zeeb_agents.deploy import check_production_readiness

    lines: list[str] = ["\n---\n\n## Current Readiness Check\n"]
    try:
        result = await check_production_readiness(project_root)
        if result.data:
            for issue in result.data.get("issues", []):
                lines.append(f"- ❌ {issue}\n")
            for passed in result.data.get("passed", []):
                lines.append(f"- ✅ {passed}\n")
    except Exception:
        lines.append("_Could not run readiness check._\n")

    return "".join(lines) if len(lines) > 1 else ""


# ---------------------------------------------------------------------------
# Public resource functions
# ---------------------------------------------------------------------------

async def get_capabilities_doc(
    project_root: Path | None = None,
) -> AgentResult:
    """Return the full `zeeb_agents` capability reference.

    Covers all 80+ public functions organised by module, the ``AgentResult``
    contract, and import examples.

    Maps to MCP resource ``mcp://docs/capabilities``.

    Args:
        project_root: When provided, a live project context section is appended.
    """
    content = _CAPABILITIES_DOC
    if project_root is not None:
        content += await _build_project_context(project_root)
    return AgentResult(
        success=True,
        message="Capabilities documentation ready.",
        data={"content": content, "uri": RESOURCE_URIS["capabilities"], "mime_type": _MIME},
    )


async def get_project_lifecycle_doc(
    project_root: Path | None = None,
) -> AgentResult:
    """Return the end-to-end project lifecycle guide.

    Covers every step from `create_project` through development, iteration,
    and monitoring.

    Maps to MCP resource ``mcp://docs/project-lifecycle``.

    Args:
        project_root: When provided, current app list and migration status
            are appended.
    """
    content = _PROJECT_LIFECYCLE_DOC
    if project_root is not None:
        content += await _build_project_context(project_root)
    return AgentResult(
        success=True,
        message="Project lifecycle documentation ready.",
        data={"content": content, "uri": RESOURCE_URIS["project-lifecycle"], "mime_type": _MIME},
    )


async def get_backend_generation_doc(
    project_root: Path | None = None,
) -> AgentResult:
    """Return the backend code-generation guide.

    Covers models, migrations, serializers, viewsets, routes, permissions,
    signals, tasks, and seed data.

    Maps to MCP resource ``mcp://docs/backend-generation``.

    Args:
        project_root: When provided, current app list is appended.
    """
    content = _BACKEND_GENERATION_DOC
    if project_root is not None:
        content += await _build_project_context(project_root)
    return AgentResult(
        success=True,
        message="Backend generation documentation ready.",
        data={"content": content, "uri": RESOURCE_URIS["backend-generation"], "mime_type": _MIME},
    )


async def get_frontend_generation_doc(
    project_root: Path | None = None,
) -> AgentResult:
    """Return the frontend integration guide.

    Covers CORS, JWT auth, OpenAPI export, JSON Schema, route inventory,
    health endpoints, and WebSocket notes.

    Maps to MCP resource ``mcp://docs/frontend-generation``.

    Args:
        project_root: When provided, current CORS settings and route
            inventory are appended.
    """
    content = _FRONTEND_GENERATION_DOC
    if project_root is not None:
        extra_lines: list[str] = ["\n---\n\n## Current Project State\n"]
        try:
            from zeeb_agents.cors import get_cors_config
            cors = await get_cors_config(project_root)
            if cors.success and cors.data and cors.data.get("cors"):
                origins = cors.data["cors"].get("CORS_ALLOW_ORIGINS", [])
                extra_lines.append(f"**CORS origins configured:** {origins}\n\n")
            else:
                extra_lines.append("**CORS:** not yet configured — call `configure_cors()`.\n\n")
        except Exception:
            pass
        try:
            from zeeb_agents.schema import list_all_routes
            routes = await list_all_routes(project_root)
            if routes.success and routes.data:
                count = routes.data.get("count", 0)
                extra_lines.append(f"**Routes registered:** {count}\n\n")
        except Exception:
            pass
        content += "".join(extra_lines) if len(extra_lines) > 1 else ""
    return AgentResult(
        success=True,
        message="Frontend integration documentation ready.",
        data={"content": content, "uri": RESOURCE_URIS["frontend-generation"], "mime_type": _MIME},
    )


async def get_deployment_doc(
    project_root: Path | None = None,
) -> AgentResult:
    """Return the deployment guide.

    Covers production settings, Dockerfile generation, requirements.txt,
    health endpoints, environment variables, and recommended stack.

    Maps to MCP resource ``mcp://docs/deployment``.

    Args:
        project_root: When provided, the current production readiness check
            result is appended.
    """
    content = _DEPLOYMENT_DOC
    if project_root is not None:
        content += await _build_deployment_context(project_root)
    return AgentResult(
        success=True,
        message="Deployment documentation ready.",
        data={"content": content, "uri": RESOURCE_URIS["deployment"], "mime_type": _MIME},
    )


# ---------------------------------------------------------------------------
# URI dispatcher
# ---------------------------------------------------------------------------

_URI_MAP = {
    RESOURCE_URIS["capabilities"]:       get_capabilities_doc,
    RESOURCE_URIS["project-lifecycle"]:  get_project_lifecycle_doc,
    RESOURCE_URIS["backend-generation"]: get_backend_generation_doc,
    RESOURCE_URIS["frontend-generation"]: get_frontend_generation_doc,
    RESOURCE_URIS["deployment"]:          get_deployment_doc,
}


async def get_resource(
    uri: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Fetch MCP resource content by URI.

    Dispatches to the appropriate documentation function based on *uri*.

    Supported URIs:

    - ``mcp://docs/capabilities``
    - ``mcp://docs/project-lifecycle``
    - ``mcp://docs/backend-generation``
    - ``mcp://docs/frontend-generation``
    - ``mcp://docs/deployment``

    Args:
        uri: The ``mcp://`` resource URI.
        project_root: Passed through to the underlying documentation function.
            When supplied, live project context is appended to the static docs.

    Returns:
        ``AgentResult`` with ``data["content"]`` (markdown),
        ``data["uri"]``, and ``data["mime_type"]``.

    Example::

        result = await get_resource("mcp://docs/deployment", project_root=Path("."))
        print(result.data["content"])
    """
    fn = _URI_MAP.get(uri)
    if fn is None:
        return AgentResult(
            success=False,
            message=(
                f"Unknown resource URI '{uri}'. "
                f"Available: {', '.join(sorted(_URI_MAP))}."
            ),
        )
    return await fn(project_root)
