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
| `name` | ✅ | Python attribute name |
| `type` | ✅ | See [Field Types](#field-types) |
| `max_length` | CharField | |
| `null` | | `True` / `False` |
| `default` | | Any serialisable value |
| `to` | FK / O2O / M2M | Target model name |
| `on_delete` | FK / O2O | Default: `"CASCADE"` |

```python
await create_model("blog", "Post", [
    {"name": "title",   "type": "CharField",  "max_length": 200},
    {"name": "body",    "type": "TextField"},
    {"name": "slug",    "type": "SlugField",  "unique": True},
    {"name": "author",  "type": "ForeignKey", "to": "User"},
], meta={"ordering": ["-created_at"]})
```

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

### `create_serializer(app, model_name, fields=None, read_only_fields=None, project_root=None)`
Append a `ModelSerializer` to `apps/<app>/serializers.py`.

```python
await create_serializer("blog", "Post",
    fields=["id", "title", "body", "created_at"],
    read_only_fields=["id", "created_at"])
```

### `update_serializer(app, model_name, fields=None, read_only_fields=None, project_root=None)`
Update the `fields` / `read_only_fields` of an existing serializer.

---

## ViewSet & Route Management

### `create_viewset(app, model_name, serializer_class=None, permission="IsAuthenticatedOrReadOnly", project_root=None)`
Append a `ModelViewSet` to `apps/<app>/views.py`.

### `add_viewset_action(app, model_name, action_name, detail=True, methods=None, project_root=None)`
Add a custom `@action` method to an existing ViewSet.

```python
await add_viewset_action("blog", "Post", "publish",
    detail=True, methods=["post"])
```

### `register_route(app, model_name, url_prefix=None, project_root=None)`
Add a `router.register(...)` line to `apps/<app>/urls.py`.

### `generate_crud(app, model_name, fields, serializer_fields=None, read_only_fields=None, permission=..., project_root=None)`
**One-shot scaffold** — creates model + serializer + viewset + registers route.

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
| `float` | `FloatField` |
| `decimal` | `DecimalField` |
| `bool`, `boolean` | `BooleanField` |
| `date` | `DateField` |
| `datetime`, `timestamp` | `DateTimeField` |
| `time` | `TimeField` |
| `json` | `JSONField` |
| `uuid` | `UUIDField` |
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

> **Safety**: Only `SELECT`, `WITH`, and `EXPLAIN` statements are allowed.  Any other statement type returns an error without executing.

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
