# Zeeb Framework

**An async-first Python web framework built on SQLAlchemy and FastAPI**

Zeeb provides a batteries-included, declarative API for building async web applications with Python: you describe models, serializers, and viewsets as classes, and the framework wires up the database and REST endpoints for you.

## Features

- **Full ORM** - Models, QuerySets, Q objects, F expressions, and migrations
- **Declarative API layer** - Serializers, ViewSets, and automatic routing
- **Async-first** - Built on SQLAlchemy async and FastAPI
- **Full CLI** - Project management commands via `manage.py` and the `zeeb` CLI

## Quick Example

```python
from zeeb_orm import Model, fields
from zeeb_api import Serializer, ModelViewSet, DefaultRouter

# Define a model
class Post(Model):
    title = fields.CharField(max_length=200)
    content = fields.TextField()
    published = fields.BooleanField(default=False)
    created_at = fields.DateTimeField(auto_now_add=True)

    class Meta:
        table_name = "posts"
        ordering = ["-created_at"]

# Create a serializer
class PostSerializer(Serializer):
    id = fields.UUIDField(read_only=True)
    title = fields.CharField(max_length=200)
    content = fields.CharField()
    published = fields.BooleanField()
    created_at = fields.DateTimeField(read_only=True)

# Create a viewset
class PostViewSet(ModelViewSet):
    queryset = Post.objects.all()
    serializer_class = PostSerializer

# Register routes
router = DefaultRouter()
router.register("posts", PostViewSet)
```

## Installation

```bash
pip install git+https://github.com/zeeb-cloud/zeebpy.git
```

## Creating a Project

```bash
# Create a new project — authentication is already wired
zeeb startproject myproject
cd myproject

# Create an app; it registers itself in INSTALLED_APPS and urls.py
python manage.py startapp blog

# Run migrations
python manage.py makemigrations
python manage.py migrate

# Start the development server
python manage.py runserver
```

The generated project already serves `/api/v1/auth/login`, `/register`,
`/refresh`, `/logout` and `/me`, plus `/health` and `/ready`. Configure it
through `.env` — see [Settings](configuration/settings.md).

## Documentation

### Getting Started
- [Installation](installation.md) - Install and configure Zeeb
- [Quick Start Tutorial](quickstart.md) - Build your first API

### ORM (zeeb_orm)
- [Models](orm/models.md) - Define your data models
- [Fields](orm/fields.md) - Available field types
- [Queries](orm/queries.md) - Query your data
- [Expressions](orm/expressions.md) - F, Q, annotations, and functions
- [Relationships](orm/relationships.md) - ForeignKey, OneToOne, ManyToMany
- [Migrations](orm/migrations.md) - Database migrations
- [Signals](orm/signals.md) - Model lifecycle signals
- [Object Permissions](orm/permissions.md) - Per-object rules and queryset filtering

### API (zeeb_api)
- [Serializers](api/serializers.md) - Serialize and validate data
- [ViewSets](api/viewsets.md) - Create API endpoints
- [Authentication](api/authentication.md) - User authentication
- [OAuth2 / OIDC](api/oauth.md) - Single sign-on (Azure AD, Google, GitHub)
- [Permissions](api/permissions.md) - Access control
- [Filtering & Search](api/filtering.md) - Filter backends, search, ordering
- [Pagination](api/pagination.md) - Page, limit/offset and cursor pagination
- [Throttling](api/throttling.md) - Rate limiting
- [Versioning](api/versioning.md) - API versioning
- [Error Handling](api/errors.md) - The standardized error envelope and error codes

### CLI & Tools
- [Commands](cli/commands.md) - Management commands
- [Agent Functions](cli/agents.md) - Importable async functions for scaffolding, management & BaaS operations

### Configuration
- [Settings](configuration/settings.md) - Project configuration
- [Logging](configuration/logging.md) - Logging setup
- [Database](configuration/database.md) - Database configuration

### Reference
- [Field Types](reference/field-types.md) - Complete field reference
- [QuerySet API](reference/queryset-api.md) - QuerySet methods
- [Expressions](reference/expressions.md) - Expression reference
- [ORM Feature Reference](orm/feature-reference.md) - Condensed map of ORM capabilities

## Why Zeeb?

- **Native async end-to-end** — the ORM, the API layer, and the server are async from the ground up
- **SQLAlchemy-backed ORM** with a high-level, chainable QuerySet API
- **FastAPI under the hood** — modern performance with a declarative, class-based developer experience
- **One framework** — models, serializers, viewsets, auth, migrations, and CLI in a single coherent stack

## License

MIT License
