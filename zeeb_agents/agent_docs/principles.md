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
- **`message` is always a string**, both on success and failure. *Expected*
  failures (bad input, missing app/model, invalid SQL, …) carry a plain,
  actionable message; only *unexpected* exceptions keep the
  `"<ErrorType>: <details>"` format.
- **`data` may be `None`** — especially on failure. Guard with
  `if result.success and result.data: ...` before indexing.
- **Failure `data` may carry machine-readable context.** Expected failures
  populate `data["error_code"]` (closed vocabulary, listed below) and — when
  the input was close to something valid — `data["suggestions"]`
  (a list of close-match candidates, also echoed as a "Did you mean: …?" in
  the message). Extra context keys (`apps`, `models`, `tables`, `columns`,
  `port`, …) appear where they help; treat any of them as optional.
- For the exact keys a given tool puts in `data`, read its `Returns data:`
  docstring block (via `{prefix}list_capabilities(include_docstrings=True)`).

### Failure `error_code` vocabulary

`data["error_code"]` (when present) is one of:

`already_exists`, `app_not_found`, `dependency_missing`, `env_key_not_found`,
`field_not_found`, `file_not_found`, `function_not_found`,
`invalid_field_spec`, `invalid_field_type`, `invalid_identifier`,
`invalid_input`, `invalid_meta`, `invalid_permission`, `invalid_regex`,
`invalid_sql`, `log_file_not_found`, `model_not_found`, `no_project_root`,
`no_user_table`, `outside_project_root`, `permission_denied`,
`server_not_reachable`, `server_not_running`, `setting_not_found`,
`table_not_found`, `user_not_found`.

### Two error layers — don't confuse them

- **Tool errors** (this layer): a tool call that fails returns
  `AgentResult(success=False, ...)` — it never raises and there is no HTTP
  involved.
