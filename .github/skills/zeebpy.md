# Zeeb Framework — LLM Skill Guide

Zeeb (`zeebpy`) is a Django-like async Python framework with two packages:

- **`zeeb_orm`** — ORM built on SQLAlchemy with Django-style API (models, fields, QuerySet, Q/F, migrations)
- **`zeeb_api`** — REST API framework built on FastAPI with DRF-style patterns (serializers, viewsets, routers, JWT auth, permissions)

Requires Python ≥ 3.10. Everything is **async-first**.

---

## Project Structure

Zeeb projects follow Django conventions:

```
myproject/
├── manage.py                    # CLI entry point
├── myproject/
│   ├── __init__.py
│   ├── settings.py              # Configuration (DATABASE, INSTALLED_APPS, etc.)
│   ├── urls.py                  # Root URL routing
│   └── asgi.py                  # ASGI application factory
├── apps/
│   └── myapp/
│       ├── __init__.py
│       ├── models.py            # ORM models
│       ├── serializers.py       # API serializers
│       ├── views.py             # ViewSets
│       └── urls.py              # App routes
└── migrations/
    └── versions/                # Alembic migration files
```

Create projects/apps with CLI:

```bash
zeeb startproject myproject
zeeb-manage startapp myapp
```

---

## ORM — Models

Import from `zeeb_orm`. Models inherit from `Model` and get a UUID primary key by default.

```python
from zeeb_orm import Model, fields

class Post(Model):
    title = fields.CharField(max_length=200)
    content = fields.TextField()
    published = fields.BooleanField(default=False)
    author = fields.ForeignKey('User', on_delete='CASCADE', related_name='posts')
    tags = fields.ManyToManyField('Tag', related_name='posts')
    created_at = fields.DateTimeField(auto_now_add=True)
    updated_at = fields.DateTimeField(auto_now=True)

    class Meta:
        table_name = 'posts'
        ordering = ['-created_at']
        indexes = [
            Index(fields=['title']),
        ]
        constraints = [
            UniqueConstraint(fields=['title', 'author']),
            CheckConstraint(check='char_length(title) > 0', name='title_not_empty'),
        ]
        unique_together = [('slug', 'author')]
```

### Available Field Types

| Field | Notes |
|-------|-------|
| `AutoField`, `BigAutoField` | Integer auto-increment PKs |
| `UUIDField` | UUID (default PK type is `UUIDAutoField`) |
| `CharField(max_length=N)` | String with max length |
| `TextField` | Unlimited text |
| `EmailField`, `SlugField`, `URLField` | Validated string types |
| `IntegerField`, `SmallIntegerField`, `BigIntegerField`, `PositiveIntegerField` | Integer variants |
| `FloatField` | Float |
| `DecimalField(max_digits, decimal_places)` | Fixed precision |
| `BooleanField` | Boolean |
| `DateField`, `TimeField`, `DateTimeField` | Temporal types |
| `JSONField` | JSON data |
| `ForeignKey(Model, on_delete=...)` | FK relationship |
| `OneToOneField(Model)` | 1:1 relationship |
| `ManyToManyField(Model)` | M2M relationship |

### Field Options

All fields accept: `null`, `blank`, `default`, `primary_key`, `unique`, `index`, `db_column`, `choices`, `validators`.

`DateTimeField` also accepts: `auto_now` (update on save), `auto_now_add` (set on create).

`ForeignKey` also accepts: `on_delete` (`'CASCADE'`, `'SET_NULL'`, `'PROTECT'`, etc.), `related_name`.

---

## ORM — QuerySet API

Every model has an `objects` manager. QuerySet methods are chainable and lazy — they build SQL and execute only when awaited.

### CRUD

```python
# Create
post = await Post.objects.create(title='Hello', content='World')

# Read
post = await Post.objects.get(id=some_uuid)          # raises if 0 or >1 match
posts = await Post.objects.filter(published=True)     # returns list
all_posts = await Post.objects.all()

# Update
await Post.objects.filter(id=some_uuid).update(title='Updated')
post.title = 'Updated'
await post.save()

# Delete
await Post.objects.filter(published=False).delete()
await post.delete()

# Get or create
post, created = await Post.objects.get_or_create(
    title='Hello', defaults={'content': 'World'}
)

# Update or create
post, created = await Post.objects.update_or_create(
    title='Hello', defaults={'content': 'New content'}
)

# Bulk create
posts = await Post.objects.bulk_create([
    Post(title='A'), Post(title='B'), Post(title='C')
])
```

