# MCP Tools

Zeeb provides Model Context Protocol (MCP) tools for AI assistants to build APIs.

## Overview

MCP enables LLMs to create and manage Zeeb projects, models, serializers, viewsets, and more through structured tool calls.

## Starting MCP Server

### stdio Mode (Recommended for AI)

```bash
python manage.py mcp stdio
```

### HTTP Mode (For debugging)

```bash
python manage.py mcp serve --host 127.0.0.1 --port 8080
```

## Available Tools

### Project Management

#### zeeb_create_project

Create a new Zeeb project.

```json
{
  "name": "zeeb_create_project",
  "arguments": {
    "name": "myproject",
    "directory": "/path/to/create"
  }
}
```

#### zeeb_create_app

Create a new app within a project.

```json
{
  "name": "zeeb_create_app",
  "arguments": {
    "name": "blog",
    "project_path": "/path/to/myproject"
  }
}
```

#### zeeb_delete_app

Delete an app.

```json
{
  "name": "zeeb_delete_app",
  "arguments": {
    "name": "blog",
    "project_path": "/path/to/myproject"
  }
}
```

#### zeeb_project_info

Get project information.

```json
{
  "name": "zeeb_project_info",
  "arguments": {
    "project_path": "/path/to/myproject"
  }
}
```

Returns:
```json
{
  "name": "myproject",
  "apps": ["blog", "users"],
  "models": {
    "blog": ["Article", "Comment"],
    "users": ["Profile"]
  },
  "database": "postgresql+asyncpg://...",
  "installed_apps": ["apps.blog", "apps.users"]
}
```

### Model Management

#### zeeb_create_model

Create a new model.

```json
{
  "name": "zeeb_create_model",
  "arguments": {
    "app_name": "blog",
    "model_name": "Article",
    "fields": [
      {"name": "title", "type": "CharField", "options": {"max_length": 200}},
      {"name": "content", "type": "TextField"},
      {"name": "published", "type": "BooleanField", "options": {"default": false}},
      {"name": "views", "type": "IntegerField", "options": {"default": 0}},
      {"name": "created_at", "type": "DateTimeField", "options": {"auto_now_add": true}}
    ],
    "meta": {
      "table_name": "articles",
      "ordering": ["-created_at"]
    },
    "project_path": "/path/to/myproject"
  }
}
```

#### zeeb_update_model

Update an existing model.

```json
{
  "name": "zeeb_update_model",
  "arguments": {
    "app_name": "blog",
    "model_name": "Article",
    "meta": {
      "ordering": ["-views", "-created_at"]
    },
    "project_path": "/path/to/myproject"
  }
}
```

#### zeeb_delete_model

Delete a model.

```json
{
  "name": "zeeb_delete_model",
  "arguments": {
    "app_name": "blog",
    "model_name": "Article",
    "project_path": "/path/to/myproject"
  }
}
```

#### zeeb_list_models

List all models in an app.

```json
{
  "name": "zeeb_list_models",
  "arguments": {
    "app_name": "blog",
    "project_path": "/path/to/myproject"
  }
}
```

Returns:
```json
{
  "models": [
    {
      "name": "Article",
      "fields": [
        {"name": "id", "type": "UUIDAutoField", "primary_key": true},
        {"name": "title", "type": "CharField", "max_length": 200},
        {"name": "content", "type": "TextField"},
        {"name": "author_id", "type": "ForeignKey", "to": "User"}
      ],
      "meta": {
        "table_name": "articles",
        "ordering": ["-created_at"]
      }
    }
  ]
}
```

#### zeeb_add_field

Add a field to an existing model.

```json
{
  "name": "zeeb_add_field",
  "arguments": {
    "app_name": "blog",
    "model_name": "Article",
    "field_name": "summary",
    "field_type": "TextField",
    "options": {"null": true, "blank": true},
    "project_path": "/path/to/myproject"
  }
}
```

#### zeeb_remove_field

Remove a field from a model.

```json
{
  "name": "zeeb_remove_field",
  "arguments": {
    "app_name": "blog",
    "model_name": "Article",
    "field_name": "summary",
    "project_path": "/path/to/myproject"
  }
}
```

#### zeeb_add_relationship

Add a relationship between models.

```json
{
  "name": "zeeb_add_relationship",
  "arguments": {
    "app_name": "blog",
    "model_name": "Article",
    "field_name": "author",
    "relationship_type": "ForeignKey",
    "related_model": "User",
    "options": {
      "on_delete": "CASCADE",
      "related_name": "articles"
    },
    "project_path": "/path/to/myproject"
  }
}
```

