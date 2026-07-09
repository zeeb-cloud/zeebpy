# Zeeb Recipes

Copy-paste task recipes with the exact, current tool calls. Every call takes a
trailing `project_id` (the host-assigned id) — shown once here, omitted in the
snippets below for brevity. Read `mcp://docs/principles` for the contract and
`mcp://docs/backend-generation` for the per-tool detail.

> Every call ends with `project_id="<id>"`. A missing id fails with
> `no_project_id`; an unknown id with `project_not_found`.

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
