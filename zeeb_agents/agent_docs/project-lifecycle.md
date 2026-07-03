# Zeeb Project Lifecycle

End-to-end guide for creating, developing, and running a Zeeb BaaS project.

> **Tool name prefix**: Tool calls below use `{prefix}` as a placeholder.
> It is replaced with the prefix your MCP server registered these tools under
> (e.g. `zeeb_`, `myapp_`, or empty string).

## Step 1 — Create the Project

```
{prefix}create_project(name="my_api", directory=".")
```
Creates: `my_api/manage.py`, `my_api/my_api/settings.py`, `my_api/my_api/urls.py`, etc.
(the inner package is named after the project, e.g. `my_api/my_api/`).

## Step 2 — Create Apps

Each logical domain lives in its own app under `apps/`.

```
{prefix}create_app(name="users")
{prefix}create_app(name="blog")
{prefix}create_app(name="billing")
```

Register in `my_api/settings.py`:
```python
INSTALLED_APPS = [
    "apps.users",
    "apps.blog",
    "apps.billing",
]
```

## Step 3 — Define Models

```
{prefix}create_model(app="blog", model_name="Post", fields=[
    {"name": "title",      "type": "CharField",    "max_length": 200},
    {"name": "slug",       "type": "SlugField",    "unique": True},
    {"name": "body",       "type": "TextField"},
    {"name": "published",  "type": "BooleanField", "default": False},
    {"name": "created_at", "type": "DateTimeField","auto_now_add": True},
])
```

Add relationships:
```
{prefix}add_relationship(app="blog", model_name="Post", rel={
    "name": "author",
    "type": "ForeignKey",
    "to": "User",
    "on_delete": "CASCADE",
    "related_name": "posts",
})
```

## Step 4 — Run Migrations

```
{prefix}make_migrations()
{prefix}run_migrations()
```

Check status:
```
{prefix}get_migration_status()
# result.data["pending"] — list of unapplied migrations
# result.data["applied"] — list of applied migrations
```

## Step 5 — Generate API Layer

If the model does **not** exist yet, scaffold the whole stack (model +
serializer + viewset + route) in one call — `fields` is required and creates the
model for you, so use this *instead of* Step 3 for that model:

```
{prefix}generate_crud(app="blog", model_name="Post", fields=[
    {"name": "title", "type": "CharField", "max_length": 200},
    {"name": "body",  "type": "TextField"},
])
# Creates: model, serializer, viewset, registers route in urls.py — then run migrations.
```

When the model already exists (as in Step 3 above), build the API piece by
piece — note `model_name=` and `url_prefix=`:
```
{prefix}create_serializer(app="blog", model_name="Post")
{prefix}create_viewset(app="blog", model_name="Post")
{prefix}register_route(app="blog", model_name="Post", url_prefix="posts")
```

## Step 6 — Configure Auth & Permissions

```
{prefix}create_permission_class(app="blog", class_name="IsPostOwner", logic="owner_only")
{prefix}manage_settings(key="AUTH_USER_MODEL", value="auth.User")
```

## Step 7 — Create the First User

```
{prefix}create_user(email="admin@example.com", password="admin123", is_superuser=True)
```

## Step 8 — Start the Dev Server

```
{prefix}start_server(addrport="127.0.0.1:8000")
# Docs available at http://localhost:8000/docs
```

## Step 9 — Seed Sample Data

```
{prefix}generate_seed_script(app="blog", count=10)
# Writes seeds/blog_seed.py — run with: python seeds/blog_seed.py
```

## Step 10 — Iterate

```
{prefix}add_field(app="blog", model_name="Post", field={"name": "views", "type": "IntegerField", "default": 0})
{prefix}make_migrations()       # detects changes across all apps
{prefix}run_migrations()
```

## Step 11 — Add Background Tasks

```
{prefix}create_task(app="billing", function_name="send_invoices", schedule="0 9 1 * *")
{prefix}create_task(app="blog",    function_name="cleanup_drafts")
```

## Step 12 — Monitor & Debug

```
{prefix}check_system_health()       # DB ping, settings check
{prefix}read_logs(lines=50)         # recent log output
{prefix}search_logs(pattern="ERROR")  # find error lines
{prefix}run_query(sql="SELECT COUNT(*) FROM blog_post")
```

## Typical Project Layout

```
my_api/
├── manage.py
├── my_api/                 # project package (named after the project)
│   ├── settings.py
│   ├── urls.py
│   └── asgi.py
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
