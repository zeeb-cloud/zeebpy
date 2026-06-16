# Zeeb Agent Principles & Special Cases

Read this first. It explains *how* the `zeeb_agents` tools behave, the
conventions every tool follows, the non-obvious special cases, and the Zeeb
framework concepts you need to drive a project correctly.

> **Tool name prefix**: Tool names below are written as `{prefix}function_name`.
> When served via MCP the `{prefix}` placeholder is replaced with the prefix
> your server registered (e.g. `zeeb_`, `myapp_`, or empty). For machine-readable
> discovery of every tool, its signature and docstring, call
> `{prefix}list_capabilities()`.

---

## 1. The `AgentResult` contract

Every tool returns the same shape — it never raises:

```
success : bool        — the ONLY source of truth for success/failure
message : str         — a one-line human-readable summary
data    : dict | None — structured output; shape documented per tool
```

Rules you can rely on:

- **Always check `success` first.** The object is truthy when `success` is
  `True` (`if result: ...`), falsy otherwise. Never infer success from the
  presence of `data`.
- **`message` is always a string**, both on success and failure. On failure it
  is formatted as `"<ErrorType>: <details>"` when an exception was caught.
- **`data` may be `None`** — especially on failure. Guard with
  `if result.success and result.data: ...` before indexing.
- For the exact keys a given tool puts in `data`, read its `Returns data:`
  docstring block (via `{prefix}list_capabilities(include_docstrings=True)`).

## 2. Functions never raise (`@agent_function`)

Every public tool is wrapped by the `@agent_function` decorator, which gives
two guarantees:

1. **No exceptions escape.** Any error inside the function is caught, logged to
   the `"zeeb_agents"` logger, and returned as
   `AgentResult(success=False, message="<ErrorType>: <details>")`. You handle
   failures by inspecting `success`, not with `try/except`.
2. **`project_root` is auto-resolved.** Every tool that touches a project takes
   a trailing `project_root` argument. **You normally omit it** — when it is
   `None`, the decorator walks up from the current working directory to find
   the nearest directory containing `manage.py` and uses that. Pass an explicit
   path only when operating on a project that is not under the CWD. If no
   `manage.py` is found, the tool returns a failure `AgentResult`.

Because `project_root` is auto-resolved, `{prefix}list_capabilities()` hides it
from the reported signatures.

## 3. Return-shape conventions

The library follows these conventions so `data` is predictable:

| Convention | Example |
|---|---|
| List/inventory tools return the **plural entity key + `count`** | `{"apps": [...], "count": 3}` |
| File/scaffold tools return paths **relative to the project root** | `{"path": "apps/blog/models.py"}` |
| Multi-step tools return a list of what happened | `generate_crud` → `{"steps": [...]}` |
| On failure, `data` carries useful context where it helps | `read_logs` failure → `{"searched_in": "..."}` |

Note that some result-list keys are **semantically named, not uniform** —
this is intentional:

- `{prefix}generate_crud` → `data["steps"]` (or `steps_completed` + `errors` on
  partial failure)
- `{prefix}run_migrations` → `data["applied"]`; `{prefix}get_migration_status`
  → `data["applied"]` and `data["pending"]`
- `{prefix}update_model` → `data["changes"]`

## 4. Security model

Two tools are deliberately sandboxed:

- **`{prefix}run_query` is read-only.** The SQL is validated before execution:
  comments are stripped, only a **single** statement is allowed, the first
  keyword must be `SELECT` / `WITH` / `EXPLAIN`, and a denylist rejects
  `INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE/REPLACE/GRANT/ATTACH/PRAGMA/VACUUM`
  anywhere in the statement. As defense-in-depth the query runs inside a
  transaction that is **always rolled back** — nothing can be committed. To
  mutate data, use the model/ORM tools or `{prefix}run_management_command`,
  not raw SQL.
- **File tools are confined to the project.** `{prefix}read_file`,
  `{prefix}write_file`, and `{prefix}list_files` resolve every path (following
  symlinks) and reject anything that escapes the project root with an error —
  `../../etc/passwd` will fail.

## 5. Special cases & gotchas

