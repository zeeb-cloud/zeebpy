# zeeb_agents — Agent Function Library

`zeeb_agents` is a standalone async Python library that exposes all Zeeb
scaffolding and management operations as importable functions.  It has no MCP
dependency — you can use it from any Python code, and plug it into an MCP
server, CLI tool, or AI agent externally.

## Installation / Usage

```python
from zeeb_agents import (
    create_project, create_app,
    create_model, add_field, generate_crud,
    run_migrations, get_migration_status,
    read_logs, search_logs,
    get_env, set_env,
    read_file, write_file, search_code,
    list_tables, run_query,
    run_tests,
    run_management_command,
)
```

All functions are `async` and return an `AgentResult`:

```python
@dataclass
class AgentResult:
    success: bool          # True / False
    message: str           # Human-readable summary
    data: dict | None      # Structured output (model names, paths, etc.)
```

```python
result = await create_model("blog", "Post", [
    {"name": "title", "type": "CharField", "max_length": 200},
    {"name": "body",  "type": "TextField"},
    {"name": "author", "type": "ForeignKey", "to": "User", "on_delete": "CASCADE"},
])
if result:   # AgentResult is truthy on success
    print(result.message)
```

### Error handling & logging

Agent functions **never raise**. *Expected* failures (bad input, a missing
app/model, invalid SQL, …) return a plain actionable message — often with a
"Did you mean: …?" suggestion — and populate `data["error_code"]` (a closed
vocabulary: `app_not_found`, `model_not_found`, `already_exists`,
`invalid_field_spec`, `invalid_field_type`, `invalid_identifier`,
`invalid_input`, `invalid_permission`, `invalid_meta`, `invalid_sql`,
`invalid_regex`, `table_not_found`, `user_not_found`, `no_user_table`,
`setting_not_found`, `env_key_not_found`, `field_not_found`,
`function_not_found`, `file_not_found`, `log_file_not_found`,
`outside_project_root`, `server_not_running`, `server_not_reachable`,
`dependency_missing`, `permission_denied`, `no_project_root`) plus
`data["suggestions"]` (close-match candidates) where applicable. Any
*unexpected* exception is caught and returned as
`AgentResult(success=False, message="ExceptionType: details")`.
Full tracebacks are logged via the `"zeeb_agents"` logger
(`logging.getLogger("zeeb_agents")`); attach a handler or raise the level to
see/silence them:

```python
import logging
logging.getLogger("zeeb_agents").setLevel(logging.ERROR)
```

### Return-shape conventions

`data` shapes vary per function (each function's docstring documents its keys
under a `Returns data:` block), but they follow consistent conventions:

- **List / inventory functions** return the plural entity key **and** a
  `count`: `{"apps": [...], "count": 3}`, `{"models": [...], "count": 5}`.
- **File / scaffold functions** return affected paths **relative to the
  project root** (`data["path"]`).
- **Multi-step functions** return a list of what happened
  (`generate_crud` → `data["steps"]`; `update_model` → `data["changes"]`;
  `run_migrations` → `data["applied"]`).
- **On failure** `data` carries `error_code` (and often `suggestions` or
  other context like `read_logs` → `{"searched_in": ...}`) for expected
  failures, and may be `None` for unexpected ones. Always guard with
  `if result.success and result.data:` before indexing.

### `project_root` auto-detection

Every project-scoped function takes a trailing `project_root` argument that you
normally omit. When it is `None`, the `@agent_function` decorator walks up from
the current working directory to the nearest `manage.py` and uses that
directory. Pass an explicit path only to target a project outside the CWD.

### Special cases & gotchas

| Function | Behavior to know |
|---|---|
| `read_logs(level=...)` | `level` matches the level as a whole token (`[ERROR]`, ` ERROR `), not as a substring — so it won't match `NOTANERROR`. |
| `create_route(path=..., body=...)` | Generates a FastAPI `APIRouter` handler and auto-wires it into `urls.py`; pass logic via `body=`. `{name}` path segments (`/items/{item_id}`) become typed `str` handler params. |
| `get_env()` | A missing `.env` is reported as `success=False` (with an empty `env` dict). |
| `make_migrations()` | "No changes detected" is `success=True` with `data["created"] = None`. |
| `manage_settings(key, value=...)` | Read mode = only `key`; write mode = `key` + `value`. Write only updates keys that already exist. |
| `replace_model_fields(...)` | Destructive — replaces **all** fields. Use `add_field`/`remove_field` for incremental edits. |
| `run_query(sql)` | Read-only: SELECT/WITH/EXPLAIN only, single statement, always rolled back. |
| `export_openapi(...)` | Requires the dev server to be running (fetches the live spec). |
| Relation field specs | `to` is **required**; `on_delete="SET_NULL"` needs `null=True`; M2M rejects `on_delete`/`null`. Invalid specs are rejected before anything is written. |
| Field spec `"raw"` key | Escape hatch for non-literal kwargs (validators, callables): a dict of kwarg → verbatim Python source. |
| `setup_auth` / `setup_oauth` | Idempotent — re-running reports `already_wired` instead of duplicating includes. |
| `start_server(...)` | Server output goes to `.zeeb_server.log`; an immediate exit returns the last log lines in `data["output"]`. |

### Tool discovery — `list_capabilities()`

