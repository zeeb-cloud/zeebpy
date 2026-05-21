# Zeeb ORM & API

A Django-like framework for building async APIs with FastAPI. Includes a powerful ORM (SQLAlchemy-based) and DRF-style serializers/viewsets.

## Features

### ORM (zeeb_orm)
- **Django-style API**: Familiar `Model.objects.filter()` syntax
- **Q Objects**: Complex queries with `Q(a=1) & Q(b=2)`, `Q(x=1) | Q(y=2)`, `~Q(z=3)`
- **F Expressions**: Reference fields in queries with `F('field_name')`
- **Async-first**: Built for async Python with sync wrappers
- **Multi-database**: PostgreSQL, MySQL, SQLite support
- **Alembic Migrations**: Django-like `makemigrations` and `migrate` commands
- **UUID Primary Keys**: Default UUID PKs for all models

### API (zeeb_api)
- **DRF-style Serializers**: Pydantic-based with familiar API
- **ModelSerializer**: Auto-generate from ORM models
- **ViewSets**: CRUD operations out of the box
- **Routers**: Auto URL generation
- **JWT Authentication**: Built-in auth with refresh tokens
- **User Model**: Database-backed User/Permission like Django
- **Exception Handling**: Standardized error responses with i18n support

### Agent Functions (zeeb_agents)
- **43 async functions** — scaffolding, migrations, server lifecycle, logs, config, files, database, testing
- **Zero MCP dependency** — import directly from any Python code; plug into an MCP server externally
- **Uniform return type** — every function returns `AgentResult(success, message, data)`

## Installation

```bash
pip install zeebpy

# With database drivers
pip install zeebpy[postgresql]  # asyncpg + psycopg2
pip install zeebpy[mysql]       # aiomysql + pymysql  
pip install zeebpy[sqlite]      # aiosqlite
pip install zeebpy[all]         # All drivers
```

## Quick Start - Project Setup

The recommended way to use Zeeb is with the project structure (like Django):

```bash
# Create a new project
zeeb startproject myproject
cd myproject

# Create an app
zeeb-manage startapp posts

# Initialize and run migrations (creates auth tables automatically)
zeeb-manage init
zeeb-manage makemigrations --name initial
zeeb-manage migrate

# Create a superuser
zeeb-manage createsuperuser

# Run the server
zeeb-manage runserver
```

### Project Structure

```
myproject/
├── manage.py                 # Management script
├── myproject/
│   ├── __init__.py
│   ├── settings.py           # Configuration
│   ├── urls.py               # URL routing
│   └── asgi.py               # ASGI application
├── apps/
│   └── posts/
│       ├── models.py         # ORM models
│       ├── serializers.py    # API serializers
│       ├── views.py          # ViewSets
│       └── urls.py           # App routes
├── migrations/
│   └── versions/             # Alembic migrations
└── requirements.txt
```

## CLI Commands

```bash
zeeb startproject <name>      # Create new project
zeeb-manage startapp <name>   # Create new app
zeeb-manage init              # Initialize migrations
zeeb-manage makemigrations    # Create migration files
zeeb-manage migrate           # Apply migrations
zeeb-manage showmigrations    # Show migration status
zeeb-manage createsuperuser   # Create admin user
zeeb-manage check             # Verify configuration
zeeb-manage shell             # Interactive Python shell
zeeb-manage runserver         # Start dev server
```

### CRUD Operations

```python
# Create
user = await User.objects.create(name='John', email='john@example.com', age=25)

# Read
user = await User.objects.get(id=1)
users = await User.objects.filter(age__gte=18)
all_users = await User.objects.all()

# Update
await User.objects.filter(age__lt=18).update(is_minor=True)
user.name = 'Jane'
await user.save()

# Delete
await User.objects.filter(last_login__lt=old_date).delete()
await user.delete()
```

### Filtering with Q Objects

```python
from zeeb_orm import Q

# Simple AND
users = await User.objects.filter(active=True, age__gte=18)

# Complex OR
users = await User.objects.filter(
    Q(role='admin') | Q(role='moderator')
)

# Combined AND/OR
users = await User.objects.filter(
    Q(age__gte=18) & (Q(name__startswith='A') | Q(name__startswith='B'))
)

# NOT
users = await User.objects.filter(~Q(deleted=True))
```

### Lookup Expressions

