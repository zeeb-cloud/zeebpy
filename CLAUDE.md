# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

zeebpy is a Django-like async framework in three packages, built and shipped together (`pyproject.toml`, hatchling):

- **zeeb_orm** — Django-style ORM wrapping SQLAlchemy 2.0 async + Alembic migrations
- **zeeb_api** — DRF-style API layer (serializers, viewsets, routers, JWT auth, permissions) on FastAPI/Pydantic v2
- **zeeb_agents** — ~40 async agent functions (scaffolding, migrations, server lifecycle, introspection), MCP-ready but with zero MCP dependency

Everything is async-first: all ORM operations are `await`ed, viewset methods are `async def`, tests run under `pytest-asyncio` with `asyncio_mode = "auto"`.

## Commands

```bash
pytest                                  # all tests (tests/ — asyncio mode is auto, no markers needed)
pytest tests/test_orm.py -v             # single file
pytest tests/test_orm.py::test_name     # single test
pytest --cov=zeeb_orm --cov=zeeb_api    # coverage
ruff check .                            # lint (line-length 100, rules E,F,I,N,W,UP)
mypy zeeb_orm zeeb_api zeeb_agents      # type check (strict mode)
```

CLI entry points (both map to `zeeb_orm.cli.main:main`):

```bash
zeeb startproject <name>
zeeb-manage startapp <name> | init | makemigrations | migrate | showmigrations |
            createsuperuser | check | shell | runserver
```

Demo project: `cd demo_blog && python manage.py runserver`. Runnable examples live at the repo root (`example_basic.py`, `example_api.py`, `example_queries.py`, `example_relationships.py`, `example_migrations.py`).

## Architecture

### zeeb_orm

- `models/base.py` — `ModelBase` metaclass: collects fields, processes `class Meta`, builds the SQLAlchemy table. Models get UUID PKs by default.
- `models/fields.py` — Django-style field types mapping to SQLAlchemy columns (incl. `ForeignKey`, `OneToOneField`, `ManyToManyField`).
- `models/manager.py` + `query/queryset.py` — `Model.objects` manager returning async QuerySets (filter with `__lookups`, `select_related`/`prefetch_related`, `get_or_create`, slicing, aggregation).
- `query/q.py`, `query/expressions.py` — `Q` objects (`&`, `|`, `~`) and `F` expressions / aggregates compiled to SQLAlchemy expressions.
- `db/connection.py` — `Database` wrapper around AsyncEngine/AsyncSession; configured via `configure(database={...})` or `DATABASE_URL` env var (asyncpg / aiomysql / aiosqlite).
- `signals.py` — Django-style signals (`pre_save`, `post_save`, `pre_delete`, `post_delete`); `pre_*` fires before flush and can abort, `post_*` fires after commit; `on_commit()` hook available.
- `migrations/` — Alembic-based: `autodetector.py` diffs models vs. schema state, `writer.py` emits migration files, `executor.py` applies them, `optimizer.py` collapses operations. Driven by the `zeeb-manage` CLI in `cli/commands/`.

### zeeb_api

- `serializers/` — `ModelSerializer` generates Pydantic validation from ORM models; `SerializerMethodField`, `read_only_fields` etc. follow DRF conventions.
- `viewsets/` + `routers/` — `ModelViewSet` (CRUD + a `POST /query/` endpoint accepting serialized `Q` filter strings, parsed in `query/`); `DefaultRouter.register()` generates FastAPI routes, routers nest via `router.include()`.
- `auth/` — JWT (PyJWT) with access/refresh tokens, `configure_jwt()`, `JWTAuthMiddleware` (sets `request.state.user`), bcrypt password hashing, Django-like `User`/`Permission` models, `auth_router` with login/refresh/logout/me endpoints.
- `permissions/` — DRF-style permission classes checked by viewsets (`IsAuthenticated`, `IsAdminUser`, ..., custom via `BasePermission`).
- `exceptions.py` + `exception_handlers.py` — standardized error envelope with i18n-oriented error codes (`AUTH_*`, `PERM_*`, `FIELD_*`, `RESOURCE_*`, `QUERY_*`, `SERVER_*`). New error cases should use this hierarchy, not ad-hoc HTTPExceptions.

### zeeb_agents

Every function returns `AgentResult(success, message, data)` (truthy on success) and must not raise — errors go into `message`. Functions operate on a generated Zeeb project on disk (scaffolding writes into `apps/<app>/`). `resources.py` dispatches MCP resource docs from `agent_docs/*.md`.

### Generated project layout (what scaffolding/CLI assume)

```
myproject/
├── manage.py
├── myproject/{settings.py, urls.py, asgi.py}
├── apps/<app>/{models.py, serializers.py, views.py, urls.py}
└── migrations/versions/
```

## Conventions

- Always update `/docs` when you add/change/remove features (`docs/orm/`, `docs/api/`, `docs/cli/`, `docs/configuration/`, `docs/reference/`).
- Configuration flows from env vars (`DATABASE_URL`, `DATABASE_ECHO`, `DEBUG`, `SECRET_KEY`, `LOG_LEVEL`, `JSON_LOGS`) or a project `settings.py`; library code reads it via `zeeb_orm.conf.settings`.
- The three packages layer strictly: zeeb_api depends on zeeb_orm; zeeb_agents depends on both. Don't introduce reverse dependencies.