Relationship types: `ForeignKey`, `OneToOne`, `ManyToMany`

### Serializer Management

#### zeeb_create_serializer

Create a serializer for a model.

```json
{
  "name": "zeeb_create_serializer",
  "arguments": {
    "app_name": "blog",
    "serializer_name": "ArticleSerializer",
    "model_name": "Article",
    "fields": ["id", "title", "content", "author", "created_at"],
    "read_only_fields": ["id", "created_at"],
    "project_path": "/path/to/myproject"
  }
}
```

#### zeeb_update_serializer

Update a serializer.

```json
{
  "name": "zeeb_update_serializer",
  "arguments": {
    "app_name": "blog",
    "serializer_name": "ArticleSerializer",
    "fields": ["id", "title", "content", "summary", "author", "created_at"],
    "extra_kwargs": {
      "content": {"required": false}
    },
    "project_path": "/path/to/myproject"
  }
}
```

### ViewSet Management

#### zeeb_create_viewset

Create a ViewSet.

```json
{
  "name": "zeeb_create_viewset",
  "arguments": {
    "app_name": "blog",
    "viewset_name": "ArticleViewSet",
    "model_name": "Article",
    "serializer_name": "ArticleSerializer",
    "viewset_type": "ModelViewSet",
    "project_path": "/path/to/myproject"
  }
}
```

ViewSet types: `ViewSet`, `ModelViewSet`, `ReadOnlyModelViewSet`

#### zeeb_add_viewset_action

Add a custom action to a ViewSet.

```json
{
  "name": "zeeb_add_viewset_action",
  "arguments": {
    "app_name": "blog",
    "viewset_name": "ArticleViewSet",
    "action_name": "publish",
    "methods": ["POST"],
    "detail": true,
    "code": "article = await self.get_object()\narticle.published = True\nawait article.save()\nreturn {\"status\": \"published\"}",
    "project_path": "/path/to/myproject"
  }
}
```

#### zeeb_generate_crud

Generate complete CRUD (model, serializer, viewset, URLs).

```json
{
  "name": "zeeb_generate_crud",
  "arguments": {
    "app_name": "blog",
    "model_name": "Article",
    "fields": [
      {"name": "title", "type": "CharField", "options": {"max_length": 200}},
      {"name": "content", "type": "TextField"},
      {"name": "published", "type": "BooleanField", "options": {"default": false}}
    ],
    "project_path": "/path/to/myproject"
  }
}
```

Creates:
- Model in `models.py`
- Serializer in `serializers.py`
- ViewSet in `views.py`
- URL registration in `urls.py`

#### zeeb_list_endpoints

List all API endpoints.

```json
{
  "name": "zeeb_list_endpoints",
  "arguments": {
    "project_path": "/path/to/myproject"
  }
}
```

Returns:
```json
{
  "endpoints": [
    {"method": "GET", "path": "/api/articles/", "view": "ArticleViewSet.list"},
    {"method": "POST", "path": "/api/articles/", "view": "ArticleViewSet.create"},
    {"method": "GET", "path": "/api/articles/{id}/", "view": "ArticleViewSet.retrieve"},
    {"method": "POST", "path": "/api/articles/{id}/publish/", "view": "ArticleViewSet.publish"}
  ]
}
```

### Migration Management

#### zeeb_run_migrations

Run migrations.

```json
{
  "name": "zeeb_run_migrations",
  "arguments": {
    "action": "migrate",
    "project_path": "/path/to/myproject"
  }
}
```

Actions: `makemigrations`, `migrate`, `rollback`

#### zeeb_migration_status

Get migration status.

```json
{
  "name": "zeeb_migration_status",
  "arguments": {
    "project_path": "/path/to/myproject"
  }
}
```

Returns:
```json
{
  "current": "002_add_profiles",
  "migrations": [
    {"revision": "001_initial", "applied": true, "date": "2024-01-15"},
    {"revision": "002_add_profiles", "applied": true, "date": "2024-01-16"},
    {"revision": "003_add_comments", "applied": false}
  ],
  "pending": 1
}
```

#### zeeb_rollback_migration

Rollback migrations.

```json
{
  "name": "zeeb_rollback_migration",
  "arguments": {
    "target": "001_initial",
    "project_path": "/path/to/myproject"
  }
}
```

### Data Management

#### zeeb_seed_data

Seed test data.

