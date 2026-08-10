# Zeeb Project Lifecycle

End-to-end guide for creating, developing, and running a Zeeb BaaS project.

> **Tool name prefix**: Tool calls below use `{prefix}` as a placeholder.
> It is replaced with the prefix your MCP server registered these tools under
> (e.g. `zeeb_`, `myapp_`, or empty string).

## The short path

Steps 1-6 below are the object-by-object walkthrough — worth reading once to
understand what the project is made of. In practice you do not sequence them by
hand: after `create_project`, one `{prefix}build_feature(spec=...)` call creates
the app, models, serializers, endpoints, routes, migrations and tests together
and verifies the result, and `{prefix}change_feature` evolves it from there.
`{prefix}get_started()` returns the current recipe plus the recommended next
action for this project. Use the individual tools below when a change is too
specific for a spec.

## Step 1 — Create the Project

```
{prefix}create_project(name="my_api")
```
Creates `my_api/manage.py`, `my_api/my_api/{settings,urls,asgi}.py`, `apps/`,
`migrations/`, `logs/`, `.env` and `.env.example` (the inner package is named
after the project, e.g. `my_api/my_api/`).

**The result is already a working, authenticated API** — do not rebuild any of
this:

- `apps/accounts/` holds the project's user model (`class User(AbstractUser)`,
  table `accounts_user`), registered via `AUTH_USER_MODEL = "accounts.User"`.
  Never call `{prefix}create_user_model` on a fresh project; add fields to the
  existing model instead. Reference it as `"accounts.User"`.
- The project `urls.py` already mounts the auth router: `POST /auth/register`,
  `/auth/login`, `/auth/refresh`, `/auth/logout` and `GET /auth/me`, under
  `API_PREFIX` (`/api/v1`). `{prefix}setup_auth` will report `already_wired`.
- CORS, JWT middleware, per-client rate limiting, API versioning, the `/health`
  and `/ready` probes, the standard error envelope and rotating logs are all
  configured and on.
- OAuth2/OIDC is wired but dormant; it activates when a provider client id is
  in the environment. `{prefix}setup_oauth(provider=...)` configures one.
- `.env` carries a signing key generated for this project, so the app also runs
  with `DEBUG=false`.

Everything is environment-driven: `settings.py` reads each value through
`env_str` / `env_bool` / `env_int` / `env_list`, with the literal as the default.
Change configuration with `{prefix}set_env` where a variable exists, and
`{prefix}manage_settings` for the setting itself.

Migrations still have to be created and applied — `{prefix}make_migrations`
then `{prefix}run_migrations` — before the server will start.

## Step 2 — Create Apps

Each logical domain lives in its own app under `apps/`.

```
{prefix}create_app(name="users")
{prefix}create_app(name="blog")
{prefix}create_app(name="billing")
```

`create_app` **auto-wires** each app by default: it appends `"apps.<name>"` to
`INSTALLED_APPS` (so migrations see its models) and includes the app's router in
the project `urls.py` (so its endpoints are served). No manual `settings.py` /
`urls.py` edit is needed. The result:
```python
INSTALLED_APPS = [
    "apps.users",
    "apps.blog",
    "apps.billing",
]
```
`{prefix}create_app` has **ensure-semantics**: re-running it on an app that
already exists leaves the files alone and re-applies only the wiring, and
reports which happened in `data["created"]`. That makes it the repair for an
app whose models never migrate or whose endpoints 404 — reach for it before
`{prefix}install_app` / `{prefix}wire_app_urls`.

To scaffold the files without wiring, pass `create_app(name=..., wire=False)`;
those two tools then wire it later.

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
piece. `create_viewset` writes the ViewSet; register its route with
`register_route` (the URL segment defaults to the pluralized lowercase model
name, `Post` → `/posts` — override it with `url_prefix=`):
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

## Step 8 — Preview the API

The preview runtime is always-on and platform-managed — you do not start a dev
server. After a change, fetch the live URLs and re-read the OpenAPI contract:

```
{prefix}get_project_reference(project_id="…")   # preview_url, runtime_api_base_url, openapi_url
{prefix}get_openapi_url(project_id="…")          # the live OpenAPI URL to build the frontend against
```

## Step 9 — Seed Sample Data

```
{prefix}generate_seed_script(app="blog", models=["Post"], count=10)
# Writes a runnable seed script; pass models=[...] for several models at once
# and output_path=... to choose where it is written.
```

## Step 10 — Iterate

```
{prefix}change_feature(feature="blog", changes=[
  {"operation": "add_field", "entity": "Post",
   "field": {"name": "views", "type": "int", "default": 0}}
])
```

One call: the field, the serializer, the migration, and the verification. The
per-object equivalent is three calls whose ordering you have to get right:

```
{prefix}add_field(app="blog", model_name="Post", field={"name": "views", "type": "IntegerField", "default": 0})
{prefix}make_migrations()       # detects changes across all apps
{prefix}run_migrations()
```

## Step 10b — Manage what you built

Features are things, not just build events:

```
{prefix}list_features()                              # what this project is made of
{prefix}deactivate_feature(feature="blog")           # off the API; tables untouched
{prefix}activate_feature(feature="blog")             # back, exactly as it was
{prefix}delete_feature(feature="blog", confirm=True) # gone, tables dropped
```

`deactivate_feature` is the safe one: it archives the API layer under
`.zeeb/archive/<feature>/` and leaves `models.py` and every row alone, so no
migration runs and activating restores the code verbatim. `delete_feature`
destroys data and refuses without `confirm=True`.

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
├── .env                    # generated signing key; gitignored
├── .env.example            # every supported variable
├── .gitignore
├── README.md
├── my_api/                 # project package (named after the project)
│   ├── settings.py
│   ├── urls.py
│   └── asgi.py
├── apps/
│   ├── accounts/           # scaffolded: the project's user model
│   │   ├── models.py       # class User(AbstractUser)
│   │   ├── serializers.py
│   │   ├── views.py
│   │   └── urls.py
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
├── migrations/             # migration files live flat in here
│   └── 0001_initial.py
├── logs/
├── tests/                  # written by generate_tests
├── seeds/
│   └── blog_seed.py
├── health.py
├── Dockerfile
├── pytest.ini
└── requirements.txt
```
