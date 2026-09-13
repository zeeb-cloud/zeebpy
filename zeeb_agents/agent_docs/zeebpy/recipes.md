# Zeeb Recipes

Copy-paste task recipes with the exact, current tool calls. Every call takes a
trailing `project_id` (the host-assigned id) — shown once here, omitted in the
snippets below for brevity. Read `mcp://docs/principles` for the contract and
`mcp://docs/backend-generation` for the per-tool detail.

> Every call ends with `project_id="<id>"`. A missing id fails with
> `no_project_id`; an unknown id with `project_not_found`.

---

## What a new project already has

`{prefix}create_project` does not produce an empty skeleton. It ships a working,
authenticated API, so do not rebuild any of this:

- `apps/accounts` with the project's user model (`accounts.User`, table
  `accounts_user`) already set as `AUTH_USER_MODEL`. **Never call
  `{prefix}create_user_model` on a fresh project** — add fields to the existing
  model. Point relations at `"accounts.User"`.
- Auth endpoints served under `API_PREFIX`: `POST /auth/register`,
  `/auth/login`, `/auth/refresh`, `/auth/logout`, `GET /auth/me`.
  `{prefix}setup_auth` reports `already_wired` on a fresh project — that is
  success, not a failure.
- CORS, JWT middleware, rate limiting, API versioning, `/health` + `/ready`,
  the standard error envelope, rotating logs, and a per-project `SECRET_KEY`
  in `.env`.
- OAuth wired but dormant until a provider client id is configured
  (`{prefix}setup_oauth`).

Configuration is environment-driven: prefer `{prefix}set_env` for values that
already have a variable, and `{prefix}manage_settings` for the setting itself.
Still required before the server starts: `{prefix}make_migrations` then
`{prefix}run_migrations`.

---

## Start here: the intent recipes

The recipes below the divider drive one object at a time (the `advanced` tier).
Reach for them when a change is too specific for a spec — not as the default.
The calls here cover most work, and each one plans, executes, and verifies a
whole outcome:

### Build a feature from a spec

```
{prefix}build_feature(spec={
    "name": "blog",
    "entities": [
        {"name": "Post", "fields": [
            {"name": "title", "type": "string", "max_length": 200},
            {"name": "body", "type": "text"},
            {"name": "author", "type": "relation", "target": "User",
             "cardinality": "many-to-one", "required": False},
        ]},
    ],
    "api": {"authentication": "read_only_public"},
})
```

One call: the app, models (relation targets first), serializers, endpoints,
routes, migrations, smoke tests, and the verification chain. Re-running it with
an EXTENDED spec adds the new fields to the existing models; it never removes
anything — fields the spec dropped come back as `data["drift"]` with a
ready-to-send `change_feature` payload.

### Review before writing

```
plan = {prefix}plan_feature(spec={...})     # writes nothing
{prefix}apply_plan(plan=plan)               # same compiler, same executor
```

Use this when the change is risky enough to read first: `plan["risk"]` reports
`low` / `medium` / `high` (high = an entity gets dropped) and
`plan["operations"]` is the exact ordered list.

### Evolve what exists

```
{prefix}change_feature(changes=[
    {"operation": "add_field", "entity": "Post",
     "field": {"name": "subtitle", "type": "string", "required": False}},
    {"operation": "alter_field", "entity": "Post",
     "field": {"name": "body", "type": "text"}},
    {"operation": "set_authentication", "entity": "Post",
     "authentication": "required"},
])
```

Semantic changes, not file edits. `alter_field` rewrites a definition in place
(restate every option) instead of the remove+add that drops the column and its
data. Migrations run only when a change actually touches the schema.

Pass `feature="blog"` to attribute the changes to a recorded feature: it
defaults the app and keeps the feature's stored spec current.

### Declare custom logic in the spec

```
{prefix}build_feature(spec={
    "name": "shop",
    "entities": [
        {"name": "Order",
         "fields": [{"name": "total", "type": "decimal"}],
         "functions": [
             {"name": "close", "kind": "action", "detail": True,
              "methods": ["post"], "actor": "admin",
              "body": "return {\"ok\": True}"},
         ]},
    ],
    "functions": [
        {"name": "revenue", "kind": "endpoint", "path": "/orders/revenue",
         "method": "get", "body": "return {\"total\": 0}"},
        {"name": "nightly_rollup", "kind": "task", "schedule": "0 2 * * *"},
    ],
})
```

