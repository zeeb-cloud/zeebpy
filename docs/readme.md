# Zeeb Framework

**A Django-like async web framework built on SQLAlchemy and FastAPI**

Zeeb provides a familiar Django-style API for building async web applications with Python. It combines the best of Django's developer experience with modern async capabilities.

## Features

- **Django-like ORM** - Models, QuerySets, Q objects, F expressions, and migrations
- **DRF-style API** - Serializers, ViewSets, and automatic routing
- **Async-first** - Built on SQLAlchemy async and FastAPI
- **Full CLI** - Django-style management commands

## Quick Example

```python
from zeeb_orm import Model, fields
from zeeb_api import Serializer, ModelViewSet, Router

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
router = Router()
router.register("posts", PostViewSet)
```

## Installation

```bash
pip install zeebpy zeeb-api
```

## Creating a Project

```bash
# Create a new project
zeeb startproject myproject
cd myproject

# Create an app
python manage.py startapp blog

# Run migrations
python manage.py makemigrations
python manage.py migrate

# Start the development server
python manage.py runserver
```

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

### API (zeeb_api)
- [Serializers](api/serializers.md) - Serialize and validate data
- [ViewSets](api/viewsets.md) - Create API endpoints
- [Authentication](api/authentication.md) - User authentication
- [Permissions](api/permissions.md) - Access control

### CLI & Tools
- [Commands](cli/commands.md) - Management commands
- [Agent Functions](cli/agents.md) - Importable async functions for scaffolding & management

### Configuration
- [Settings](configuration/settings.md) - Project configuration
- [Logging](configuration/logging.md) - Logging setup
- [Database](configuration/database.md) - Database configuration

### Reference
- [Field Types](reference/field-types.md) - Complete field reference
- [QuerySet API](reference/queryset-api.md) - QuerySet methods
- [Expressions](reference/expressions.md) - Expression reference

## Why Zeeb?

| Feature | Django | Zeeb |
|---------|--------|------|
| Async support | Limited | Native |
| ORM | Django ORM | SQLAlchemy (async) |
| API framework | DRF (sync) | FastAPI (async) |
| Learning curve | Familiar | Django-like API |
| Performance | Good | Excellent (async) |

## License

MIT License