For programmatic discovery of the entire tool surface, call
`list_capabilities()`. It introspects everything exported from
`zeeb_agents.__all__`, so the inventory always matches the installed code:

```python
result = await list_capabilities(include_docstrings=True)
for tool in result.data["tools"]:
    print(tool["module"], tool["name"], tool["signature"])
    # tool["summary"] (first docstring line) and tool["doc"] (full docstring,
    # incl. the Returns data: block) are also available
```

Pass `module="models"` to filter to one module. This is the function an MCP
server typically registers so an agent can discover available tools at runtime.

---

## Project & App Management

### `create_project(name, directory=".")`
Create a new Zeeb project.  Delegates to the CLI `startproject` command so
the structure is always in sync.

```python
await create_project("myapp", directory="/projects")
```

### `create_app(name, project_root=None)`
Create a new app inside an existing project.

```python
await create_app("blog")
```

### `delete_app(name, project_root=None)`
Delete an app directory.

### `rename_app(old_name, new_name, project_root=None)`
Rename an app directory.

### `get_project_info(project_root=None)`
Returns project root, app list, installed apps, and database URL.

---

## Model Management

### `create_model(app, model_name, fields, meta=None, project_root=None)`
Append a `Model` subclass to `apps/<app>/models.py`.

**Field spec dicts:**