| Tool | What to watch for |
|---|---|
| `{prefix}read_logs(level=...)` | `level` matches the log level as a whole token / `[LEVEL]` tag — it will not match it as an incidental substring of another word. |
| `{prefix}create_route(path=...)` | `{name}` segments in the path (e.g. `/items/{item_id}`) are auto-extracted and added as `str` parameters to the generated handler. |
| `{prefix}get_env` | Returns `success=False` when there is **no `.env` file** (the returned `env` dict is empty). A missing file is reported as a failure, not an empty success. |
| `{prefix}make_migrations` | Returns `success=True` even when there are **no changes** to migrate (`data["created"]` is then `None`). "Nothing to do" is success. |
| `{prefix}manage_settings` | Dual-mode: pass only `key` (or `read_only=True`) to **read**; pass `key` + `value` to **write**. Both modes return `data={"key": ..., "value": ...}`. |
| `{prefix}replace_model_fields` | **Destructive** — replaces *all* fields on the model (`class Meta` is preserved). Use `{prefix}add_field` / `{prefix}remove_field` for incremental edits. |
| `{prefix}generate_crud` | Best-effort: on partial failure it returns `success=False` with `data["steps_completed"]` and `data["errors"]` so you can see how far it got. |
| `{prefix}export_openapi` | Needs the dev server running (it fetches the live spec over HTTP). Start it with `{prefix}start_server` first. |

## 6. Framework concepts a coding agent needs

Zeeb is a Django-shaped, async-first framework: an ORM (`zeeb_orm`) over
SQLAlchemy, a DRF-style API layer (`zeeb_api`) over FastAPI, and these agent
tools. A generated project looks like:

```
myproject/
├── manage.py
├── myproject/{settings.py, urls.py, asgi.py}
├── apps/<app>/{models.py, serializers.py, views.py, urls.py}
└── migrations/versions/
```

### The generation flow

The normal order to build a resource end-to-end:

1. `{prefix}create_app("blog")` — scaffold the app under `apps/blog/`.
2. `{prefix}create_model(...)` — append a `Model` to `apps/blog/models.py`.
3. `{prefix}create_serializer(...)` — a DRF-style `ModelSerializer` in
   `serializers.py`.
4. `{prefix}create_viewset(...)` — a `ModelViewSet` (CRUD) in `views.py`.
5. `{prefix}register_route(...)` — wire the ViewSet into routing (see below).
6. `{prefix}make_migrations()` then `{prefix}run_migrations()` — apply schema.

`{prefix}generate_crud(...)` does steps 2–5 in one shot; you still run the
migrations afterwards.

### How URLs get registered

Routing is two-tiered, mirroring Django:

- `{prefix}register_route(app, model_name, url_prefix=None)` appends
  `router.register("<prefix>", <Model>ViewSet)` to **`apps/<app>/urls.py`** and
  ensures the ViewSet is imported there. `url_prefix` defaults to the app name.
- The app's router is in turn included by the **project's `myproject/urls.py`**,
  which mounts each app and the auth routes. A `ModelViewSet` registered this
  way exposes the full CRUD set plus a `POST /<prefix>/query/` endpoint that
  accepts serialized `Q` filter strings.
- For a single function endpoint instead of a ViewSet, use
  `{prefix}create_route(app, path, method, function_name, ...)`, which appends a
  plain `@router.<method>(path)` handler to `views.py`.
- Inspect what is wired with `{prefix}list_endpoints()` (scans `urls.py`) or
  `{prefix}list_all_routes()` (full inventory incl. function routes).

### How auth works

`zeeb_api` ships Django-like auth that these tools manage:

- **Users**: a `User` model with hashed passwords. Manage it with
  `{prefix}create_user(email, password, is_staff=, is_superuser=)`,
  `{prefix}list_users`, `{prefix}get_user`, `{prefix}update_user`,
  `{prefix}delete_user`, and `{prefix}set_user_password` (which re-hashes).
  Never write raw password values — always go through these tools.
- **Tokens**: JWT with access/refresh tokens. The `auth_router` exposes
  `login` / `refresh` / `logout` / `me` endpoints; a middleware sets
  `request.state.user`. Secrets come from settings/env — manage them with
  `{prefix}manage_settings` / `{prefix}set_env`. In production
  (`DEBUG=false`) insecure default secrets are refused and the app fails fast.
- **Authorization**: DRF-style permission classes guard ViewSets. ViewSets
  scaffolded by `{prefix}create_viewset` default to
  `IsAuthenticatedOrReadOnly`; pass `permission=` to change it. Create custom
  classes with `{prefix}create_permission_class(app, class_name, logic=...)`
  and list them with `{prefix}list_permission_classes`.

### Settings & configuration

Project configuration lives in `myproject/settings.py` (read it with
`{prefix}get_settings`, edit single keys with `{prefix}manage_settings`) and in
the `.env` file (`{prefix}get_env` / `{prefix}set_env` / `{prefix}delete_env`).
The database URL, debug flag, CORS, and JWT secrets all flow from here.

---

See also `mcp://docs/capabilities` (full tool inventory),
`mcp://docs/project-lifecycle`, `mcp://docs/backend-generation`,
`mcp://docs/frontend-generation`, and `mcp://docs/deployment`.