### Filtering & Lookups

```python
# Keyword lookups (double-underscore syntax)
Post.objects.filter(age__gt=18)              # >
Post.objects.filter(age__gte=18)             # >=
Post.objects.filter(age__lt=65)              # <
Post.objects.filter(age__lte=65)             # <=
Post.objects.filter(name__contains='john')   # LIKE %john%
Post.objects.filter(name__icontains='john')  # case-insensitive
Post.objects.filter(name__startswith='J')    # LIKE J%
Post.objects.filter(name__endswith='son')    # LIKE %son
Post.objects.filter(email__iexact='A@B.COM') # case-insensitive exact
Post.objects.filter(status__in=['active', 'pending'])
Post.objects.filter(age__range=(18, 65))
Post.objects.filter(deleted_at__isnull=True)
Post.objects.filter(name__regex=r'^J.*n$')
```

### Q Objects — Complex Filters

```python
from zeeb_orm import Q

# AND
posts = await Post.objects.filter(Q(published=True) & Q(author_id=user_id))

# OR
posts = await Post.objects.filter(Q(role='admin') | Q(role='moderator'))

# NOT
posts = await Post.objects.filter(~Q(deleted=True))

# Combined
posts = await Post.objects.filter(
    Q(age__gte=18) & (Q(name__startswith='A') | Q(name__startswith='B'))
)
```

### F Expressions — Field References

```python
from zeeb_orm import F

# Compare fields to each other
Post.objects.filter(likes__gt=F('dislikes'))

# Arithmetic in updates
await Post.objects.update(view_count=F('view_count') + 1)

# Complex expressions
Post.objects.filter(score__gt=F('likes') - F('dislikes'))
```

### Ordering, Slicing, Distinct

```python
Post.objects.order_by('name')               # ASC
Post.objects.order_by('-created_at')         # DESC
Post.objects.order_by('last_name', 'first_name')

Post.objects.all()[:10]                      # LIMIT 10
Post.objects.all()[5:15]                     # OFFSET 5 LIMIT 10

Post.objects.values('country').distinct()
```

### Aggregation

```python
from zeeb_orm import Count, Sum, Avg, Min, Max

# Aggregate (returns dict)
result = await Post.objects.aggregate(
    total=Count('*'),
    avg_age=Avg('age'),
    max_age=Max('age'),
)
# {'total': 100, 'avg_age': 32.5, 'max_age': 65}

# Annotate (adds computed fields to each object)
users = await User.objects.annotate(
    post_count=Count('posts')
).filter(post_count__gt=5)
```

### Related Object Loading

```python
# JOIN loading (select_related) — for ForeignKey/OneToOne
posts = await Post.objects.select_related('author').all()
post.author.name  # no extra query

# Separate query loading (prefetch_related) — for reverse FK/M2M
users = await User.objects.prefetch_related('posts').all()
user.posts  # no extra query
```

### Other QuerySet Methods

```python
await Post.objects.first()
await Post.objects.last()
await Post.objects.filter(email='x@x.com').exists()   # bool
await Post.objects.filter(active=True).count()         # int

Post.objects.only('id', 'title')       # load only these fields
Post.objects.defer('content')          # load all except these
Post.objects.values('id', 'title')     # return dicts
Post.objects.values_list('id', flat=True)  # return flat list
```

### Transactions

```python
from zeeb_orm import atomic

# Context manager
async with atomic():
    user = await User.objects.create(name='John')
    await Post.objects.create(author=user, title='First post')
    # both committed together, or rolled back on error

# Decorator
@atomic()
async def create_user_with_profile(name: str):
    user = await User.objects.create(name=name)
    await Profile.objects.create(user=user)
    return user
```

---