```python
# Comparison
User.objects.filter(age__gt=18)       # Greater than
User.objects.filter(age__gte=18)      # Greater than or equal
User.objects.filter(age__lt=65)       # Less than
User.objects.filter(age__lte=65)      # Less than or equal

# String matching
User.objects.filter(name__contains='john')      # LIKE %john%
User.objects.filter(name__icontains='john')     # Case-insensitive
User.objects.filter(name__startswith='J')       # LIKE J%
User.objects.filter(name__endswith='son')       # LIKE %son
User.objects.filter(email__iexact='JOHN@x.com') # Case-insensitive exact

# In/Range
User.objects.filter(status__in=['active', 'pending'])
User.objects.filter(age__range=(18, 65))

# Null checks
User.objects.filter(deleted_at__isnull=True)
```

### F Expressions

```python
from zeeb_orm import F

# Compare fields
Post.objects.filter(likes__gt=F('dislikes'))

# Arithmetic
await Post.objects.update(view_count=F('view_count') + 1)

# Complex expressions
Post.objects.filter(score__gt=F('likes') - F('dislikes'))
```

### Aggregations

```python
from zeeb_orm import Count, Sum, Avg, Min, Max

# Aggregate values
result = await User.objects.aggregate(
    total=Count('*'),
    avg_age=Avg('age'),
    max_age=Max('age')
)
# {'total': 100, 'avg_age': 32.5, 'max_age': 65}

# Annotate (add computed fields)
users = await User.objects.annotate(
    post_count=Count('posts')
).filter(post_count__gt=5)
```

### QuerySet Methods

```python
# Ordering
User.objects.order_by('name')           # Ascending
User.objects.order_by('-created_at')    # Descending
User.objects.order_by('last_name', 'first_name')

# Limiting
User.objects.all()[:10]                 # First 10
User.objects.all()[5:15]                # 5 to 15

# Distinct
User.objects.values('country').distinct()

# Single objects
user = await User.objects.first()
user = await User.objects.last()
user = await User.objects.get(id=1)     # Raises if not found or multiple

# Existence/Count
exists = await User.objects.filter(email='x@x.com').exists()
count = await User.objects.filter(active=True).count()

# Get or create
user, created = await User.objects.get_or_create(
    email='john@example.com',
    defaults={'name': 'John'}
)

# Update or create
user, created = await User.objects.update_or_create(
    email='john@example.com',
    defaults={'name': 'John Updated'}
)
```

### Related Object Loading

```python
# Eager loading with JOINs (select_related)
posts = await Post.objects.select_related('author').all()
for post in posts:
    print(post.author.name)  # No additional query

# Prefetch in separate queries (prefetch_related)
users = await User.objects.prefetch_related('posts').all()
for user in users:
    for post in user.posts:  # No additional query
        print(post.title)
```

### Transactions

```python
from zeeb_orm import atomic

# As context manager
async with atomic():
    user = await User.objects.create(name='John')
    await Post.objects.create(author=user, title='First post')
    # Both committed together, or rolled back on error

# As decorator
@atomic()
async def create_user_with_profile(name: str):
    user = await User.objects.create(name=name)
    await Profile.objects.create(user=user)
    return user
```

## Migrations

Zeeb ORM uses Alembic for migrations with Django-like commands.

### Initialize Migrations

```bash
zeeb init
# Creates migrations/ directory with Alembic configuration
```

### Create Migration

```bash
zeeb makemigrations -m "create users table"
# Auto-detects model changes and creates migration file
```

### Apply Migrations

```bash
zeeb migrate
# Applies all pending migrations

zeeb migrate abc123
# Migrate to specific revision
```

### Rollback

```bash
zeeb rollback
# Roll back one migration

zeeb rollback -1
# Roll back one migration (explicit)

zeeb rollback base
# Roll back all migrations
```

### Show Status

```bash
zeeb showmigrations
# Shows all migrations and their status

zeeb current
# Shows current revision
```

## Field Types

| Field | Description |
|-------|-------------|
| `AutoField` | Auto-incrementing integer primary key |
| `BigAutoField` | Auto-incrementing big integer primary key |
| `CharField(max_length)` | String with max length |
| `TextField` | Unlimited text |
| `EmailField` | Email string |
| `SlugField` | URL-friendly string |
| `URLField` | URL string |
| `IntegerField` | Integer |
| `SmallIntegerField` | Small integer |
| `BigIntegerField` | Big integer |
| `PositiveIntegerField` | Positive integer |
| `FloatField` | Floating point |
| `DecimalField(max_digits, decimal_places)` | Fixed precision decimal |
| `BooleanField` | True/False |
| `DateField` | Date |
| `TimeField` | Time |
| `DateTimeField` | Date and time |
| `JSONField` | JSON data |
| `UUIDField` | UUID |
| `ForeignKey(Model)` | Foreign key relationship |
| `OneToOneField(Model)` | One-to-one relationship |
| `ManyToManyField(Model)` | Many-to-many relationship |

