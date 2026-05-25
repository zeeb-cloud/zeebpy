# Frontend Integration Guide

How a frontend application (SPA, mobile, or 3rd-party client) integrates
with a Zeeb BaaS and how to generate the artefacts it needs.

> **Tool name prefix**: Tool calls below use `{prefix}` as a placeholder.
> It is replaced with the prefix your MCP server registered these tools under
> (e.g. `zeeb_`, `myapp_`, or empty string).

## 1 — Configure CORS

The very first step when a frontend on a different origin calls the API:

```
{prefix}configure_cors(
    origins=["https://myapp.vercel.app", "http://localhost:5173"],
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_credentials=True,
)
```

Then register the middleware in `settings.py`:
```python
MIDDLEWARE = [
    "zeeb_api.middleware.CORSMiddleware",
    # ... other middleware
]
```

Read current CORS settings:
```
{prefix}get_cors_config()
# result.data["cors"]["CORS_ALLOW_ORIGINS"] == [...]
```

## 2 — Authentication

Zeeb API ships with JWT authentication out of the box.

**Obtain tokens** — `POST /auth/token/`:
```json
{ "email": "user@example.com", "password": "secret" }
```
Response:
```json
{ "access": "<jwt_access_token>", "refresh": "<jwt_refresh_token>" }
```

**Use the token**:
```http
Authorization: Bearer <jwt_access_token>
```

**Refresh** — `POST /auth/token/refresh/`:
```json
{ "refresh": "<jwt_refresh_token>" }
```

The auth URLs are registered automatically when `zeeb_api.auth` is in `INSTALLED_APPS`.

### Create Initial Users

```
{prefix}create_user(email="admin@example.com", password="admin123", is_superuser=True)
{prefix}create_user(email="user@example.com",  password="user123")
```

## 3 — OpenAPI / Swagger Documentation

The live server exposes `/docs` (Swagger UI) and `/openapi.json` automatically.
To save the spec for client SDK generation:

```
# Dev server must be running (zeeb runserver)
{prefix}export_openapi(port=8000, output_path="openapi.json")
```

The saved `openapi.json` can be used with:
- **openapi-generator** to generate typed clients (TypeScript, Swift, Dart…)
- **Speakeasy**, **Stainless**, or **fern** for SDK generation
- **Postman** / **Insomnia** for API testing collections

## 4 — Data Shapes (JSON Schema)

Get the exact shape of any model for frontend form generation or validation:

```
{prefix}get_model_json_schema(app="blog", model_name="Post")
# result.data["schema"] ==
# {
#   "title": "Post",
#   "type": "object",
#   "properties": {
#     "id":         {"type": "integer", "readOnly": true},
#     "title":      {"type": "string", "maxLength": 200},
#     "slug":       {"type": "string"},
#     "body":       {"type": "string"},
#     "published":  {"type": "boolean"},
#     "created_at": {"type": "string", "format": "date-time"}
#   },
#   "required": ["title", "slug", "body"]
# }
```

Use this schema directly with:
- **Zod** (TypeScript): `z.object({...})`
- **Yup** (React Hook Form)
- **Ajv** for runtime JSON validation
- **react-jsonschema-form** for auto-generated forms

## 5 — Route Inventory

Discover all available endpoints without a running server:

```
{prefix}list_all_routes()
# result.data["routes"] == [
#   {"app": "blog", "type": "viewset", "prefix": "posts",  "viewset": "PostViewSet"},
#   {"app": "blog", "type": "route",   "method": "GET",    "path": "/posts/featured"},
#   {"app": "users","type": "viewset", "prefix": "users",  "viewset": "UserViewSet"},
# ]
```

Standard ViewSet `{prefix}` expands to these REST endpoints:
| Method | Path | Action |
|---|---|---|
| GET    | `/{route_prefix}/`      | list |
| POST   | `/{route_prefix}/`      | create |
| GET    | `/{route_prefix}/{id}/` | retrieve |
| PUT    | `/{route_prefix}/{id}/` | update |
| PATCH  | `/{route_prefix}/{id}/` | partial_update |
| DELETE | `/{route_prefix}/{id}/` | destroy |

## 6 — Health / Status Endpoints

Add health probes that frontend monitoring dashboards or load balancers use:

```
{prefix}create_health_endpoint()
# Creates health.py with GET /health and GET /ready
```

After registering the router:
```
GET /health  →  {"status": "ok"}                       # 200 always
GET /ready   →  {"status": "ready", "db": "ok"}        # 200 / 503
```

## 7 — Real-Time Considerations

Zeeb does not yet ship a built-in WebSocket layer.  For real-time features:

- Use **polling** against REST endpoints (simplest)
- Add **Broadcaster** + **starlette-websockets** manually for pub/sub
- Use an external managed service (Pusher, Ably, Supabase Realtime)

Scaffold a WebSocket endpoint stub:
```
{prefix}create_route(app="chat", path="/ws/messages", method="websocket", function_name="ws_messages_handler")
```

## 8 — Environment Configuration

Set frontend-relevant settings via env:

```
{prefix}set_env(key="FRONTEND_URL",                        value="https://myapp.vercel.app")
{prefix}set_env(key="JWT_ACCESS_TOKEN_LIFETIME_MINUTES",   value="60")
{prefix}set_env(key="JWT_REFRESH_TOKEN_LIFETIME_DAYS",     value="7")
```