| Key | Required | Notes |
|-----|----------|-------|
| `name` | ✅ | Python attribute name (validated as an identifier) |
| `type` | ✅ | See [Field Types](#field-types) |
| `max_length` | CharField | |
| `null` | | `True` / `False` |
| `default` | | Any literal value (str/num/bool/None/list/dict) |
| `choices` | | List of `[value, label]` pairs |
| `help_text`, `verbose_name`, `db_column`, `unique`, `index` | | Pass through as literals |
| `to` | FK / O2O / M2M | Target model name — **required** on relation fields |
| `on_delete` | FK / O2O | One of CASCADE/PROTECT/RESTRICT/SET_NULL/SET_DEFAULT/DO_NOTHING; `SET_NULL` requires `null=True` |
| `through`, `through_fields`, `related_name`, `db_table` | M2M | Custom through table support |
| `raw` | | Escape hatch: dict of kwarg → **verbatim Python source** for validators, callables, etc. Raw entries win over same-named keys. |

All specs are validated up front — invalid types get "Did you mean" suggestions
and nothing is written on failure.

```python
await create_model("blog", "Post", [
    {"name": "title",   "type": "CharField",  "max_length": 200},
    {"name": "body",    "type": "TextField"},
    {"name": "slug",    "type": "SlugField",  "unique": True},
    {"name": "status",  "type": "CharField",  "max_length": 10,
     "choices": [["draft", "Draft"], ["pub", "Published"]], "default": "draft"},
    {"name": "score",   "type": "IntegerField", "default": 0,
     "raw": {"validators": "[validators.MinValueValidator(0)]"}},
    {"name": "author",  "type": "ForeignKey", "to": "User"},
], meta={
    "ordering": ["-created_at"],
    "unique_together": [["author", "slug"]],
    "indexes": [{"fields": ["created_at"], "name": "idx_post_created"}],
    "constraints": [{"check": "score >= 0", "name": "ck_score_positive"}],
})
```

Valid `meta` keys: `table_name`/`db_table`, `abstract`, `managed`, `ordering`,
`unique_together`, `index_together`, `indexes`, `constraints`,
`default_permissions`, `app_label`.

### `update_model(app, model_name, rename_to=None, meta_changes=None, project_root=None)`
Rename a model and/or update its `class Meta` options.

### `delete_model(app, model_name, project_root=None)`
Remove a model class from `models.py`.

### `list_models(project_root=None)`
Return all models (and their fields) across all apps.

### `add_field(app, model_name, field, project_root=None)`
Add a single field to an existing model.

### `remove_field(app, model_name, field_name, project_root=None)`
Remove a field from an existing model.

### `add_relationship(app, model_name, rel, project_root=None)`
Convenience wrapper around `add_field` for relation fields.

```python
await add_relationship("blog", "Post", {
    "name": "category",
    "type": "ForeignKey",
    "to": "Category",
    "on_delete": "SET_NULL",
    "null": True,
})
```

---

## Serializer Management

### `create_serializer(app, model_name, fields=None, read_only_fields=None, extra_fields=None, validate_fields=None, project_root=None)`
Append a `ModelSerializer` to `apps/<app>/serializers.py`. `extra_fields`
declares serializer fields (`SerializerMethodField` entries get a `get_<name>`
stub, `{"type": "nested", "serializer": "UserSerializer"}` renders a nested
serializer, other types take kwargs like `write_only`/`source`/`max_length`);
`validate_fields` emits `validate_<field>(self, value)` stubs.

```python
await create_serializer("blog", "Post",
    fields=["id", "title", "body", "created_at"],
    read_only_fields=["id", "created_at"],
    extra_fields=[
        {"name": "summary", "type": "SerializerMethodField"},
        {"name": "author", "type": "nested", "serializer": "UserSerializer",
         "read_only": True},
    ],
    validate_fields=["title"])
```

### `update_serializer(app, model_name, fields=None, read_only_fields=None, project_root=None)`
Update the `fields` / `read_only_fields` of an existing serializer.

---

## ViewSet & Route Management

### `create_viewset(app, model_name, serializer_class=None, permission="IsAuthenticatedOrReadOnly", read_only=False, lookup_field=None, pagination=None, throttles=None, search_fields=None, ordering_fields=None, filterset=None, project_root=None)`
Append a `ModelViewSet` (or `ReadOnlyModelViewSet` with `read_only=True`) to
`apps/<app>/views.py`, with the full viewset option surface:

```python
await create_viewset("shop", "Product",
    permission="IsAuthenticated",
    lookup_field="slug",
    pagination="page",                 # "page" | "limit_offset" | "cursor"
    throttles=["UserRateThrottle"],    # Anon/User/ScopedRateThrottle
    search_fields=["name"],            # ?search=…
    ordering_fields=["price"],         # ?ordering=…
    filterset="ProductFilter")         # from apps/shop/filters.py
```

Permissions are validated against `zeeb_api.permissions` (`AllowAny`,
`IsAuthenticated`, `IsAdminUser`, `IsAuthenticatedOrReadOnly`, `IsOwner`,
`IsOwnerOrReadOnly`, `DjangoModelPermissions`); imports for pagination,
throttling, and filters are added automatically.

### `update_viewset(app, model_name, permission=None, lookup_field=None, pagination=None, throttles=None, search_fields=None, ordering_fields=None, project_root=None)`
Set or update class attributes on an existing ViewSet (same option names as
`create_viewset`); `filter_backends` is kept in sync when
`search_fields`/`ordering_fields` are given.

### `add_viewset_action(app, model_name, action_name, detail=True, methods=None, body=None, project_root=None)`
Add a custom `@action` method to an existing ViewSet. Pass the method body via
`body=` (dedented/indented automatically) so you don't have to edit `views.py`
by hand; omit it for a `pass  # TODO` placeholder.

```python
await add_viewset_action("blog", "Post", "publish",
    detail=True, methods=["post"],
    body="""
        post = await self.get_object()
        post.published = True
        await post.save()
        return {"status": "published"}
    """)
```

### `register_route(app, model_name, url_prefix=None, project_root=None)`
Add a `router.register(...)` line to `apps/<app>/urls.py`.

### `generate_crud(app, model_name, fields, serializer_fields=None, read_only_fields=None, permission=..., meta=None, extra_fields=None, validate_fields=None, read_only=False, lookup_field=None, pagination=None, throttles=None, search_fields=None, ordering_fields=None, filterset=None, project_root=None)`
**One-shot scaffold** — creates model + serializer + viewset + registers route.
Forwards `meta` to `create_model`, `extra_fields`/`validate_fields` to
`create_serializer`, and all viewset options to `create_viewset`.

```python
await generate_crud("blog", "Post", fields=[
    {"name": "title", "type": "CharField", "max_length": 200},
    {"name": "body",  "type": "TextField"},
])
```

### `list_endpoints(project_root=None)`
Return all `router.register(...)` calls found across all apps.

---

## Migrations

### `make_migrations(name=None, project_root=None)`
Detect model changes and write a migration file.

### `run_migrations(project_root=None)`
Apply all pending migrations.

### `get_migration_status(project_root=None)`
Return list of migrations with `applied: bool` for each.

### `rollback_migration(steps=1, project_root=None)`
Roll back the last N migration(s).

---

## Dev Server

### `start_server(addrport="127.0.0.1:9000", project_root=None)`
Start the development server as a background process.
PID is stored in `.zeeb_server.pid` in the project root.

### `stop_server(project_root=None)`
Stop the background server.

### `get_server_status(project_root=None)`
Check if the server is running.

---

## Field Types

Friendly aliases accepted by the `type` key in field specs:

| Alias(es) | zeeb_orm Field |
|-----------|---------------|
| `str`, `char`, `string`, `varchar` | `CharField` |
| `text` | `TextField` |
| `email` | `EmailField` |
| `url` | `URLField` |
| `slug` | `SlugField` |
| `int`, `integer` | `IntegerField` |
| `smallint` | `SmallIntegerField` |
| `bigint` | `BigIntegerField` |
| `posint` | `PositiveIntegerField` |
| `possmallint` | `PositiveSmallIntegerField` |
| `posbigint` | `PositiveBigIntegerField` |
| `float` | `FloatField` |
| `decimal` | `DecimalField` |
| `bool`, `boolean` | `BooleanField` |
| `date` | `DateField` |
| `datetime`, `timestamp` | `DateTimeField` |
| `time` | `TimeField` |
| `duration` | `DurationField` |
| `json` | `JSONField` |
| `uuid` | `UUIDField` |
| `binary`, `bytes` | `BinaryField` |
| `ip`, `ipaddress` | `GenericIPAddressField` |
| `auto` | `AutoField` |
| `bigauto` | `BigAutoField` |
| `uuidauto` | `UUIDAutoField` |
| `fk`, `foreignkey` | `ForeignKey` |
| `o2o`, `onetoone` | `OneToOneField` |
| `m2m`, `manytomany` | `ManyToManyField` |

The exact zeeb_orm class name is always accepted too (e.g. `"CharField"`).

---

## Logs

### `read_logs(lines=200, level=None, log_file=None, project_root=None)`
Return the last *lines* lines from the project log file.  Auto-detects log files in a `logs/` subdirectory or `*.log` files in the project root.

- `level`: Optional filter — only lines containing `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`.
- `log_file`: Explicit path to a log file (relative to project root or absolute).

```python
result = await read_logs(lines=50, level="ERROR")
# result.data == {"path": "logs/app.log", "lines": [...], "total_lines": 1200}
```

### `search_logs(pattern, log_file=None, project_root=None)`
Search all log files for lines matching a regular expression.

```python
result = await search_logs(r"TimeoutError")
# result.data == {"matches": [{"file": "logs/app.log", "line_no": 42, "content": "..."}], "count": 3}
```

### `clear_logs(log_file=None, project_root=None)`
Truncate log file(s) to zero bytes.

---

## Configuration & Environment

### `get_settings(project_root=None)`
Return the parsed project settings as a dictionary.

```python
result = await get_settings()
# result.data == {"settings": {"DATABASE": {"url": "..."}, "DEBUG": True, ...}}
```

### `get_env(project_root=None)`
Read the project `.env` file and return it as a key-value dict.

```python
result = await get_env()
# result.data == {"path": ".env", "env": {"SECRET_KEY": "...", "DEBUG": "1"}}
```

### `set_env(key, value, project_root=None)`
Set (or create) an environment variable in `.env`.  Creates the file if it does not exist.

```python
await set_env("SECRET_KEY", "my-secret")
```

### `delete_env(key, project_root=None)`
Remove a key from `.env`.

---

## File System

> **Safety**: file paths (relative or absolute) must resolve to a location
> **inside the project root**.  Paths that escape it (e.g. `../secret.txt` or
> an absolute path outside the project) return
> `AgentResult(success=False, message="...outside the project root")` without
> touching the file.

### `read_file(path, project_root=None)`
Read any project file and return its content.

```python
result = await read_file("myapp/apps/post/models.py")
# result.data == {"path": "...", "content": "...", "size": 512}
```

### `write_file(path, content, project_root=None)`
Write (or overwrite) a file.  Creates parent directories as needed.

```python
await write_file("myapp/apps/post/utils.py", "# utilities\n")
```

### `list_files(directory=".", pattern="*", project_root=None)`
List files and directories with an optional glob pattern.

```python
result = await list_files("myapp/apps", pattern="*.py")
# result.data == {"entries": [{"name": "models.py", "path": "...", "type": "file", "size": 512}, ...]}
```

### `search_code(pattern, glob="**/*.py", project_root=None)`
Search for a regex pattern across project source files.

```python
result = await search_code(r"class Post", glob="**/*.py")
# result.data == {"files": [{"file": "...", "matches": [{"line_no": 5, "content": "class Post(Model):"}]}], "total_matches": 1}
```

---

## Database Introspection

### `list_tables(project_root=None)`
Return the names of all tables in the project database.

```python
result = await list_tables()
# result.data == {"tables": ["auth_user", "post_post", "migrations"]}
```

### `describe_table(table_name, project_root=None)`
Return column names, types, nullability, and defaults for a table.

```python
result = await describe_table("post_post")
# result.data == {"table": "post_post", "columns": [{"name": "id", "type": "INTEGER", ...}, ...]}
```

### `run_query(sql, project_root=None)`
Execute a read-only SQL query (`SELECT`, `WITH`, `EXPLAIN`) and return rows as a list of dicts.

```python
result = await run_query("SELECT id, title FROM post_post LIMIT 5")
# result.data == {"rows": [{"id": 1, "title": "Hello World"}], "count": 1}
```

> **Safety**: `run_query` is gated to read-only queries:
>
> - SQL comments (`-- ...` and `/* ... */`) are stripped before validation, so
>   comment-prefixed statements cannot sneak past the check.
> - Exactly **one** statement is allowed (a single trailing `;` is tolerated;
>   `SELECT 1; DROP TABLE x` is rejected).
> - The first keyword must be `SELECT`, `WITH`, or `EXPLAIN`.
> - The mutating keywords `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`,
>   `CREATE`, `TRUNCATE`, `REPLACE`, `GRANT`, `ATTACH`, `PRAGMA`, and `VACUUM`
>   are rejected **anywhere** in the statement (so `WITH ... DELETE` or
>   `EXPLAIN ANALYZE DELETE` bypasses fail too).  The check uses word
>   boundaries, so column names like `created_at` / `updated_at` are fine —
>   but a bare `REPLACE(...)` string function call will be rejected; rename or
>   avoid it in ad-hoc queries.
> - Defense in depth: the query runs inside an explicit transaction that is
>   **always rolled back**, so even a statement that slipped through could
>   never be committed.

---

## Testing

### `run_tests(path=None, verbose=False, project_root=None)`
Run the project test suite via pytest and return pass/fail counts and output.

- `path`: Specific test file or directory.  Runs all tests if `None`.
- `verbose`: Pass `-v` for detailed test names.

```python
result = await run_tests()
# result.success == True
# result.message == "114 passed, 0 failed"
# result.data == {"passed": 114, "failed": 0, "errors": 0, "skipped": 0, "output": "..."}
```

---

## Shell / Management Commands

### `run_management_command(command, args=None, project_root=None)`
Run any `manage.py` command and capture its output.

```python
result = await run_management_command("showmigrations")
result = await run_management_command("createsuperuser", args=["--no-input", "--username=admin"])
# result.data == {"command": "...", "args": [...], "returncode": 0, "output": "..."}
```

---

## Signals Scaffolding

Manage `@receiver` decorated functions in `{app}/signals.py` files.

### `create_signal_receiver(app, signal_name, model_name, function_name, project_root=None)`
Create an async receiver stub. Creates `signals.py` if it doesn't exist.

- `signal_name`: One of `pre_save`, `post_save`, `pre_delete`, `post_delete`.

```python
result = await create_signal_receiver("blog", "post_save", "Article", "on_article_saved")
# result.data == {"path": "blog/signals.py", "signal": "post_save", "action": "created", ...}
```

### `list_signal_receivers(app, project_root=None)`
List all `@receiver(...)` decorated functions in `{app}/signals.py`.

```python
result = await list_signal_receivers("blog")
# result.data == {"receivers": [{"func_name": "on_article_saved", "signal": "post_save", "sender": "Article"}], "count": 1}
```

### `read_signal_receiver(app, function_name, project_root=None)`
Return the full source (decorator + body) of a specific receiver.

```python
result = await read_signal_receiver("blog", "on_article_saved")
# result.data == {"source": "@receiver(post_save, sender=Article)\nasync def on_article_saved(...): ...", ...}
```

### `edit_signal_receiver(app, function_name, new_body, project_root=None)`
Replace the body of an existing receiver function (decorator is preserved).

```python
result = await edit_signal_receiver("blog", "on_article_saved", "    await notify(instance)")
```

### `delete_signal_receiver(app, function_name, project_root=None)`
Remove a receiver function and its `@receiver(...)` decorator from `signals.py`.

```python
result = await delete_signal_receiver("blog", "on_article_saved")
```

### `list_model_signals(app, model_name, project_root=None)`
Filter receivers by `sender=<model_name>` — shows all signals for one model.

```python
result = await list_model_signals("blog", "Article")
# result.data == {"model": "Article", "receivers": [{"func_name": "...", "signal": "post_save"}], "count": 1}
```

---

## Project Introspection

### `list_apps(project_root=None)`
Return all app directory names found under `apps/`.

```python
result = await list_apps()
# result.data == {"apps": ["blog", "users"], "count": 2}
```

### `get_project_structure(project_root=None, max_depth=3)`
Return a nested directory tree for the project. Skips `__pycache__`, `.git`, `.venv`, etc.

```python
result = await get_project_structure(max_depth=2)
# result.data == {"root": "/path/to/project", "file_count": 42, "tree": {...}}
# tree == {"name": "myproject", "type": "dir", "children": [...]}
```

---

## Standalone Routes

### `create_route(app, path, method, function_name, response_model=None, body=None, imports=None, project_root=None)`
Append a standalone `@router.<method>(path)` async handler to `{app}/views.py`
**and** auto-wire it into `{app}/urls.py` so it is actually served. Unlike
`create_viewset`, this creates a plain function — not a class-based ViewSet. The
handler lives on a FastAPI `router = APIRouter()` (note: `zeeb_api` exposes no
`Router`) and always receives a typed `request: Request`.

- `method`: One of `get`, `post`, `put`, `patch`, `delete`.
- `response_model`: Optional Pydantic model name added as `response_model=...`.
- `body`: Handler implementation as Python source (dedented/indented for you).
  Put the real logic here — no `write_file` needed. Omit for a placeholder.
- `imports`: Import lines the body needs (added to `views.py` if absent).

```python
result = await create_route(
    "blog", "/posts/featured", "get", "get_featured_posts",
    imports=["from .models import Post"],
    body="""
        posts = await Post.objects.filter(featured=True).all()
        return [{"id": str(p.id), "title": p.title} for p in posts]
    """,
)
# result.data == {"app": "blog", "path": "/posts/featured", "method": "get",
#                 "function_name": "get_featured_posts", "response_model": None, "wired": True}
```

---

## Model Field Replacement

### `replace_model_fields(app, model_name, fields, project_root=None)`
Destructively replace **all** field lines in a model with a new set.
`class Meta:` is preserved. Use `add_field`/`remove_field` for incremental changes.

```python
result = await replace_model_fields("blog", "Post", [
    {"name": "title", "type": "CharField", "max_length": 200},
    {"name": "body",  "type": "TextField"},
    {"name": "published", "type": "BooleanField", "default": False},
])
# result.data == {"model": "Post", "fields": ["title", "body", "published"]}
```

---

## Settings Management

### `manage_settings(key, value=None, *, read_only=False, project_root=None)`
Read or update a top-level scalar setting in `settings.py`.

**Read a setting:**
```python
result = await manage_settings("DEBUG")
# result.data == {"key": "DEBUG", "value": True}
```

**Update a setting:**
```python
result = await manage_settings("DEBUG", False)
result = await manage_settings("SECRET_KEY", "new-secret-value")
# result.data == {"key": "DEBUG", "value": False}
```

Supports scalar types only (`str`, `int`, `float`, `bool`, `None`).
For complex types (dict, list), use `read_file`/`write_file` to edit `settings.py` directly.

---

## Seed Data

### `generate_seed_script(app, models=None, count=5, output_path=None, project_root=None)`
Generate a Python seed script that populates the database with sample records.
Reads model definitions from `{app}/models.py` and writes `seeds/{app}_seed.py`.

- `models`: List of model names to include. Defaults to all models in the app.
- `count`: Number of records to create per model (default: 5).
- `output_path`: Override the output file path (relative to project root).

Placeholders are typed per field (ints stay ints, booleans stay booleans,
JSON fields get dict literals, `DurationField` gets `timedelta`); fields with
`choices` use the first choice value. Auto-generated fields (PKs, `auto_now`
timestamps, M2M) and nullable relations are omitted; non-null relations are
emitted as `None` with a `# TODO` comment to fill in.

```python
result = await generate_seed_script("blog", count=10)
# result.data == {"path": "seeds/blog_seed.py", "models_seeded": ["Post", "Comment"], "count": 10}

# Run the generated script
# python seeds/blog_seed.py
```

---

## BaaS — User Management

Functions for runtime user CRUD against the project's live database.
Passwords are always hashed via zeeb_api's PBKDF2 hasher.

### `create_user(email, password, is_staff=False, is_superuser=False, project_root=None)`
Create a new user.  The user table is auto-detected (first table with `email` + `password` columns).

```python
result = await create_user("alice@example.com", "s3cr3t")
result = await create_user("admin@example.com", "admin123", is_staff=True, is_superuser=True)
```

### `list_users(limit=50, offset=0, project_root=None)`
List users (passwords omitted from results).

```python
result = await list_users(limit=20)
# result.data == {"users": [...], "total": 42, "limit": 20, "offset": 0}
```

### `get_user(email_or_id, project_root=None)`
Fetch a single user by email or integer ID.

```python
result = await get_user("alice@example.com")
result = await get_user(1)
```

### `update_user(email_or_id, changes, project_root=None)`
Update user fields.  Pass `password` through `set_user_password` instead.

```python
result = await update_user("alice@example.com", {"is_staff": True, "is_active": False})
```

### `delete_user(email_or_id, project_root=None)`
Delete a user by email or integer ID.

```python
result = await delete_user("alice@example.com")
```

### `set_user_password(email_or_id, new_password, project_root=None)`
Set a new hashed password for an existing user.

```python
result = await set_user_password("alice@example.com", "n3wP@ssword!")
```

---

## BaaS — CORS Configuration

### `configure_cors(origins, methods=None, allow_credentials=True, allow_headers=None, expose_headers=None, project_root=None)`
Write CORS settings to `settings.py`.  `zeeb_api.middleware.CORSMiddleware`
reads these keys automatically.

```python
result = await configure_cors(
    origins=["https://myapp.com", "http://localhost:3000"],
    methods=["GET", "POST", "PUT", "DELETE"],
)
# Writes: CORS_ALLOW_ORIGINS, CORS_ALLOW_METHODS, CORS_ALLOW_CREDENTIALS, CORS_ALLOW_HEADERS
```

Add middleware in `settings.py`:
```python
MIDDLEWARE = [
    "zeeb_api.middleware.CORSMiddleware",
]
```

### `get_cors_config(project_root=None)`
Read the current CORS settings from the project.

```python
result = await get_cors_config()
# result.data == {"cors": {"CORS_ALLOW_ORIGINS": [...], ...}}
```

---

## BaaS — Background Tasks

### `create_task(app, function_name, schedule=None, project_root=None)`
Scaffold an async task function in `apps/{app}/tasks.py`.
Creates the file with a header if it does not exist.

- `schedule`: Cron expression (e.g. `"0 9 1 * *"`) used as a comment in the stub.

```python
result = await create_task("billing", "send_monthly_invoices", schedule="0 9 1 * *")
result = await create_task("notifications", "cleanup_old_notifications")
```

### `list_tasks(app, project_root=None)`
List all async task functions in `apps/{app}/tasks.py`.

```python
result = await list_tasks("billing")
# result.data == {"tasks": ["send_monthly_invoices", "retry_failed_payments"], "count": 2}
```

### `delete_task(app, function_name, project_root=None)`
Remove a task function from `apps/{app}/tasks.py`.

```python
result = await delete_task("billing", "send_monthly_invoices")
```

---

## BaaS — Health Endpoints

### `create_health_endpoint(project_root=None)`
Scaffold `/health` (liveness) and `/ready` (readiness + DB ping) route handlers
in `health.py` at the project root.

```python
result = await create_health_endpoint()
# Creates health.py with GET /health and GET /ready

# Register in your main app:
# from health import router as health_router
# app.include_router(health_router)
```

### `check_system_health(project_root=None)`
Run runtime health checks: settings load, DB connectivity, table inventory.

```python
result = await check_system_health()
# result.success == True  (overall healthy)
# result.data == {
#   "checks": {"settings": "ok", "db": "ok", "tables": [...], "overall": "healthy"}
# }
```

---

## BaaS — API Schema & Route Introspection

### `get_model_json_schema(app, model_name, project_root=None)`
Return a JSON Schema object for a zeeb_orm model — useful for client SDK
generation or frontend form validation.

```python
result = await get_model_json_schema("blog", "Post")
# result.data["schema"] == {
#   "$schema": "...", "title": "Post", "type": "object",
#   "properties": {"id": ..., "title": ..., "body": ...},
#   "required": ["title"]
# }
```

### `list_all_routes(project_root=None)`
Scan all apps for both ViewSet registrations and standalone route decorators.

```python
result = await list_all_routes()
# result.data["routes"] == [
#   {"app": "blog", "type": "viewset", "prefix": "posts", "viewset": "PostViewSet"},
#   {"app": "blog", "type": "route",   "method": "GET",   "path": "/posts/featured"},
# ]
```

### `export_openapi(output_path=None, port=8000, project_root=None)`
Fetch the live OpenAPI spec from the running dev server and save to `openapi.json`.
The dev server must be running (`zeeb runserver`).

```python
result = await export_openapi(port=8000)
# Saves openapi.json in project root
```

---

## BaaS — Deployment Scaffolding

### `generate_dockerfile(python_version="3.12", port=8000, project_root=None)`
Generate a multi-stage production `Dockerfile` and `.dockerignore`.

```python
result = await generate_dockerfile(python_version="3.12", port=8080)
# result.data == {"files_written": ["Dockerfile", ".dockerignore"], ...}
```

### `generate_requirements(output_path="requirements.txt", project_root=None)`
Run `pip freeze` and write `requirements.txt` (filters out editable installs).

```python
result = await generate_requirements()
# result.data == {"path": "requirements.txt", "package_count": 42}
```

### `check_production_readiness(project_root=None)`
Validate that the project is ready for production.  Checks:

- `DEBUG = False`
- `SECRET_KEY` is set and not a placeholder
- Database is not SQLite
- `requirements.txt` exists
- `Dockerfile` exists

```python
result = await check_production_readiness()
# result.success == False  (has issues)
# result.data == {
#   "ready": False,
#   "issues": ["DEBUG is True ...", "Database is SQLite ..."],
#   "passed": ["SECRET_KEY is set."]
# }
```

---

## BaaS — Permission Class Scaffolding

### `create_permission_class(app, class_name, logic="deny_all", project_root=None)`
Scaffold a `BasePermission` subclass in `apps/{app}/permissions.py`.

Available `logic` presets:
- `"deny_all"` — reject all (safe default during development)
- `"allow_all"` — accept all unconditionally
- `"owner_only"` — allow if `obj.user_id == request.user.id`
- `"staff_only"` — allow if `request.user.is_staff`
- `"authenticated"` — allow any authenticated user

```python
result = await create_permission_class("blog", "IsPostOwner", logic="owner_only")
result = await create_permission_class("admin", "IsStaffUser", logic="staff_only")

# Use in a ViewSet:
# class PostViewSet(ModelViewSet):
#     permission_classes = [IsPostOwner]
```

### `list_permission_classes(app, project_root=None)`
List all permission classes in `apps/{app}/permissions.py`.

```python
result = await list_permission_classes("blog")
# result.data == {"permissions": ["IsPostOwner", "IsPublicRead"], "count": 2}
```

---

## Auth Scaffolding

### `setup_auth(enable_registration=True, url_prefix="/auth", access_token_minutes=None, refresh_token_days=None, project_root=None)`
Wire zeeb_api's JWT auth router into the project `urls.py` — exposes
`POST {prefix}/login/`, `/refresh/`, `/logout/`, `GET /me/`, and (optionally)
`POST /register/`. Idempotent: re-running reports `data["already_wired"]=True`.
Optionally writes `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` /
`JWT_REFRESH_TOKEN_EXPIRE_DAYS`.

```python
await setup_auth(enable_registration=True, access_token_minutes=30)
```

### `setup_oauth(provider, client_id_env=None, client_secret_env=None, scopes=None, redirect_uri=None, project_root=None)`
Configure an OAuth/OIDC provider preset (`"azure"`, `"github"`, `"google"`)
in `OAUTH_PROVIDERS` and wire `create_oauth_router()` into `urls.py`.
Credentials are read from environment variables (default
`<PROVIDER>_CLIENT_ID` / `<PROVIDER>_CLIENT_SECRET`) — set them with
`set_env`. Requires the `zeebpy[oauth]` extra at runtime.

```python
await setup_oauth("google")
await set_env("GOOGLE_CLIENT_ID", "…")
await set_env("GOOGLE_CLIENT_SECRET", "…")
```

### `create_user_model(app, model_name="User", extra_fields=None, set_auth_user_model=True, project_root=None)`
Create a custom user model extending `zeeb_api.auth.models.AbstractUser` and
set `AUTH_USER_MODEL`. Run migrations afterwards; `create_user` etc. then work
against the new table.

```python
await create_user_model("accounts", "Member", extra_fields=[
    {"name": "phone", "type": "CharField", "max_length": 20, "null": True},
])
```

---

## FilterSet Scaffolding

### `create_filterset(app, model_name, filter_fields, project_root=None)`
Create a `FilterSet` class in `apps/{app}/filters.py` for declarative
query-parameter filtering; attach it to a viewset via
`create_viewset(filterset=...)`.

```python
await create_filterset("shop", "Product", {
    "price": ["gte", "lte"],       # ?price__gte=10&price__lte=50
    "status": ["exact", "in"],     # ?status=live or ?status__in=live,draft
})
```

Valid lookups: `exact`, `iexact`, `contains`, `icontains`, `in`, `gt`, `gte`,
`lt`, `lte`, `startswith`, `istartswith`, `endswith`, `iendswith`, `isnull`.

---

## API Configuration

### `configure_throttling(default_classes=None, rates=None, project_root=None)`
Write project-wide rate-limit defaults (`DEFAULT_THROTTLE_CLASSES` as
`zeeb_api.throttling` dotted paths, `DEFAULT_THROTTLE_RATES`). Rate format:
`"<number>/<period>"` with period `s/sec`, `m/min`, `h/hour`, `d/day`.

```python
await configure_throttling(
    default_classes=["AnonRateThrottle"],
    rates={"anon": "100/hour", "user": "1000/day"},
)
```

> The default throttle cache is in-memory **per process** — multi-worker
> deployments need a custom `BaseThrottleCache`.

### `configure_versioning(scheme="url", default_version=None, allowed_versions=None, project_root=None)`
Write API-versioning settings (`DEFAULT_VERSIONING_CLASS`, `DEFAULT_VERSION`,
`ALLOWED_VERSIONS`). Schemes: `"url"` (`/v1/…`), `"header"`
(`X-API-Version`), `"query"` (`?version=`), `"accept"`.

```python
await configure_versioning(scheme="header", default_version="1.0",
                           allowed_versions=["1.0", "2.0"])
```

---

## MCP Resource Content

Functions that return rich markdown documentation for use as MCP resource
bodies.  Each function maps to one `mcp://` URI and returns an
`AgentResult` with:

- `data["content"]`   — markdown string (serve as resource body)
- `data["uri"]`       — the canonical `mcp://` URI
- `data["mime_type"]` — always `"text/markdown"`

When `project_root` is supplied, live project context (app list, migration
status, readiness check, etc.) is appended to the static documentation.

### Tool name prefix

All documentation files use `{prefix}` as a placeholder before every tool
name.  When the markdown is rendered, `{prefix}` is replaced with the
`tool_prefix` argument you pass — matching whatever prefix your MCP server
registered the tools under.

```python
# MCP server that registers tools as "zeeb_create_model", "zeeb_list_apps", …
result = await get_resource("mcp://docs/capabilities", tool_prefix="zeeb_")
# → docs show zeeb_create_model, zeeb_list_apps, …

# MCP server with no prefix (tools keep their Python function names)
result = await get_resource("mcp://docs/capabilities", tool_prefix="")
# → docs show create_model, list_apps, …
```

The `tool_prefix` parameter is available on every individual doc function and
on `get_resource()`.

### `get_resource(uri, project_root=None, tool_prefix="")` — URI dispatcher

```python
from zeeb_agents import get_resource, RESOURCE_URIS

# RESOURCE_URIS maps short names → full URIs
# {
#   "principles":         "mcp://docs/principles",
#   "capabilities":       "mcp://docs/capabilities",
#   "project-lifecycle":  "mcp://docs/project-lifecycle",
#   "backend-generation": "mcp://docs/backend-generation",
#   "frontend-generation":"mcp://docs/frontend-generation",
#   "deployment":         "mcp://docs/deployment",
# }

result = await get_resource("mcp://docs/capabilities", tool_prefix="zeeb_")
print(result.data["content"])   # markdown with zeeb_* tool names

# With live project context
result = await get_resource("mcp://docs/deployment", project_root=Path("."), tool_prefix="zeeb_")
```

### `get_capabilities_doc(project_root=None, tool_prefix="")` → `mcp://docs/capabilities`

Complete inventory of all public `zeeb_agents` functions (80+), organised
by module with signatures and descriptions.  Tool names use `{prefix}` in
the source file — rendered with the given `tool_prefix` at call time.

### `get_project_lifecycle_doc(project_root=None, tool_prefix="")` → `mcp://docs/project-lifecycle`

Step-by-step guide from `create_project` → apps → models → migrations →
API → users → server → tasks → monitoring.  Dynamic context: current app
list and migration status.

### `get_backend_generation_doc(project_root=None, tool_prefix="")` → `mcp://docs/backend-generation`

How to generate models, serializers, viewsets, routes, migrations, signals,
permissions, tasks, and seeds.  Includes field type reference table.

### `get_frontend_generation_doc(project_root=None, tool_prefix="")` → `mcp://docs/frontend-generation`

Frontend integration: CORS setup, JWT authentication, OpenAPI export, JSON
Schema for models, route inventory, health endpoints, WebSocket notes.
Dynamic context: configured CORS origins and registered route count.

### `get_deployment_doc(project_root=None, tool_prefix="")` → `mcp://docs/deployment`

Production deployment: readiness check, Dockerfile generation,
requirements.txt, health probes (k8s/Docker Compose examples), migrations
in CI/CD, environment variable reference, recommended stack.  Dynamic
context: live readiness check result.

### MCP server integration example

```python
# In your MCP server (using mcp-python or similar):
from mcp.server import Server
from mcp.types import Resource
from zeeb_agents import get_resource, RESOURCE_URIS

server = Server("zeebpy-mcp")

# The prefix your server uses when registering zeeb_agents tools
TOOL_PREFIX = "zeeb_"

@server.list_resources()
async def list_resources():
    return [
        Resource(uri=uri, name=name.replace("-", " ").title(), mimeType="text/markdown")
        for name, uri in RESOURCE_URIS.items()
    ]

@server.read_resource()
async def read_resource(uri: str):
    result = await get_resource(uri, tool_prefix=TOOL_PREFIX)
    if not result.success:
        raise ValueError(result.message)
    return result.data["content"]
```