### Field Options

```python
fields.CharField(
    max_length=100,
    null=False,          # Allow NULL in database
    blank=False,         # Allow empty in validation
    default='value',     # Default value
    primary_key=False,   # Is primary key
    unique=False,        # Unique constraint
    index=False,         # Create index
    db_column='col',     # Custom column name
    choices=[('a', 'A')],# Allowed values
)

fields.DateTimeField(
    auto_now=False,      # Update on every save
    auto_now_add=False,  # Set on creation
)

fields.ForeignKey(
    User,
    on_delete='CASCADE', # CASCADE, SET_NULL, PROTECT, etc.
    related_name='posts',# Reverse relation name
    null=False,
)
```

## Model Meta Options

```python
class User(Model):
    name = fields.CharField(max_length=100)
    
    class Meta:
        table_name = 'users'              # Database table name
        ordering = ['-created_at']        # Default ordering
        abstract = False                  # Abstract base model
        
        # Indexes
        indexes = [
            Index(fields=['name', 'email']),
            Index(fields=['status'], name='status_idx'),
        ]
        
        # Constraints
        constraints = [
            UniqueConstraint(fields=['email', 'tenant_id']),
            CheckConstraint(check='age >= 0', name='age_positive'),
        ]
        
        unique_together = [('email', 'tenant_id')]
```

## Configuration

### Settings

```python
from zeeb_orm import configure

configure(
    database={
        'url': 'postgresql+asyncpg://localhost/mydb',
        'echo': False,           # Log SQL
        'pool_size': 5,          # Connection pool size
        'max_overflow': 10,      # Max extra connections
        'pool_timeout': 30,      # Wait timeout
        'pool_recycle': 1800,    # Recycle connections
    },
    migrations_dir='migrations',
)
```

### Environment Variables

```bash
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/mydb
DATABASE_ECHO=false
DATABASE_POOL_SIZE=5
```

## Database Support

| Database | Async Driver | Sync Driver |
|----------|--------------|-------------|
| PostgreSQL | `asyncpg` | `psycopg2` |
| MySQL | `aiomysql` | `pymysql` |
| SQLite | `aiosqlite` | (built-in) |

### Connection URLs

```python
# PostgreSQL
'postgresql+asyncpg://user:pass@localhost:5432/mydb'

# MySQL  
'mysql+aiomysql://user:pass@localhost:3306/mydb'

# SQLite
'sqlite+aiosqlite:///./mydb.sqlite3'
```

---

## zeeb_api - DRF-style API Framework

### Defining Models and Serializers

```python
# apps/blog/models.py
from zeeb_orm import Model, fields

class Post(Model):
    title = fields.CharField(max_length=200)
    content = fields.TextField()
    author = fields.ForeignKey('User', related_name='posts')
    published = fields.BooleanField(default=False)
    created_at = fields.DateTimeField(auto_now_add=True)

# apps/blog/serializers.py
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

### Creating ViewSets

```python
# apps/blog/views.py
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

### Routing

```python
# apps/blog/urls.py
from zeeb_api.routers import DefaultRouter
from .views import PostViewSet

router = DefaultRouter()
router.register('posts', PostViewSet)

# myproject/urls.py  
from zeeb_api.routers import DefaultRouter
from apps.blog.urls import router as blog_router

router = DefaultRouter()
router.include(blog_router, prefix='blog')

# In asgi.py
from myproject.urls import router
app.include_router(router.routes, prefix="/api")
```

### Query Endpoint (POST /api/posts/query/)

ViewSets include a query endpoint for complex filtering:

```python
# Request
POST /api/posts/query/
{
    "filter": "Q(published=True) & Q(author_id=1)",
    "order_by": ["-created_at"],
    "limit": 20,
    "offset": 0
}

# Response
{
    "count": 45,
    "limit": 20,
    "offset": 0,
    "results": [...]
}
```

### Permissions

```python
from zeeb_api import permissions

# Built-in permissions
permissions.AllowAny              # No restrictions
permissions.IsAuthenticated       # Must be logged in
permissions.IsAdminUser           # Must be admin/superuser
permissions.IsAuthenticatedOrReadOnly  # Auth for writes

# Custom permission
class IsAuthor(permissions.BasePermission):
    async def has_object_permission(self, request, view, obj):
        return obj.author_id == request.state.user.id

class PostViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, IsAuthor]
```

---

## JWT Authentication

### Configuration

```python
# In settings.py
from zeeb_api.auth import configure_jwt

configure_jwt(
    secret_key="your-secret-key",  # Required
    algorithm="HS256",
    access_token_expires=900,       # 15 minutes
    refresh_token_expires=604800,   # 7 days
)
```