## ORM — Migrations

Zeeb wraps Alembic with Django-like commands:

```bash
zeeb-manage init                              # initialize migrations directory
zeeb-manage makemigrations --name create_posts # auto-detect changes, generate migration
zeeb-manage migrate                           # apply all pending migrations
zeeb-manage migrate abc123                    # migrate to specific revision
zeeb-manage rollback                          # roll back one migration
zeeb-manage rollback base                     # roll back all
zeeb-manage showmigrations                    # show migration status
zeeb-manage current                           # show current revision
```

---

## ORM — Configuration

```python
from zeeb_orm import configure

configure(
    database={
        'url': 'postgresql+asyncpg://user:pass@localhost/mydb',
        'echo': False,
        'pool_size': 5,
        'max_overflow': 10,
        'pool_timeout': 30,
        'pool_recycle': 1800,
    },
    migrations_dir='migrations',
)
```

### Connection URLs

```python
# PostgreSQL (async: asyncpg, sync: psycopg2)
'postgresql+asyncpg://user:pass@localhost:5432/mydb'

# MySQL (async: aiomysql, sync: pymysql)
'mysql+aiomysql://user:pass@localhost:3306/mydb'

# SQLite (async: aiosqlite)
'sqlite+aiosqlite:///./mydb.sqlite3'
```

Environment variables: `DATABASE_URL`, `DATABASE_ECHO`, `DATABASE_POOL_SIZE`.

---

## API — Serializers

Import from `zeeb_api`. Serializers are Pydantic-based with DRF-style API.

```python
from zeeb_api import serializers
from .models import Post

class PostSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = ['id', 'title', 'content', 'author_id', 'author_name', 'published', 'created_at']
        read_only_fields = ['created_at']

    def get_author_name(self, obj):
        return obj.author.name if obj.author else None
```

`ModelSerializer` auto-generates fields from the ORM model. Use `fields = "__all__"` for all fields, or list specific ones. Primary keys and `auto_now` fields are automatically read-only.

For custom serializers without a model, use `serializers.Serializer` and declare fields explicitly.

---

## API — ViewSets

```python
from zeeb_api import viewsets, permissions
from .models import Post
from .serializers import PostSerializer

class PostViewSet(viewsets.ModelViewSet):
    queryset = Post.objects
    serializer_class = PostSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    async def get_queryset(self):
        qs = self.queryset
        if not self.request.state.user.is_staff:
            qs = qs.filter(published=True)
        return qs
```

`ModelViewSet` provides: `list`, `retrieve`, `create`, `update`, `partial_update`, `destroy`, and `query` actions.

### Custom Actions

```python
from zeeb_api.viewsets.decorators import action

class PostViewSet(viewsets.ModelViewSet):
    queryset = Post.objects
    serializer_class = PostSerializer

    @action(detail=True, methods=['post'])
    async def like(self, request, pk=None):
        post = await self.get_object()
        await post.objects.update(likes=F('likes') + 1)
        return {"liked": True}

    @action(detail=False, methods=['get'])
    async def featured(self, request):
        posts = await Post.objects.filter(featured=True)
        serializer = self.get_serializer(posts, many=True)
        return serializer.data
```

---

## API — Routers

```python
# apps/blog/urls.py
from zeeb_api.routers import DefaultRouter
from .views import PostViewSet, CommentViewSet

router = DefaultRouter()
router.register('posts', PostViewSet)
router.register('comments', CommentViewSet)

# myproject/urls.py
from zeeb_api.routers import DefaultRouter
from apps.blog.urls import router as blog_router

router = DefaultRouter()
router.include(blog_router, prefix='blog')
```

### Generated Endpoints

For `router.register('posts', PostViewSet)`:

| Method | URL | Action |
|--------|-----|--------|
| GET | `/posts/` | list |
| POST | `/posts/` | create |
| POST | `/posts/query/` | query (complex filtering) |
| GET | `/posts/{id}/` | retrieve |
| PUT | `/posts/{id}/` | update |
| PATCH | `/posts/{id}/` | partial_update |
| DELETE | `/posts/{id}/` | destroy |

### Query Endpoint

