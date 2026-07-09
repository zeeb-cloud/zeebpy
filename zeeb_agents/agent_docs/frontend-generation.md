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

**Obtain tokens** — `POST /auth/login`:
```json
{ "email": "user@example.com", "password": "secret" }
```
Response:
```json
{ "access_token": "<jwt>", "refresh_token": "<jwt>", "token_type": "bearer", "expires_in": 900 }
```

**Use the token**:
```http
Authorization: Bearer <jwt_access_token>
```

**Refresh** — `POST /auth/refresh`:
```json
{ "refresh_token": "<jwt_refresh_token>" }
```

Also available: `POST /auth/logout`, `GET /auth/me`, and (when registration is
enabled) `POST /auth/register`.

The auth URLs are registered automatically when `zeeb_api.auth` is in `INSTALLED_APPS`.

### Create Initial Users

```
{prefix}create_user(email="admin@example.com", password="admin123", is_superuser=True)
{prefix}create_user(email="user@example.com",  password="user123")
```

## 3 — Handling API Errors

**Every** error the API returns — validation, auth, permissions, 404s,
throttling, crashes — uses one standardized envelope. Generated frontend code
must branch on `error.code` (stable, machine-readable), never on `message`
(English debug text):

```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": [
      {"code": "FIELD_REQUIRED", "field": "email", "message": "Field required",
       "meta": {"input": null}}
    ],
    "meta": {"request_id": "…", "timestamp": "…", "path": "/api/users/", "method": "POST"}
  }
}
```

Codes a generated client must handle:

| `error.code` | Status | Client behaviour |
|---|---|---|
| `AUTH_TOKEN_EXPIRED` | 401 | `POST /auth/refresh` with the refresh token, then retry once. |
| `AUTH_TOKEN_MISSING` / `AUTH_TOKEN_INVALID` / `AUTH_INVALID_CREDENTIALS` | 401 | Clear stored tokens; send the user to login. |
| `PERM_DENIED` (and other `PERM_*`) | 403 | Show a "no access" state — do not retry. |
| `VALIDATION_ERROR` | 400/422 | Map `error.details[]` onto form fields via each detail's `field` (dotted for nested, `null` = non-field) and `code` (`FIELD_REQUIRED`, `FIELD_TOO_SHORT`, `FIELD_INVALID_EMAIL`, …); `meta` carries constraint context (`min_length`, `input`, …). |
| `RESOURCE_NOT_FOUND` | 404 | Show not-found state. |
| `RESOURCE_CONFLICT` / `RESOURCE_ALREADY_EXISTS` | 409 | Surface the conflict (e.g. duplicate). |
| `RATE_LIMIT_EXCEEDED` | 429 | Back off for the `Retry-After` header value (seconds) before retrying. |
| `SERVER_*` | 5xx | Generic error state; safe to retry idempotent requests with backoff. |

Since `error.code` values are stable identifiers, key your i18n translations on
them. `error.meta.request_id` echoes an `X-Request-ID` header when the client
sends one — include it in support/error reports to correlate with server logs.
The full code taxonomy is in the project docs (`docs/api/errors.md`).

## 4 — OpenAPI / Swagger Documentation

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

## 5 — Data Shapes (JSON Schema)

Get the exact shape of any model for frontend form generation or validation:

```
{prefix}get_model_json_schema(app="blog", model="Post")
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

## 6 — Route Inventory

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
| POST   | `/{route_prefix}/query` | list (Q-filter body, `{}` for all) |
| POST   | `/{route_prefix}`       | create |
| GET    | `/{route_prefix}/{id}`  | retrieve |
| PUT    | `/{route_prefix}/{id}`  | update |
| PATCH  | `/{route_prefix}/{id}`  | partial_update |
| DELETE | `/{route_prefix}/{id}`  | destroy |

**Path conventions:** canonical paths have **no trailing slash** — generated
clients must copy paths verbatim from `/openapi.json`, never generalize a
pattern from one endpoint to another. There is no `GET` list endpoint; lists go
through `POST /{route_prefix}/query`. (The backend also serves every route with
a trailing slash appended — no redirect — so a client that adds one still
works, but the slash-less form is canonical.)

## 7 — Health / Status Endpoints

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

## 8 — Real-Time Considerations

Zeeb does not yet ship a built-in WebSocket layer.  For real-time features:

- Use **polling** against REST endpoints (simplest)
- Add **Broadcaster** + **starlette-websockets** manually for pub/sub
- Use an external managed service (Pusher, Ably, Supabase Realtime)

`{prefix}create_route` only emits HTTP handlers (`get`/`post`/`put`/`patch`/
`delete`) — WebSockets are not one of its methods. A WebSocket route is one of
the few cases with no dedicated tool, so write the module yourself with
`{prefix}write_file` (e.g. `apps/chat/ws.py`) and include its FastAPI
`APIRouter` from the project `urls.py`.

## 9 — Environment Configuration

Set frontend-relevant settings via env:

```
{prefix}set_env(key="FRONTEND_URL",                        value="https://myapp.vercel.app")
{prefix}set_env(key="JWT_ACCESS_TOKEN_LIFETIME_MINUTES",   value="60")
{prefix}set_env(key="JWT_REFRESH_TOKEN_LIFETIME_DAYS",     value="7")
```
