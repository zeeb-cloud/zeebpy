# API Versioning

DRF-style API versioning: a configurable scheme determines the requested API
version for each request and makes it available as `request.state.version`,
`viewset.version`, or via the `get_api_version` dependency.

## Quick Start

```python
# settings.py
MIDDLEWARE = [
    "zeeb_api.middleware.CORSMiddleware",
    "zeeb_api.middleware.JWTAuthMiddleware",
    "zeeb_api.versioning.VersioningMiddleware",
]

DEFAULT_VERSIONING_CLASS = "zeeb_api.versioning.HeaderVersioning"
DEFAULT_VERSION = "1.0"
ALLOWED_VERSIONS = ["1.0", "2.0"]
```

```python
class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer

    async def retrieve(self, request, id=None):
        if self.version == "2.0":
            ...  # new behavior
        return await super().retrieve(request, id=id)
```

Requests with a version not in `ALLOWED_VERSIONS` receive a **400** response
with the standardized envelope and `error.code == "API_VERSION_INVALID"`.
If no version is supplied, `DEFAULT_VERSION` is used (the version is `None`
when that is unset too).

## Versioning Schemes

Configure one scheme via `DEFAULT_VERSIONING_CLASS` (dotted path).

### QueryParameterVersioning

```http
GET /articles/?version=2.0
```

The parameter name comes from `VERSION_PARAM` (default `"version"`).

### HeaderVersioning

```http
GET /articles/
X-API-Version: 2.0
```

The header name comes from `VERSION_HEADER` (default `"X-API-Version"`).

### AcceptHeaderVersioning

```http
GET /articles/
Accept: application/json; version=2.0
```

The version is read from the media-type parameters of the `Accept` header
(parameter name from `VERSION_PARAM`).

### URLPathVersioning

```http
GET /v1/articles/
GET /v2.1/articles/
```

The version is extracted from the URL path with the pattern
`/v<number>[.<number>]/`. Because the versioning middleware runs before
routing (where `request.path_params` is still empty), the path is matched
with a regex — no route parameter is required.

To actually serve multiple path versions, mount your routers under version
prefixes:

```python
from fastapi import FastAPI
from zeeb_api.routers import DefaultRouter

app = FastAPI()

v1 = DefaultRouter()
v1.register("articles", ArticleViewSetV1)

v2 = DefaultRouter()
v2.register("articles", ArticleViewSetV2)

for router in v1.get_urls():
    app.include_router(router, prefix="/v1")
for router in v2.get_urls():
    app.include_router(router, prefix="/v2")
```

## Installing the Middleware

`VersioningMiddleware` self-configures from settings, so it can be listed in
`MIDDLEWARE` (loaded without arguments) or added manually:

```python
from zeeb_api.versioning import VersioningMiddleware

app.add_middleware(VersioningMiddleware)
```

With no `DEFAULT_VERSIONING_CLASS` configured, the middleware sets
`request.state.version = None` and passes every request through.

## Accessing the Version

- **ViewSets**: `self.version` is populated by the router on every action.
- **Middleware/raw requests**: `request.state.version`.
- **Plain routes**: the `get_api_version` dependency. It returns
  `request.state.version` when the middleware is installed, and otherwise
  runs the configured scheme on demand (raising a 400
  `API_VERSION_INVALID` error for invalid versions):

```python
from fastapi import Depends
from zeeb_api.versioning import get_api_version


@app.get("/articles")
async def list_articles(version: str | None = Depends(get_api_version)):
    if version == "2.0":
        ...
```

## Custom Schemes

Subclass `BaseVersioning` and implement `extract_version()`:

```python
from zeeb_api.versioning import BaseVersioning


class SubdomainVersioning(BaseVersioning):
    def extract_version(self, request):
        host = request.headers.get("host", "")
        subdomain = host.split(".")[0]
        return subdomain if subdomain.startswith("v") else None
```

`determine_version()` handles the rest: it falls back to `default_version`
when `extract_version()` returns `None` and raises `InvalidVersionError` for
versions not in `allowed_versions` (the default version is always allowed).

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `DEFAULT_VERSIONING_CLASS` | `None` | Dotted path to the versioning scheme (None disables versioning) |
| `DEFAULT_VERSION` | `None` | Version used when the request does not specify one |
| `ALLOWED_VERSIONS` | `None` | List of accepted versions (None accepts any) |
| `VERSION_PARAM` | `"version"` | Query/media-type parameter name |
| `VERSION_HEADER` | `"X-API-Version"` | Header name for `HeaderVersioning` |