POST to `/posts/query/` with a body:

```json
{
    "filter": "Q(published=True) & Q(author_id=1)",
    "order_by": ["-created_at"],
    "limit": 20,
    "offset": 0
}
```

---

## API — JWT Authentication

### Configuration (in settings.py)

```python
from zeeb_api.auth import configure_jwt

configure_jwt(
    secret_key="your-secret-key",
    algorithm="HS256",
    access_token_expires=900,       # 15 minutes
    refresh_token_expires=604800,   # 7 days
)
```

### Auth Router

```python
# In asgi.py
from zeeb_api.auth import auth_router
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
```

Endpoints:
- `POST /api/auth/login/` — returns `{ access_token, refresh_token, token_type, expires_in }`
- `POST /api/auth/refresh/` — refresh access token
- `POST /api/auth/logout/` — invalidate tokens
- `GET /api/auth/me/` — get current user

### Middleware

```python
from zeeb_api.auth import JWTAuthMiddleware
app.add_middleware(JWTAuthMiddleware)
```

The middleware extracts Bearer tokens and sets `request.state.user`. It does **not** raise on missing/invalid tokens — permissions enforce authentication.

### User Model

```python
from zeeb_api.auth import get_user_model
User = get_user_model()

user = await User.objects.create(email="user@example.com", username="johndoe")
user.set_password("secret123")
await user.save()

if user.check_password("secret123"):
    print("Valid!")
```

### Accessing the Authenticated User

```python
class MyViewSet(viewsets.ModelViewSet):
    async def create(self, request):
        user = request.state.user  # the authenticated user (or None)
        ...
```

---

## API — Permissions

```python
from zeeb_api import permissions

# Built-in
permissions.AllowAny                    # no restrictions
permissions.IsAuthenticated             # must be logged in
permissions.IsAdminUser                 # must be admin/staff/superuser
permissions.IsAuthenticatedOrReadOnly   # auth for writes, open for reads

# Custom
class IsAuthor(permissions.BasePermission):
    async def has_object_permission(self, request, view, obj):
        return obj.author_id == request.state.user.id

class PostViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, IsAuthor]
```

### ORM-Level Permissions (Rule system)

```python
from zeeb_orm.permissions import Rule

class Post(Model):
    title = fields.CharField(max_length=200)
    author = fields.ForeignKey('User', related_name='posts')

    class Meta:
        permissions = {
            'read': Rule.public(),
            'change': Rule.owner('author') | Rule.staff(),
            'delete': Rule.owner('author') | Rule.superuser(),
        }

# Automatic QuerySet filtering
readable_posts = await Post.objects.readable_by(user)
changeable_posts = await Post.objects.changeable_by(user)
```

---

## API — Exception Handling

All errors return a standardized structure designed for frontend i18n:

```json
{
    "success": false,
    "error": {
        "code": "VALIDATION_ERROR",
        "message": "Validation failed",
        "details": [
            {"code": "FIELD_REQUIRED", "field": "email", "message": "This field is required"}
        ],
        "meta": {"request_id": "abc-123", "timestamp": "2024-01-15T10:30:00Z"}
    }
}
```

### Error Code Prefixes

| Prefix | Category | Examples |
|--------|----------|----------|
| `AUTH_` | Authentication | `AUTH_TOKEN_EXPIRED`, `AUTH_INVALID_CREDENTIALS`, `AUTH_TOKEN_MISSING` |
| `PERM_` | Permissions | `PERM_DENIED`, `PERM_INSUFFICIENT_ROLE` |
| `FIELD_` | Validation | `FIELD_REQUIRED`, `FIELD_TOO_LONG`, `FIELD_INVALID_EMAIL` |
| `RESOURCE_` | Resources | `RESOURCE_NOT_FOUND`, `RESOURCE_CONFLICT` |
| `QUERY_` | Query parsing | `QUERY_INVALID_FILTER`, `QUERY_INVALID_OPERATOR` |
| `SERVER_` | Server errors | `SERVER_ERROR` |

### Raising Exceptions