### Auth Endpoints

```python
# In asgi.py
from zeeb_api.auth import auth_router

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
```

Endpoints:
- `POST /api/auth/login/` - Get tokens from credentials
- `POST /api/auth/refresh/` - Refresh access token
- `POST /api/auth/logout/` - Invalidate tokens
- `GET /api/auth/me/` - Get current user

### Login Request/Response

```python
# Request
POST /api/auth/login/
{"email": "user@example.com", "password": "secret123"}

# Response
{
    "access_token": "eyJ...",
    "refresh_token": "eyJ...",
    "token_type": "bearer",
    "expires_in": 900
}
```

### Protecting Routes

```python
from zeeb_api.auth import JWTAuthMiddleware

# Add middleware to FastAPI
app.add_middleware(JWTAuthMiddleware)

# In viewsets
class ProtectedViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    
    async def list(self, request):
        user = request.state.user  # The authenticated user
        ...
```

### User Model

Zeeb includes Django-like User and Permission models:

```python
from zeeb_api.auth import get_user_model

User = get_user_model()

# Create user
user = await User.objects.create(
    email="user@example.com",
    username="johndoe"
)
user.set_password("secret123")
await user.save()

# Check password
if user.check_password("secret123"):
    print("Valid!")
```

---

## Error Handling

All errors return a consistent structure for frontend i18n:

```json
{
    "success": false,
    "error": {
        "code": "VALIDATION_ERROR",
        "message": "Validation failed",
        "details": [
            {
                "code": "FIELD_REQUIRED",
                "field": "email",
                "message": "This field is required"
            }
        ],
        "meta": {
            "request_id": "abc-123",
            "timestamp": "2024-01-15T10:30:00Z"
        }
    }
}
```

Error codes are designed for frontend translation:
- `AUTH_*` - Authentication (AUTH_TOKEN_EXPIRED, AUTH_INVALID_CREDENTIALS)
- `PERM_*` - Permissions (PERM_DENIED)
- `FIELD_*` - Field validation (FIELD_REQUIRED, FIELD_INVALID_FORMAT)
- `RESOURCE_*` - Resources (RESOURCE_NOT_FOUND)
- `QUERY_*` - Query parsing (QUERY_INVALID_FILTER)
- `SERVER_*` - Server errors (SERVER_ERROR)

### Custom Exceptions

```python
from zeeb_api.exceptions import ZeebException, ErrorCode

raise ZeebException(
    code=ErrorCode.RESOURCE_NOT_FOUND,
    message="Post not found",
    details=[{"code": "RESOURCE_NOT_FOUND", "field": "id"}]
)
```

---

---

## zeeb_agents — Agent Functions

`zeeb_agents` is a standalone async Python library with 43 functions covering everything needed to scaffold, manage, and inspect a Zeeb project.  It has no MCP dependency — consume it from any Python code or wire it into an external MCP server.

```python
from zeeb_agents import (
    # Scaffolding
    create_project, create_app, create_model, add_field, generate_crud,
    # Migrations
    make_migrations, run_migrations, get_migration_status,
    # Server
    start_server, stop_server, get_server_status,
    # Observability
    read_logs, search_logs,
    get_settings, get_env, set_env,
    read_file, write_file, search_code,
    list_tables, run_query,
    run_tests,
    run_management_command,
)
```

All functions return an `AgentResult`:

```python
@dataclass
class AgentResult:
    success: bool          # True on success
    message: str           # Human-readable summary
    data: dict | None      # Structured output

if result:   # AgentResult is truthy on success
    print(result.message)
    print(result.data)
```

### Quick examples

```python
# Generate full CRUD (model + serializer + viewset + route) in one call
result = await generate_crud("blog", "Post", fields=[
    {"name": "title", "type": "CharField", "max_length": 200},
    {"name": "body",  "type": "TextField"},
])

# Read last 50 error log lines
result = await read_logs(lines=50, level="ERROR")

# Inspect the live database
tables = await list_tables()
rows   = await run_query("SELECT id, title FROM blog_post LIMIT 5")

# Manage environment
await set_env("DEBUG", "0")

# Run tests
result = await run_tests()
# result.message == "114 passed"

# Run any management command
await run_management_command("createsuperuser", args=["--no-input", "--username=admin"])
```

Full API reference: [`docs/cli/agents.md`](docs/cli/agents.md)

---

## Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=zeeb_orm --cov=zeeb_api

# Run specific test file
pytest tests/test_orm.py -v
```

## License

MIT License
