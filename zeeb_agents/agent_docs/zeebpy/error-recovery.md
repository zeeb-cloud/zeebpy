# Error Recovery

When a tool returns `success=False`, `data["error_code"]` (when present) tells
you *why* in a closed vocabulary, and often `data["suggestions"]` (close-match
candidates, also echoed in `message`). Use this table to turn a failure into the
next call. See `mcp://docs/principles` for the full `AgentResult` contract.

> These are **tool** errors (this layer), not the running API's HTTP errors.
> Never `try/except` a tool — inspect `success` and `error_code`.

## The failure envelope

A failed call's `data` carries more than the code:

| key | meaning |
|---|---|
| `error_code` | Why it failed, from the closed vocabulary below. |
| `recoverable` | `true` when a corrected argument or a preparatory call in this session can succeed; `false` for context faults (no project id, unknown project, runtime not configured, filesystem permission) that need the platform or the user. |
| `suggestions` | Close-match candidates for the rejected input (also echoed in `message`). |
| `problems` | For spec/change compilation: EVERY problem at once, each `{"path", "code", "message", "suggestions"?}` — fix them in one pass instead of one call per problem. |
| `affected` | `{"apps", "entities", "files"}` the call was touching. |
| `state_changed` | Whether anything was written. Set on **every** failure except `partial_failure` (which reports its own progress), so you never have to infer "did this leave the project half-changed?" from a missing key. |

`state_changed` has **three** states, not two:

| value | meaning |
|---|---|
| `false` | Nothing was written. The failure was caught before any edit — fix the argument and re-run. |
| `true` | Something was written. Only `partial_failure` reports this; use `steps_completed` / `remaining_operations` to resume. |
| `null` | **Unknown.** An unanticipated exception escaped, so the tool genuinely cannot say. Treat it as "possibly written" — re-running is the documented recovery, because writes are idempotent. |

Do not write `if not state_changed:` — that treats `null` as "nothing
happened", which is exactly the case where it isn't safe to assume that.

### Unexpected failures

An exception the tool did not anticipate still returns a structured envelope
rather than a bare message:

| key | meaning |
|---|---|
| `error_type` | The Python exception class name (there is no `error_code` — the closed vocabulary only covers anticipated failures). |
| `recoverable` | Always `true`: re-running is the documented recovery. |
| `state_changed` | Always `null` — see above. |
| `next_actions` | Ordered follow-ups, starting with "re-run the same call". |

The full traceback is logged to the `zeeb_agents` logger, not returned.

### Partial failure of an intent call

`build_feature` / `apply_plan` / `change_feature` execute a plan step by step.
When some steps land and one fails, the call returns `success=False` with
`error_code="partial_failure"`, `recoverable=true`, and the metadata to resume:

| key | meaning |
|---|---|
| `steps_completed` | Human-readable log of what did run. |
| `failed_operations` | `[{"index", "op", "error", "error_code", "recoverable"}]` — which operation failed, and where in the plan. |
| `completed_count` / `total_count` | Progress through the plan. |
| `remaining_operations` | Operations that never ran (a migration failure stops the run). |
| `state_changed` | Whether anything was written at all. |

Recovery: fix the cause named by `failed_operations[0]`, then **re-run the same
call** — completed steps are skip-idempotent, so it resumes rather than
duplicating work.

### Verification is a verdict, not a failure

A failed verification never flips `success`, because `success=False` means
"incomplete — re-run to resume" and an agent would loop on an already-applied
build. Check `data["verified"]` instead: the message says `BUILT BUT
VERIFICATION FAILED: <checks>`, `verification.checks` has the detail (including
the failing test node ids), and `next_actions` says what to do.

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
| `feature_not_found` | No such feature. | `{prefix}list_features()`; the known names are in `data["known_features"]`. |
| `setting_not_found` | No such settings key. | `{prefix}get_settings()` to see available keys. |
| `env_key_not_found` | No such `.env` variable. | `{prefix}get_env()`; set it with `{prefix}set_env`. |
| `file_not_found` | Path does not exist in the project. | `{prefix}list_files(...)`; check the path. |
| `log_file_not_found` | No log file to read. | The app may not have logged yet; check `data["searched_in"]`. |
| `no_user_table` | The auth/user table is not set up. | Run `{prefix}setup_auth` and migrations first. |

## State / conflicts

| `error_code` | Meaning | What to do |
|---|---|---|
| `already_exists` | The thing you are creating already exists. | Use the update/edit tool instead, or a different name. |
| `prefix_conflict` | The route prefix is already taken by a *different* ViewSet — a second registration would be silently shadowed. | Re-issue with an explicit `url_prefix=` for a distinct segment. |
| `dependency_missing` | An optional dependency (e.g. `zeebpy[oauth]`) is not installed. | Install the extra named in `message`. |
| `feature_archived` | The feature's code is out of the tree, so the change would apply to nothing. | `{prefix}activate_feature(feature=...)` first. |
| `feature_active` | The feature is already in the tree. | Nothing to restore; `{prefix}deactivate_feature(feature=...)` is the other direction. |
| `archive_missing` | Neither an archive nor a stored spec to restore the feature from. | `{prefix}build_feature(spec=...)` to recreate it. |

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