```json
{
  "name": "zeeb_seed_data",
  "arguments": {
    "app_name": "blog",
    "model_name": "Article",
    "count": 10,
    "data_template": {
      "title": "Article {n}",
      "content": "Content for article {n}",
      "published": true
    },
    "project_path": "/path/to/myproject"
  }
}
```

#### zeeb_query_data

Query data from models.

```json
{
  "name": "zeeb_query_data",
  "arguments": {
    "app_name": "blog",
    "model_name": "Article",
    "filters": {"published": true},
    "order_by": ["-created_at"],
    "limit": 10,
    "project_path": "/path/to/myproject"
  }
}
```

Returns:
```json
{
  "count": 42,
  "results": [
    {"id": "...", "title": "Latest Article", "published": true},
    ...
  ]
}
```

### Server Management

#### zeeb_start_server

Start the development server.

```json
{
  "name": "zeeb_start_server",
  "arguments": {
    "host": "127.0.0.1",
    "port": 8000,
    "project_path": "/path/to/myproject"
  }
}
```

#### zeeb_stop_server

Stop the development server.

```json
{
  "name": "zeeb_stop_server",
  "arguments": {
    "project_path": "/path/to/myproject"
  }
}
```

#### zeeb_server_status

Get server status.

```json
{
  "name": "zeeb_server_status",
  "arguments": {
    "project_path": "/path/to/myproject"
  }
}
```

## Field Types Reference

Available field types for `zeeb_create_model` and `zeeb_add_field`:

| Type | Options |
|------|---------|
| `CharField` | `max_length` (required) |
| `TextField` | - |
| `IntegerField` | `default` |
| `BigIntegerField` | `default` |
| `FloatField` | `default` |
| `DecimalField` | `max_digits`, `decimal_places` |
| `BooleanField` | `default` |
| `DateField` | `auto_now`, `auto_now_add` |
| `DateTimeField` | `auto_now`, `auto_now_add` |
| `EmailField` | - |
| `URLField` | - |
| `SlugField` | `max_length` |
| `UUIDField` | - |
| `JSONField` | `default` |
| `ForeignKey` | `to`, `on_delete`, `related_name` |
| `OneToOne` | `to`, `on_delete`, `related_name` |
| `ManyToMany` | `to`, `related_name` |

Common options for all fields: `null`, `blank`, `default`, `unique`, `index`

## Example: Building a Blog API

```json
// 1. Create project
{"name": "zeeb_create_project", "arguments": {"name": "blog_api"}}

// 2. Create blog app
{"name": "zeeb_create_app", "arguments": {"name": "blog"}}

// 3. Create Author model
{"name": "zeeb_create_model", "arguments": {
  "app_name": "blog",
  "model_name": "Author",
  "fields": [
    {"name": "name", "type": "CharField", "options": {"max_length": 100}},
    {"name": "email", "type": "EmailField", "options": {"unique": true}}
  ]
}}

// 4. Create Article with full CRUD
{"name": "zeeb_generate_crud", "arguments": {
  "app_name": "blog",
  "model_name": "Article",
  "fields": [
    {"name": "title", "type": "CharField", "options": {"max_length": 200}},
    {"name": "content", "type": "TextField"},
    {"name": "published", "type": "BooleanField", "options": {"default": false}}
  ]
}}

// 5. Add author relationship
{"name": "zeeb_add_relationship", "arguments": {
  "app_name": "blog",
  "model_name": "Article",
  "field_name": "author",
  "relationship_type": "ForeignKey",
  "related_model": "Author",
  "options": {"on_delete": "CASCADE", "related_name": "articles"}
}}

// 6. Run migrations
{"name": "zeeb_run_migrations", "arguments": {"action": "makemigrations"}}
{"name": "zeeb_run_migrations", "arguments": {"action": "migrate"}}

// 7. Add custom action
{"name": "zeeb_add_viewset_action", "arguments": {
  "app_name": "blog",
  "viewset_name": "ArticleViewSet",
  "action_name": "publish",
  "methods": ["POST"],
  "detail": true,
  "code": "article = await self.get_object()\narticle.published = True\nawait article.save()\nreturn {\"status\": \"published\"}"
}}

// 8. Start server
{"name": "zeeb_start_server", "arguments": {"port": 8000}}
```

## Next Steps

- [CLI Commands](commands.md) - Manual CLI usage
- [Models](../orm/models.md) - Model documentation
- [ViewSets](../api/viewsets.md) - ViewSet documentation