- **API errors** (the generated project's HTTP layer): the running Zeeb API
  returns a standardized JSON envelope
  `{"success": false, "error": {"code", "message", "details", "meta"}}` with
  machine-readable codes (`AUTH_*`, `PERM_*`, `FIELD_*`, `RESOURCE_*`,
  `QUERY_*`, `RATE_LIMIT_*`, `SERVER_*`, …). Code you generate *into* the
  project (e.g. `body=` of `{prefix}create_route` /
  `{prefix}create_action`) should raise `zeeb_api.exceptions` classes so
  responses stay in that envelope — see `mcp://docs/backend-generation` for
  raising them and `mcp://docs/frontend-generation` for handling them
  client-side.

## 2. Functions never raise (`@agent_function`)

Every public tool is wrapped by the `@agent_function` decorator, which gives
two guarantees:

1. **No exceptions escape.** Any error inside the function is caught, logged to
   the `"zeeb_agents"` logger, and returned as a failure `AgentResult`.
   Expected failures return plain actionable messages with
   `data["error_code"]`; unexpected exceptions keep the
   `"<ErrorType>: <details>"` message format. You handle failures by
   inspecting `success`, not with `try/except`.
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
| Expected failures carry `error_code` (+ optional `suggestions`) in `data` | `create_model` bad type → `{"error_code": "invalid_field_type", "suggestions": ["CharField"]}` |
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
| `{prefix}create_route(path=..., body=..., imports=...)` | Writes a FastAPI `@router.<method>` handler to `views.py` on a `router = APIRouter()` (FastAPI's `APIRouter` — `zeeb_api` exposes **no** `Router`) **and** auto-includes it into `urls.py` so it is served. Pass the implementation in `body=` and any imports it needs in `imports=` — don't `write_file` the wrapper. `{name}` segments in the path (e.g. `/items/{item_id}`) are auto-extracted as typed `str` handler params; the handler always gets a typed `request: Request`. |
| `{prefix}create_action(viewset=..., name=...)` | Scaffolds a routed `@action` method on an existing ViewSet (named by `viewset=`, e.g. `PostViewSet`). `detail=True` operates on one object (`/posts/{id}/<name>/`), `detail=False` on the collection; the action `name` doubles as the URL segment. |
| `{prefix}get_env` | Returns `success=False` when there is **no `.env` file** (the returned `env` dict is empty). A missing file is reported as a failure, not an empty success. |
| `{prefix}create_migration` | Returns `success=True` even when there are **no changes** to migrate (`data["created"]` is then `None`). "Nothing to do" is success. |
| `{prefix}manage_settings` | Dual-mode: pass only `key` (or `read_only=True`) to **read**; pass `key` + `value` to **write**. Both modes return `data={"key": ..., "value": ...}`. |
| `{prefix}add_field` / `{prefix}remove_field` | Incremental model edits — add or drop a single field in place, leaving the rest of the model (and `class Meta`) untouched. |
| `{prefix}generate_crud` | Best-effort: on partial failure it returns `success=False` with `data["steps_completed"]` and `data["errors"]` so you can see how far it got. |
| `{prefix}export_openapi` | Writes a static copy of the OpenAPI spec into the project tree. For the live contract prefer `{prefix}get_openapi_url` / `{prefix}get_project_reference` — the preview runtime is always-on and platform-managed, so you do not (and cannot) start a dev server yourself. |
| Field specs — relations | `ForeignKey` / `OneToOneField` / `ManyToManyField` specs **require `"to"`** (the target model name); `on_delete="SET_NULL"` requires `null=True`; M2M rejects `on_delete`/`null` but accepts `through`/`through_fields`. Invalid specs are rejected *before* anything is written. |
| Field specs — the `"raw"` escape hatch | Any field-spec value must be a plain literal (str/num/bool/None/list/tuple/dict). For validators, callables, or other arbitrary Python, use the reserved `"raw"` key: a dict of kwarg name → verbatim source, e.g. `{"name": "score", "type": "IntegerField", "raw": {"validators": "[validators.MinValueValidator(0)]"}}`. Raw entries win over same-named plain keys; `from zeeb_orm import validators` is imported automatically when referenced. |
| `{prefix}setup_auth` / `{prefix}setup_oauth` | Idempotent wiring: re-running reports `already_wired=True` instead of duplicating includes. OAuth credentials are read from env vars (`data["client_id_env"]` / `data["client_secret_env"]`) — set them with `{prefix}set_env`. |

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
   This step also wires the ViewSet into `apps/<app>/urls.py` for you (see below);
   there is no separate route-registration call.
5. `{prefix}create_migration()` then `{prefix}run_migrations()` — apply schema.

`{prefix}generate_crud(...)` does the model + serializer + viewset (with routing)
in one shot; you still run the migrations afterwards.

### How URLs get registered

Routing is two-tiered, mirroring Django:

- `{prefix}create_viewset(...)` (and `{prefix}generate_crud(...)`) appends
  `router.register("<prefix>", <Model>ViewSet)` to **`apps/<app>/urls.py`** and
  ensures the ViewSet is imported there — route registration is automatic, there
  is no separate register-route tool. Override the URL segment with the
  `prefix=` argument (it defaults to the lowercase model name + `s`).
- The app's router is in turn included by the **project's `myproject/urls.py`**,
  which mounts each app and the auth routes. A `ModelViewSet` registered this
  way exposes the full CRUD set plus a `POST /<prefix>/query/` endpoint that
  accepts serialized `Q` filter strings.
- For a single function endpoint instead of a ViewSet, use
  `{prefix}create_route(app, path, method, name, body=..., imports=...)`.
  It appends a plain `@router.<method>(path)` handler (on a FastAPI
  `APIRouter`) to `views.py` **and** wires that router into `apps/<app>/urls.py`
  for you, so the endpoint is served once the app router is included by the
  project `urls.py` — the same inclusion a ViewSet needs. Provide the handler
  logic via `body=`; you should not write the wrapper by hand.
- Inspect what is wired with `{prefix}list_endpoints()` (scans `urls.py`) or
  `{prefix}list_all_routes()` (full inventory incl. function routes).

### How auth works

`zeeb_api` ships Django-like auth that these tools manage:

- **Users**: a `User` model with hashed passwords. Manage it with
  `{prefix}create_user(email, password, is_staff=, is_superuser=)`,
  `{prefix}list_users`, `{prefix}get_user`, `{prefix}update_user`,
  `{prefix}delete_user`, and `{prefix}set_user_password` (which re-hashes).
  Never write raw password values — always go through these tools.
- **Tokens**: JWT with access/refresh tokens. Wire the auth router
  (`login` / `refresh` / `logout` / `me` / optional `register`) with
  `{prefix}setup_auth(enable_registration=, url_prefix=)`; a middleware sets
  `request.state.user`. Secrets come from settings/env — manage them with
  `{prefix}manage_settings` / `{prefix}set_env`. In production
  (`DEBUG=false`) insecure default secrets are refused and the app fails fast.
- **OAuth/OIDC**: `{prefix}setup_oauth(provider=...)` configures an
  `OAUTH_PROVIDERS` entry (azure / github / google presets) and wires the
  OAuth router; credentials are read from env vars.
- **Custom user model**: `{prefix}create_user_model(app, model_name=, extra_fields=)`
  extends `AbstractUser` and sets `AUTH_USER_MODEL`; run migrations after.
- **Authorization**: DRF-style permission classes guard ViewSets. ViewSets
  scaffolded by `{prefix}create_viewset` default to
  `IsAuthenticatedOrReadOnly`; pass `permission=` to change it (valid:
  `AllowAny`, `IsAuthenticated`, `IsAdminUser`, `IsAuthenticatedOrReadOnly`,
  `IsOwner`, `IsOwnerOrReadOnly`, `DjangoModelPermissions`). Create custom
  classes with `{prefix}create_permission_class(app, class_name, logic=...)`
  and list them with `{prefix}list_permission_classes`.
- **Rate limiting & versioning**: set project-wide defaults with
  `{prefix}configure_throttling(default_classes=, rates=)` and
  `{prefix}configure_versioning(scheme=, default_version=, allowed_versions=)`;
  per-viewset throttles go through `{prefix}create_viewset(throttles=...)`.

### Settings & configuration

Project configuration lives in `myproject/settings.py` (read it with
`{prefix}get_settings`, edit single keys with `{prefix}manage_settings`) and in
the `.env` file (`{prefix}get_env` / `{prefix}set_env` / `{prefix}delete_env`).
The database URL, debug flag, CORS, and JWT secrets all flow from here.

## 7. Build with scaffolding tools, not `write_file`

`{prefix}write_file` overwrites a whole file and bypasses the generators'
import-management and route-wiring. **It is the wrong tool for backend
components.** Almost everything you would otherwise hand-write has a tool that
emits valid, correctly-imported, correctly-wired code:

| Want to add… | Use | Not |
|---|---|---|
| a model / field / relation | `{prefix}create_model` / `{prefix}add_field` / `{prefix}add_relationship` | editing `models.py` |
| a serializer | `{prefix}create_serializer` / `{prefix}update_serializer` | editing `serializers.py` |
| a CRUD ViewSet (auto-wired into `urls.py`) | `{prefix}create_viewset` | editing `views.py` / `urls.py` |
| a custom ViewSet action | `{prefix}create_action(viewset=..., name=...)` | a hand-written `@action` |
| a standalone `@router.get/post` endpoint | `{prefix}create_route(..., body=..., imports=...)` | a hand-written `@router.<m>` wrapper |
| a signal receiver | `{prefix}create_signal_receiver` (+ `{prefix}edit_signal_receiver`) | editing `signals.py` |
| a permission class | `{prefix}create_permission_class` | editing `permissions.py` |
| a FilterSet | `{prefix}create_filterset` (+ `{prefix}create_viewset(filterset=...)`) | editing `filters.py` |
| JWT auth / OAuth / user model | `{prefix}setup_auth` / `{prefix}setup_oauth` / `{prefix}create_user_model` | editing `urls.py` / `settings.py` |
| throttling / versioning defaults | `{prefix}configure_throttling` / `{prefix}configure_versioning` | editing `settings.py` |
| CORS / a setting | `{prefix}configure_cors` / `{prefix}manage_settings` | editing `settings.py` |

Pass your actual implementation through the tool's `body=` argument (for routes
and actions) so you keep full control of the logic without touching files
directly. The scaffolding tools handle the imports, the router instance, and
the `urls.py` wiring that hand-written code routinely gets wrong. Reserve
`{prefix}write_file` for files no tool covers (e.g. an ad-hoc utility module),
and `{prefix}read_file` to inspect what a tool generated.

---

See also `mcp://docs/capabilities` (full tool inventory),
`mcp://docs/project-lifecycle`, `mcp://docs/backend-generation`,
`mcp://docs/frontend-generation`, and `mcp://docs/deployment`.
