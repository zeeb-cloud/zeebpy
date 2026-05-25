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
