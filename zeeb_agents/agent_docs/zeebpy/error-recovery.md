# Error Recovery

When a tool returns `success=False`, `data["error_code"]` (when present) tells
you *why* in a closed vocabulary, and often `data["suggestions"]` (close-match
candidates, also echoed in `message`). Use this table to turn a failure into the
next call. See `mcp://docs/principles` for the full `AgentResult` contract.

> These are **tool** errors (this layer), not the running API's HTTP errors.
> Never `try/except` a tool — inspect `success` and `error_code`.

## Project addressing

| `error_code` | Meaning | What to do |
|---|---|---|
| `no_project_id` | You did not pass `project_id`. | Pass the host-assigned `project_id` on the call. |
| `project_not_found` | No project is registered under that id (or its files are missing). | Check the id; create it with `{prefix}create_project` if it does not exist yet. |

## Input validation

| `error_code` | Meaning | What to do |
|---|---|---|
| `invalid_identifier` | A name is not a valid Python identifier. | Use `[A-Za-z_][A-Za-z0-9_]*` (no spaces/hyphens). |
| `invalid_input` | A generic argument was rejected. | Read `message`; fix the offending argument. |
| `invalid_field_type` | Unknown field type. | Use a supported type (see `suggestions`, e.g. `CharField`). |
| `invalid_field_spec` | A field/relation spec is malformed. | Relations need `"to"`; `on_delete="SET_NULL"` needs `null=True`; M2M rejects `on_delete`/`null`. |
| `invalid_meta` | A `class Meta` option is invalid. | Fix the `meta`/`meta_changes` keys. |
| `invalid_permission` | Unknown permission class. | Use a valid class (see `suggestions`), e.g. `IsAuthenticated`. |
| `invalid_authentication` | Unknown authentication class. | Use a valid class (see `suggestions`), e.g. `JWTAuthentication`. |
| `invalid_regex` | A search/pattern regex did not compile. | Fix the regex passed to `search_code`/`search_logs`. |
| `invalid_sql` | `run_query` rejected the SQL. | Read-only only: single `SELECT`/`WITH`/`EXPLAIN`, no mutations. |

## Not found

| `error_code` | Meaning | What to do |
|---|---|---|
| `app_not_found` | No such app. | `{prefix}list_apps()`; create it with `{prefix}create_app`. |
| `model_not_found` | No such model. | `{prefix}list_models()`; check the `model_name`. |
| `field_not_found` | No such field on the model. | Inspect the model; check the `field_name`. |
| `table_not_found` | No such database table. | `{prefix}list_tables()`; run migrations if the model is new. |
| `user_not_found` | No user matches. | `{prefix}list_users()`; check the email/id. |
| `function_not_found` | No such signal receiver / function. | `{prefix}list_signal_receivers(app=...)`. |
| `setting_not_found` | No such settings key. | `{prefix}get_settings()` to see available keys. |
| `env_key_not_found` | No such `.env` variable. | `{prefix}get_env()`; set it with `{prefix}set_env`. |
| `file_not_found` | Path does not exist in the project. | `{prefix}list_files(...)`; check the path. |
| `log_file_not_found` | No log file to read. | The app may not have logged yet; check `data["searched_in"]`. |
| `no_user_table` | The auth/user table is not set up. | Run `{prefix}setup_auth` and migrations first. |

## State / conflicts

| `error_code` | Meaning | What to do |
|---|---|---|
| `already_exists` | The thing you are creating already exists. | Use the update/edit tool instead, or a different name. |
| `dependency_missing` | An optional dependency (e.g. `zeebpy[oauth]`) is not installed. | Install the extra named in `message`. |

## Security boundaries

| `error_code` | Meaning | What to do |
|---|---|---|
| `outside_project_root` | A file path escaped the project. | Use a path inside the project (no `../` escapes). |
| `permission_denied` | Filesystem permission error. | Fix permissions / target a writable path. |

## Runtime

| `error_code` | Meaning | What to do |
|---|---|---|
| `runtime_not_configured` | The platform has not published preview/OpenAPI URLs yet. | Retry later; the preview runtime becomes live asynchronously. |
| `server_not_reachable` | A health check could not reach the API. | The platform manages the runtime; verify via `{prefix}check_system_health`. |