Business logic stays inside the spec. `kind` is `action` (a method on the
entity's endpoint), `endpoint` (a standalone route), `hook` (a model lifecycle
receiver), `task` (a background job), or `rule` (a permission class). See
`mcp://docs/backend-generation` for every key.

### Manage a feature after it is built

```
{prefix}list_features()                              # what exists, and its status
{prefix}deactivate_feature(feature="blog")           # off the API, data intact
{prefix}activate_feature(feature="blog")             # back, exactly as it was
{prefix}delete_feature(feature="blog", confirm=True) # gone, tables dropped
```

`deactivate_feature` archives the API layer and **never touches `models.py` or
the tables** — no migration is generated and activating restores the code
verbatim, hand edits included. `delete_feature` is the destructive one and
refuses without `confirm=True`, reporting the exact scope in
`data["would_delete"]` so the decision is informed.

Features may share an app. Ownership is recorded per artifact, so archiving one
leaves its app-mates serving.

### Diagnose, fix, verify

```
{prefix}diagnose_problem(symptom="POST /api/posts returns 500",
                         endpoint="/api/posts")
# → root_cause + recommended_fix; run the fix it names, then:
{prefix}verify_project()
```

`verify_project` is the acceptance gate: never report work as done unless it
passes. `data["verified"]` is the verdict; an empty test suite FAILS the tests
check (no tests collected is zero evidence).

---

## Recipe: a CRUD resource, end to end

```
{prefix}create_app(name="blog")
{prefix}generate_crud(app="blog", model_name="Post", fields=[
    {"name": "title", "type": "CharField", "max_length": 200},
    {"name": "body", "type": "TextField"},
    {"name": "published", "type": "BooleanField", "default": False},
])
{prefix}make_migrations()
{prefix}run_migrations()
```

`generate_crud` writes the model, serializer, and ViewSet **and** registers the
route (URL segment defaults to the app name). Then apply the schema.

## Recipe: build the pieces by hand (more control)

```
{prefix}create_model(app="blog", model_name="Post", fields=[
    {"name": "title", "type": "CharField", "max_length": 200},
])
{prefix}create_serializer(app="blog", model_name="Post")
{prefix}create_viewset(app="blog", model_name="Post", permission="IsAuthenticated")
{prefix}register_route(app="blog", model_name="Post", url_prefix="posts")
{prefix}make_migrations()
{prefix}run_migrations()
```

`create_viewset` writes the ViewSet only — `register_route` mounts it (override
the segment with `url_prefix=`).

## Recipe: a custom action on a ViewSet

```
{prefix}add_viewset_action(
    app="blog", model_name="Post", action_name="publish",
    detail=True, methods=["post"],
    response_serializer="PostSerializer", permission="IsAdminUser",
    body="""
        post = await self.get_object()
        post.published = True
        await post.save()
        return PostSerializer(post).data
    """,
)
```

Read the validated request body inside an action with
`self.get_action_request_body()` (there is no `request.data`).

## Recipe: a standalone function endpoint

```
{prefix}create_route(
    app="blog", path="/posts/{post_id}/stats", method="get",
    function_name="post_stats",
    body="""
        return {"post_id": post_id, "views": 0}
    """,
)
```

Path `{segments}` become typed handler params; the handler always gets
`request: Request`. The route is auto-wired into `urls.py`.

## Recipe: add auth

```
{prefix}setup_auth(enable_registration=True, url_prefix="/auth")
{prefix}create_user(email="admin@example.com", password="s3cret", is_superuser=True)
```

`setup_auth` is idempotent (re-running reports `already_wired=True`). Secrets
come from settings/env — set them with `{prefix}set_env`.

## Recipe: seed sample data

```
{prefix}generate_seed_script(app="blog", models=["Post"], count=20)
```

Writes a runnable Python seed script (it does not insert rows itself). Pass
several models at once with `models=["A", "B"]`.

## Recipe: preview the running API

```
{prefix}get_project_reference()   # preview_url, runtime_api_base_url, openapi_url
{prefix}get_openapi_url()         # the live OpenAPI URL to build a client against
```

The preview runtime is always-on and platform-managed — you do not start a dev
server. If these fail with `runtime_not_configured`, the platform has not
published the URLs for this project yet.

## Recipe: inspect what exists

```
{prefix}list_models()                        # models across all apps
{prefix}list_endpoints()                      # registered ViewSet routes
{prefix}get_migration_status()                # applied vs pending
{prefix}describe_table(table_name="blog_post")
{prefix}run_query(sql="SELECT COUNT(*) FROM blog_post")   # read-only
```
