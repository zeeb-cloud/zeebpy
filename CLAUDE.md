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
mypy zeeb_orm zeeb_api zeeb_agents      # type check (advisory — ~290 known findings
                                        # in metaclass-heavy internals; don't add new ones)
```

CLI entry points (both map to `zeeb_orm.cli.main:main`):

```bash
zeeb startproject <name>
zeeb-manage startapp <name> [--model <Name>] | init | makemigrations | migrate |
            showmigrations | squashmigrations | showurls | inspect |
            frontend-brief | createsuperuser | check | shell | runserver
```

`zeeb_orm/cli/main.py` owns `build_parser()` and the `COMMANDS` tuple — the
documentation drift test compares `docs/cli/commands.md` against them, so a new
subcommand must be added to both. Commands that report a result take `--json`
and go through `zeeb_orm/cli/output.py`, which emits the same
`success`/`message`/`data` envelope as `AgentResult` and *requires* a
`next_command` on every failure.

Demo project: `cd demo_blog && python manage.py runserver`. Runnable examples live at the repo root (`example_basic.py`, `example_api.py`, `example_queries.py`, `example_relationships.py`, `example_migrations.py`).

## Architecture

### zeeb_orm

- `models/base.py` — `ModelBase` metaclass: collects fields, processes `class Meta`. Models get UUID PKs by default. Split-out helpers: `models/sa_builder.py` (SQLAlchemy table/DDL incl. Meta constraints + M2M through tables), `models/relations.py` (`resolve_relation`, pending-relation registry — `_process_pending_relations` must stay importable from `base`), `models/permissions_gen.py`, `models/deletion.py` (on_delete constants + `Collector`), `models/related_m2m.py` (`ManyRelatedManager`).
- `models/fields.py` — Django-style field types mapping to SQLAlchemy columns (incl. `ForeignKey` with all `on_delete` variants, `OneToOneField`, full `ManyToManyField`); `choices`/`validators` are enforced on save (see `validators.py`, `Model.full_clean`; `validate=False` opts out).
- `models/manager.py` + `query/queryset.py` — `Model.objects` manager returning async QuerySets (related-field traversal `author__name__gte`, datetime transforms `created__year`, `in_bulk`/`iterator`/`union`/`select_for_update`/`explain`, `Manager.from_queryset`/`as_manager`). Join machinery in `query/joins.py` (`JoinContext`, aliases shared with `select_related`); path parsing in `query/q.py::parse_path`; datetime transforms in `query/transforms.py` (per-dialect `@compiles`).
- `query/expressions.py` — `F`, `Q`, `Case`/`When`, `Subquery`/`OuterRef`/`Exists`, window functions (`Window`, `Rank`, `Lag`, …), `Cast`, `StringAgg`, aggregates.
- `exceptions.py` — `ValidationError`, `FieldError`, `ProtectedError`, `NotSupportedError`, … (canonical; `DoesNotExist` also re-exported here).
- `db/connection.py` — `Database` wrapper around AsyncEngine/AsyncSession; configured via `configure(database={...})` or `DATABASE_URL` env var (asyncpg / aiomysql / aiosqlite).
- `signals.py` — Django-style signals (`pre_save`, `post_save`, `pre_delete`, `post_delete`); `pre_*` fires before flush and can abort, `post_*` fires after commit; `on_commit()` hook available.
- `migrations/` — Alembic-based: `autodetector.py` diffs models vs. schema state, `writer.py` emits migration files, `executor.py` applies them, `optimizer.py` collapses operations. Driven by the `zeeb-manage` CLI in `cli/commands/`.
- Feature-parity status vs. Django (incl. deliberate omissions): `docs/orm/feature-reference.md`.

### zeeb_api

- `serializers/` — `ModelSerializer` generates Pydantic validation from ORM models; `SerializerMethodField`, `read_only_fields` etc. follow DRF conventions.
- `viewsets/` + `routers/` — `ModelViewSet` (CRUD + a `POST /query/` endpoint accepting serialized `Q` filter strings, parsed in `query/`); `DefaultRouter.register()` generates FastAPI routes, routers nest via `router.include()`.
- `auth/` — JWT (PyJWT) with access/refresh tokens, `configure_jwt()`, `JWTAuthMiddleware` (sets `request.state.user`), bcrypt password hashing, Django-like `User`/`Permission` models, `auth_router` with login/refresh/logout/me endpoints. Insecure default secrets are refused when `DEBUG=false` (`InsecureSecretError`; `create_app` fails fast).
- `auth/oauth/` — OAuth2/OIDC layer (optional extra `zeebpy[oauth]`: httpx + pyjwt[crypto] + python-multipart): generic `OAuthProvider` (PKCE, discovery, async JWKS validation), `AzureADProvider`/`GoogleProvider`/`GitHubProvider` presets, `ExternalIdentity` model, `create_oauth_router` (browser + SPA flows), `ExternalTokenValidator` for externally-issued bearer tokens. All exports are PEP-562 lazy — plain `import zeeb_api` must not require httpx or register the `auth_external_identities` table.
- `permissions/` — DRF-style permission classes checked by viewsets (`IsAuthenticated`, `IsAdminUser`, ..., custom via `BasePermission`).
- `throttling/` — DRF-style rate limiting (`AnonRateThrottle`/`UserRateThrottle`/`ScopedRateThrottle`, `throttle_classes` on viewsets, `throttle()` dependency for plain routes); the default cache is in-memory **per process** — multi-worker deployments need a custom `BaseThrottleCache`.
- `versioning/` — `URLPathVersioning`/`HeaderVersioning`/`QueryParameterVersioning`/`AcceptHeaderVersioning`, `VersioningMiddleware` (sets `request.state.version` / `viewset.version`), `get_api_version` dependency.
- `exceptions.py` + `exception_handlers.py` — canonical, standardized error envelope with i18n-oriented error codes (`AUTH_*`, `PERM_*`, `FIELD_*`, `RESOURCE_*`, `QUERY_*`, `SERVER_*`). New error cases should use this hierarchy, not ad-hoc HTTPExceptions. `zeeb_api/response.py` is a deprecated shim — never import exceptions from it in new code.

### zeeb_agents

Every function returns `AgentResult(success, message, data)` (truthy on success) and must not raise — this is enforced by the `@agent_function` decorator (`_utils/decorators.py`: resolves `project_root`, logs failures to the `"zeeb_agents"` logger, wraps exceptions into `AgentResult`). All public functions use it; function signatures, names, and existing `data` keys are the MCP-facing contract — you may enrich docstrings additively (e.g. document the `data` shape, special cases) but must not break the contract (no renamed/removed functions or `data` keys, no signature changes; a *new optional* parameter must be appended, never inserted, so positional callers keep working). New `data` keys are fine when additive. Follow the `Returns data:`/`Notes:` docstring convention used across the modules, and document the return-shape conventions in `agent_docs/principles.md`. Functions operate on a generated Zeeb project on disk (scaffolding writes into `apps/<app>/`). `run_query` enforces a read-only SQL gate (comment stripping, single-statement, keyword denylist, always-rollback transaction); `files.py` rejects paths escaping the project root. `resources.py` dispatches MCP resource docs from `agent_docs/*.md`; `capabilities.py::list_capabilities` introspects `__all__` for machine-readable tool discovery.

**Tiers — which tools an agent should see.** `tiers.py` is the single source of truth: `CORE_TOOLS` (~20 declarative tools), `ESCAPE_HATCH_TOOLS` (5), and everything else implicitly `advanced`. `list_capabilities` reports a `tier` per tool and filters on `tier=`; an MCP server registers `DEFAULT_SURFACE` and keeps the rest dispatchable. **Advanced is not deprecated** — the feature compiler executes exactly those functions, and they stay importable and supported. Re-tiering a tool is a presentation change and is allowed; removing or renaming one is not. Keep `tiers.py` in sync with the sibling `zeeb-mcp` (`libs/zeeb_codegen/tools.py`), whose `test_capability_partition_is_total` fails if a new export is unmapped.

**Features.** `intent.py` owns the lifecycle (`build_feature`/`change_feature`/`list_features`/`deactivate_feature`/`activate_feature`/`delete_feature`); `feature_spec.py` is the private compiler+executor; `feature_manifest.py` records per-artifact ownership in the project's `.zeeb/features.json`; `feature_archive.py` does the file surgery for archive/restore into `.zeeb/archive/<feature>/`. Two invariants: features may **share an app** (so archiving is surgical AST edits, never directory moves — ownership decides what moves), and **deactivate never touches `models.py`** (the schema stays, no migration is generated, no data can be lost — dropping tables is `delete_feature`'s job and needs `confirm=True`). `.zeeb/` is committed project state, not build output; keep it out of the scaffold's `.gitignore`. A FeatureSpec's `functions` block (`action`/`endpoint`/`hook`/`task`/`rule`) compiles to the same per-object tools; adding a kind means adding to `FUNCTION_KINDS`, `KNOWN_OPS`, `_OP_REQUIRED`, the executor dispatch, and `functions.py::FUNCTION_FILES`.

### Generated project layout (what scaffolding/CLI assume)

```
myproject/
├── manage.py
├── .env / .env.example          # env-driven config; .env holds a generated SECRET_KEY (0600)
├── AGENTS.md                    # what a coding agent reads; CLAUDE.md + .cursor/rules point here
├── pyproject.toml               # [tool.zeeb] identity marker + ruff config
├── pytest.ini
├── myproject/{settings.py, urls.py, asgi.py}
├── apps/accounts/               # scaffolded user model (AUTH_USER_MODEL = "accounts.User")
├── apps/<app>/{models.py, serializers.py, views.py, urls.py}   # 5 files, no apps.py
├── tests/{conftest.py, test_smoke.py, test_<app>.py}
├── logs/
└── migrations/                  # migration files live FLAT here, not under versions/
```

Templates and the AST wiring helpers live in `zeeb_orm/scaffold/` — one source
of truth for both the CLI and the agent layer. `zeeb_agents/_utils/wiring.py` is
a thin adapter that re-raises `ScaffoldError` as `AgentError` with the same
code. `zeeb_orm.scaffold.project.URLS_PY` *is*
`zeeb_orm.scaffold.wiring.STANDARD_URLS_TEMPLATE`; keep it that way.

`startproject` ships a batteries-on boilerplate: JWT auth endpoints mounted,
CORS, rate limiting, API versioning, health probes, error envelope and rotating
logs, all toggleable through `.env` via `zeeb_api.conf.env`
(`load_env` + `env_str`/`env_bool`/`env_int`/`env_list`). Keep every
env-driven setting on **one line** — `zeeb_agents.manage_settings` rewrites
settings with a single-line regex. `startapp` registers the app it creates
(`INSTALLED_APPS` + project `urls.py`); `--no-wire` opts out, `--model <Name>`
generates a complete working resource plus a passing test.

Three more pieces of `zeeb_orm/scaffold/` are load-bearing:

- `harness.py` owns `pytest.ini` + `tests/conftest.py` + `tests/test_smoke.py`.
  `zeeb_agents.test_scaffold` imports them rather than defining its own, so a
  feature generated by the agent layer runs against the fixtures the CLI shipped.
  A freshly scaffolded project passes `pytest` **before** its first migration —
  the `db` fixture builds the schema from the model registry. Keep that true.
- `agent_guide.py` owns `AGENTS.md`; `CLAUDE.md` and `.cursor/rules/zeebpy.mdc`
  are rendered from the same constant. `tests/test_scaffold_agent_docs.py`
  asserts every path, command and flag it names actually exists.
- The app templates carry their example in the **module docstring**, not as
  commented-out code. That means anything inspecting a generated file must read
  the AST, never the text — see `code_gen.router_registrations` and
  `code_gen.imports_name`. A substring match sees the example and acts on it.

A generated project must stay `ruff check .` clean
(`tests/test_scaffold_boilerplate.py::test_a_generated_project_passes_its_own_lint`).

## Conventions

- Always update `/docs` when you add/change/remove features (`docs/orm/`, `docs/api/`, `docs/cli/`, `docs/configuration/`, `docs/reference/`).
- Configuration is layered: project `settings.py` is read by `zeeb_api.conf.settings` (LazySettings, canonical in-process reader); `zeeb_api.conf.orm.apply_orm_settings()` hands it down to `zeeb_orm.conf.settings` (low-level sink, also fed by env vars `DATABASE_URL`, `DATABASE_ECHO`, …); `zeeb_agents` reads *target* projects off disk by path (`load_project_settings`) and is process-independent by design.
- The three packages layer strictly: zeeb_api depends on zeeb_orm; zeeb_agents depends on both. Don't introduce reverse dependencies.
