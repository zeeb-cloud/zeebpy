# Frontend Integration Guide

How a frontend application (SPA, mobile, or 3rd-party client) integrates
with a Zeeb BaaS and how to generate the artefacts it needs.

## 1 — Configure CORS

The very first step when a frontend on a different origin calls the API:

```python
from zeeb_agents import configure_cors

await configure_cors(
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
```python
from zeeb_agents import get_cors_config
result = await get_cors_config()
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

```python
from zeeb_agents import create_user

await create_user("admin@example.com", "admin123", is_superuser=True)
await create_user("user@example.com",  "user123")
```

## 3 — OpenAPI / Swagger Documentation

The live server exposes `/docs` (Swagger UI) and `/openapi.json`
automatically.  To save the spec for client SDK generation:

```python
from zeeb_agents import export_openapi

# Dev server must be running (zeeb runserver)
await export_openapi(port=8000, output_path="openapi.json")
```

The saved `openapi.json` can be used with:
- **openapi-generator** to generate typed clients (TypeScript, Swift, Dart…)
- **Speakeasy**, **Stainless**, or **fern** for SDK generation
- **Postman** / **Insomnia** for API testing collections

## 4 — Data Shapes (JSON Schema)

Get the exact shape of any model for frontend form generation or validation:

```python
from zeeb_agents import get_model_json_schema

result = await get_model_json_schema("blog", "Post")
schema = result.data["schema"]
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

```python
from zeeb_agents import list_all_routes

result = await list_all_routes()
# result.data["routes"] == [
#   {"app": "blog", "type": "viewset", "prefix": "posts", "viewset": "PostViewSet"},
#   {"app": "blog", "type": "route",   "method": "GET",   "path": "/posts/featured"},
#   {"app": "users","type": "viewset", "prefix": "users", "viewset": "UserViewSet"},
# ]
```

Standard ViewSet `{prefix}` expands to these REST endpoints:
| Method | Path | Action |
|---|---|---|
| GET    | `/{prefix}/`     | list |
| POST   | `/{prefix}/`     | create |
| GET    | `/{prefix}/{id}/`| retrieve |
| PUT    | `/{prefix}/{id}/`| update |
| PATCH  | `/{prefix}/{id}/`| partial_update |
| DELETE | `/{prefix}/{id}/`| destroy |

## 6 — Health / Status Endpoints

Add health probes that frontend monitoring dashboards or load balancers use:

```python
from zeeb_agents import create_health_endpoint

await create_health_endpoint()
# Creates health.py with GET /health and GET /ready
```

Register the router in your main app and then:
```
GET /health  →  {"status": "ok"}                       # 200 always
GET /ready   →  {"status": "ready", "db": "ok"}        # 200 / 503
```

## 7 — Real-Time Considerations

Zeeb does not yet ship a built-in WebSocket layer.  For real-time features:

- Use **polling** against REST endpoints (simplest)
- Add **Broadcaster** + **starlette-websockets** manually for pub/sub
- Use an external managed service (Pusher, Ably, Supabase Realtime)

The `create_route` function can scaffold a WebSocket endpoint stub:
```python
from zeeb_agents import create_route

await create_route("chat", "/ws/messages", "websocket", "ws_messages_handler")
```

## 8 — Environment Configuration

Set frontend-relevant settings via env:

```python
from zeeb_agents import set_env, get_env

await set_env("FRONTEND_URL", "https://myapp.vercel.app")
await set_env("JWT_ACCESS_TOKEN_LIFETIME_MINUTES", "60")
await set_env("JWT_REFRESH_TOKEN_LIFETIME_DAYS", "7")
```