```python
from zeeb_api.exceptions import ZeebException, ErrorCode

raise ZeebException(
    code=ErrorCode.RESOURCE_NOT_FOUND,
    message="Post not found",
    details=[{"code": "RESOURCE_NOT_FOUND", "field": "id"}],
)
```

Other exception classes: `ValidationException`, `AuthenticationException`, `PermissionException`, `ResourceNotFoundException`, `ResourceConflictException`, `QueryException`, `RateLimitException`, `ServerException`.

---

## API — Application Factory

```python
# myproject/asgi.py
from zeeb_api.app import create_app

app = create_app(settings_module='myproject.settings')
```

`create_app()` loads settings, creates the FastAPI app, configures JWT, installs middleware (auth, CORS), installs exception handlers, and loads URL routing from `ROOT_URLCONF`.

---

## Settings Module

A typical `settings.py`:

```python
DATABASE = {
    'url': 'sqlite+aiosqlite:///./mydb.sqlite3',
}

INSTALLED_APPS = [
    'zeeb_api.auth',     # built-in User/Permission models
    'apps.blog',
    'apps.comments',
]

ROOT_URLCONF = 'myproject.urls'
API_PREFIX = '/api'

SECRET_KEY = 'your-secret-key'
JWT_ALGORITHM = 'HS256'
JWT_ACCESS_TOKEN_EXPIRES = 900
JWT_REFRESH_TOKEN_EXPIRES = 604800

MIDDLEWARE = [
    'zeeb_api.middleware.auth.AuthMiddleware',
    'zeeb_api.middleware.cors.CORSMiddleware',
]

DEBUG = True
TITLE = 'My API'
VERSION = '1.0.0'
```

---

## CLI Commands

```bash
zeeb startproject <name>          # scaffold a new project
zeeb-manage startapp <name>       # create a new app in apps/
zeeb-manage init                  # initialize Alembic migrations
zeeb-manage makemigrations        # auto-generate migration from model changes
zeeb-manage migrate               # apply pending migrations
zeeb-manage showmigrations        # list migration status
zeeb-manage createsuperuser       # create admin user interactively
zeeb-manage check                 # verify project configuration
zeeb-manage shell                 # interactive Python shell with models loaded
zeeb-manage runserver             # start uvicorn dev server
```

---

## MCP Server

Zeeb includes a Model Context Protocol server (`zeeb-mcp`) for AI-assisted development:

```bash
zeeb-mcp    # start MCP server
```

Available MCP tools:
- `zeeb_create_project` / `zeeb_create_app` — scaffold projects and apps
- `zeeb_create_model` — add models with fields to `models.py`
- `zeeb_create_serializer` — generate serializers in `serializers.py`
- `zeeb_create_viewset` — generate viewsets in `views.py`
- `zeeb_create_migration` / `zeeb_run_migrations` — manage migrations
- `zeeb_seed_data` — seed database with sample data

---

## Key Patterns & Conventions

1. **Async everywhere** — All ORM operations, viewset actions, permission checks, and serializer saves are `async`.
2. **Lazy QuerySets** — QuerySets don't execute until awaited, iterated, or a terminal method (`.all()`, `.get()`, `.first()`, `.count()`, `.exists()`) is called.
3. **Immutable chaining** — Each `.filter()`, `.order_by()`, etc. returns a new QuerySet clone.
4. **UUID primary keys** — Models get a `UUIDAutoField` PK by default. No need to declare `id` manually.
5. **Django naming** — Use snake_case for fields, table names, related names. Use `__` for lookup expressions.
6. **Import paths** — `from zeeb_orm import Model, fields, Q, F, Count, Sum, Avg, atomic` and `from zeeb_api import serializers, viewsets, permissions`.
7. **`request.state.user`** — The authenticated user in viewset methods, set by JWT middleware.
8. **Error codes for i18n** — Always use `ErrorCode` enum values; frontends translate by code, not message text.

---

## Running Tests

```bash
pytest                                    # run all tests
pytest --cov=zeeb_orm --cov=zeeb_api      # with coverage
pytest tests/test_orm.py -v               # specific file
```

Test config uses `asyncio_mode = "auto"` (pytest-asyncio).
