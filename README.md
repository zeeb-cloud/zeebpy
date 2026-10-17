<div align="center">

# zeebpy

**The async-first Python framework for APIs that ship on day one.**

A model-driven ORM, a declarative API layer and an agent toolkit that can build features for you,<br>
all async from the database driver to the HTTP response.

[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0%20async-D71F00?logo=sqlalchemy&logoColor=white)](docs/orm/models.md)
[![FastAPI](https://img.shields.io/badge/FastAPI-Pydantic%20v2-009688?logo=fastapi&logoColor=white)](docs/api/viewsets.md)
[![Docs coverage](https://img.shields.io/badge/docs%20coverage-100%25-brightgreen)](tests/test_docs_coverage.py)
[![Lint: ruff](https://img.shields.io/badge/lint-ruff-D7FF64?logo=ruff&logoColor=black)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](pyproject.toml)

[Quickstart](#-quickstart) · [Tour](#-a-quick-tour) · [Agents](#-zeeb_agents-a-framework-your-coding-agent-can-drive) · [Docs](#-documentation) · [Contributing](#-development)

</div>

---

```bash
zeeb startproject shop && cd shop
zeeb-manage startapp catalog --model Product
zeeb-manage makemigrations --name initial && zeeb-manage migrate
zeeb-manage runserver        # → http://127.0.0.1:9000/docs
```

Those four lines give you a running API with a user model, JWT auth, CORS, rate
limiting, API versioning, health probes, a uniform error envelope, rotating
logs, a full CRUD resource with its own passing tests, and an `AGENTS.md` that
tells your coding agent how the project works.

## ✨ Why zeebpy

<table>
<tr>
<td width="33%" valign="top">

### 🗄️ `zeeb_orm`
A model-first ORM on **SQLAlchemy 2.0 async**.

- Chainable, lazy QuerySets you `await`
- `Q`, `F`, `Case`/`When`, subqueries, window functions
- Relation traversal: `author__name__startswith`
- `ForeignKey`, `OneToOneField`, `ManyToManyField` with every `on_delete` mode
- Signals, `atomic()`, `on_commit()`
- Autodetected **Alembic** migrations, rename detection, squashing
- PostgreSQL · MySQL · SQLite

</td>
<td width="33%" valign="top">

### ⚡ `zeeb_api`
Declarative REST on **FastAPI + Pydantic v2**.

- `ModelSerializer` derives validation from the model
- `ModelViewSet` + routers give you CRUD and a `POST /query` endpoint
- JWT access/refresh tokens, bcrypt, users and permissions
- OAuth2/OIDC: Azure AD, Google, GitHub (PKCE)
- Permissions, throttling, versioning, filtering, pagination
- One error envelope with i18n-ready codes

</td>
<td width="33%" valign="top">

### 🤖 `zeeb_agents`
**130+ async tools** for driving a project from code.

- Scaffolding, migrations, database, users, logs, deployment
- A feature compiler: spec → models, API, migration, tests
- Every call returns an `AgentResult` and never raises
- Tiered surface for LLM agents (22 core tools)
- Ready for MCP, with **zero** MCP dependency

</td>
</tr>
</table>

## 🚀 Quickstart

```bash
pip install "zeebpy[sqlite] @ git+https://github.com/zeeb-cloud/zeebpy.git"
```

<details>
<summary>Other databases and extras</summary>

```bash
pip install "zeebpy[postgresql] @ git+https://github.com/zeeb-cloud/zeebpy.git"  # asyncpg + psycopg2
pip install "zeebpy[mysql] @ git+https://github.com/zeeb-cloud/zeebpy.git"       # aiomysql + pymysql
pip install "zeebpy[oauth] @ git+https://github.com/zeeb-cloud/zeebpy.git"       # httpx, pyjwt[crypto]
pip install "zeebpy[all] @ git+https://github.com/zeeb-cloud/zeebpy.git"         # everything
```

</details>

Create a project and an app. `startapp` registers itself in `INSTALLED_APPS` and
`urls.py`, so there's nothing to wire up by hand:

```bash
zeeb startproject shop
cd shop
zeeb-manage startapp catalog --model Product   # model, serializer, viewset, route, tests
zeeb-manage makemigrations --name initial
zeeb-manage migrate
pytest                                          # the generated suite passes
zeeb-manage runserver                           # http://127.0.0.1:9000
```

Authentication is live immediately:

```bash
curl -X POST localhost:9000/api/v1/auth/register \
  -H 'content-type: application/json' \
  -d '{"email": "me@example.com", "password": "s3cret-pass-123"}'

curl -X POST localhost:9000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email": "me@example.com", "password": "s3cret-pass-123"}'
# → {"access_token": "eyJ…", "refresh_token": "eyJ…", …}

curl -X POST localhost:9000/api/v1/products \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"title": "Espresso", "slug": "espresso", "body": "Short and strong.", "published": true}'
```

<details>
<summary>What <code>startproject</code> generates</summary>

```text
shop/
├── manage.py
├── .env / .env.example     # every setting, env-driven; .env holds a generated SECRET_KEY
├── AGENTS.md               # conventions for coding agents (CLAUDE.md + .cursor/rules point here)
├── pyproject.toml          # [tool.zeeb] marker + ruff config
├── pytest.ini
├── requirements.txt
├── shop/
│   ├── settings.py         # one line per setting, read from .env
│   ├── urls.py             # auth, OAuth and app routers
│   └── asgi.py             # app = create_app("shop.settings")
├── apps/
│   └── accounts/           # your user model (AUTH_USER_MODEL = "accounts.User")
├── tests/                  # conftest builds the schema from the models, no migration needed
├── logs/
└── migrations/
```

Every app is five files: `__init__.py`, `models.py`, `serializers.py`, `views.py`
and `urls.py`. The project passes `ruff check .` and `pytest` before its first
migration.

</details>

## 🧭 A quick tour

### Models and queries

```python
from zeeb_orm import Count, F, Model, Q, Rank, Window, atomic, fields


class Author(Model):
    name = fields.CharField(max_length=100)
    email = fields.EmailField(unique=True)


class Post(Model):
    title = fields.CharField(max_length=200)
    author = fields.ForeignKey(Author, on_delete="CASCADE", related_name="posts")
    published = fields.BooleanField(default=False)
    likes = fields.IntegerField(default=0)
    dislikes = fields.IntegerField(default=0)
    created_at = fields.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


async def tour():
    async with atomic():                       # both rows, or neither
        ada = await Author.objects.create(name="Ada", email="ada@example.com")
        await Post.objects.create(title="Hello, async", author=ada, published=True, likes=12)

    # Chainable and lazy; nothing runs until you await
    popular = await (
        Post.objects.filter(Q(published=True) | Q(likes__gt=F("dislikes")))
        .select_related("author")
        .order_by("-likes")
    )

    # Traverse relations, aggregate, rank
    prolific = await Author.objects.annotate(n=Count("posts")).filter(n__gte=2)
    ranked = await Post.objects.annotate(
        rank=Window(Rank(), order_by="-likes")
    ).values("title", "rank")
    this_year = await Post.objects.filter(author__name__startswith="A", created_at__year=2026)

    # Set-based updates with field references
    await Post.objects.filter(published=False).update(likes=F("likes") + 1)
```

### An API in three small files

This is what `zeeb-manage startapp catalog --model Product` writes:

```python
# apps/catalog/serializers.py
from zeeb_api import serializers

from .models import Product


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["id", "title", "slug", "body", "published", "owner", "created_at", "updated_at"]
        # Server-owned: returned, never accepted from the request body.
        read_only_fields = ["id", "owner", "created_at", "updated_at"]
```

```python
# apps/catalog/views.py
from zeeb_api import permissions, viewsets

from .models import Product
from .serializers import ProductSerializer


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    async def perform_create(self, serializer: ProductSerializer) -> None:
        """Stamp the authenticated caller as owner; never trust the body."""
        await serializer.save(owner_id=self.request.state.user.id)
```

```python
# apps/catalog/urls.py
from zeeb_api.routers import DefaultRouter

from .views import ProductViewSet

router = DefaultRouter()
router.register("products", ProductViewSet)
```

That gives you `list`, `retrieve`, `create`, `update`, `partial_update`,
`destroy`, and a query endpoint that takes a `Q` expression as a string. The
expression is parsed safely, not evaluated:

```http
POST /api/v1/products/query
Authorization: Bearer eyJ…

{"filter": "Q(published=True) & Q(title__icontains=\"esp\")", "order_by": ["-created_at"], "limit": 20}
```

```json
{"count": 1, "limit": 20, "offset": 0, "results": [{"id": "c4d7…", "title": "Espresso", "...": "..."}]}
```

### One error shape, everywhere

Validation, auth, permissions, throttling and unexpected crashes all use the same
envelope. Error codes are stable and ready for translation (`AUTH_*`, `PERM_*`,
`FIELD_*`, `RESOURCE_*`, `QUERY_*`, `SERVER_*`):

```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": [{"code": "FIELD_REQUIRED", "field": "body", "message": "Field required", "meta": null}],
    "meta": {"request_id": "cabe8d14-…", "timestamp": "2026-10-17T21:59:56Z", "path": "/api/v1/products", "method": "POST"}
  }
}
```

### Configure everything from `.env`

```bash
DEBUG=false                     # insecure default secrets are refused when false
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/shop
CORS_ALLOW_ORIGINS=https://app.example.com
AUTH_LOGIN_THROTTLE_RATE=10/min
GOOGLE_CLIENT_ID=…              # setting a client id mounts that provider's OAuth routes
```

Real environment variables always override `.env`. Every setting is listed in
[Settings](docs/configuration/settings.md).

## 🤖 `zeeb_agents`: a framework your coding agent can drive

`zeeb_agents` is a plain async Python library. Each tool takes structured input,
works on a project on disk, and returns an `AgentResult(success, message, data)`.
It never raises: a failure comes back with an `error_code`, suggestions and the
next step to take.

```python
from pathlib import Path

import zeeb_agents
from zeeb_agents import build_feature, list_features

zeeb_agents.configure(project_resolver=lambda pid: Path("/srv/projects") / pid)


async def add_blog():
    # One call compiles the spec, writes the model, serializer, viewset, route
    # and tests, generates and applies the migration, then verifies the project.
    result = await build_feature({
        "name": "blog",
        "entities": [{"name": "Article", "fields": [
            {"name": "title", "type": "string", "max_length": 200},
            {"name": "body", "type": "text"},
        ]}],
    }, project_id="shop")

    if result:                                  # AgentResult is truthy on success
        print(result.data["affected"])          # {"apps": [...], "entities": [...], "files": [...]}
    else:
        print(result.data["error_code"], result.data.get("problems"))

    features = await list_features(project_id="shop")
```

- **Feature lifecycle.** `plan_feature` (dry run), `build_feature`,
  `change_feature`, `deactivate_feature` (archives the code and keeps the data),
  `activate_feature`, `delete_feature` (needs `confirm=True`).
- **Per-object tools.** `create_model`, `add_field`, `generate_crud`,
  `make_migrations`, `run_query` (read-only, behind a SQL gate),
  `read_logs`, `create_user`, `generate_dockerfile`, and around 120 more.
- **Tiers.** `list_capabilities(tier="core")` returns the 22 tools an LLM agent
  should see first. The rest stay importable and supported.
- **Safe by construction.** Every path is confined to the project root, generated
  code is built from validated identifiers, and every `.py` edit is atomic and
  rejected if the result wouldn't parse.

Full reference: [Agent functions](docs/cli/agents.md).

## 🏗️ Architecture

```mermaid
flowchart LR
    agents["<b>zeeb_agents</b><br/>130+ async tools<br/>feature compiler"]
    api["<b>zeeb_api</b><br/>serializers · viewsets · routers<br/>auth · OAuth · permissions"]
    orm["<b>zeeb_orm</b><br/>models · QuerySets · expressions<br/>signals · migrations"]
    fastapi["FastAPI + Pydantic v2"]
    sa["SQLAlchemy 2.0 async + Alembic"]
    db[("PostgreSQL · MySQL · SQLite")]

    agents --> api
    agents --> orm
    api --> orm
    api --> fastapi
    orm --> sa --> db
```

The three packages ship together and depend on each other in one direction only:
`zeeb_agents` → `zeeb_api` → `zeeb_orm`.

## 🛠️ CLI

| Command | What it does |
| --- | --- |
| `zeeb startproject <name>` | New project with auth, CORS, throttling, versioning, health probes and logs |
| `zeeb-manage startapp <name> [--model <Name>]` | New app, registered automatically; `--model` adds a working resource and tests |
| `zeeb-manage makemigrations` / `migrate` | Detect model changes and apply them |
| `zeeb-manage showmigrations` / `squashmigrations` | Inspect and compact migration history |
| `zeeb-manage showurls` / `inspect` | Every route; the project as structured data |
| `zeeb-manage frontend-brief` | A brief of the API contract for a frontend (or the agent building one) |
| `zeeb-manage createsuperuser` / `check` / `shell` / `runserver` | Everyday management |

Commands that report a result accept `--json` and return the same
`success`/`message`/`data` envelope as the agent tools. See [CLI commands](docs/cli/commands.md).

## 📚 Documentation

| | |
| --- | --- |
| **Start** | [Installation](docs/installation.md) · [Quickstart tutorial](docs/quickstart.md) · [Docs index](docs/readme.md) |
| **ORM** | [Models](docs/orm/models.md) · [Fields](docs/orm/fields.md) · [Queries](docs/orm/queries.md) · [Expressions](docs/orm/expressions.md) · [Relationships](docs/orm/relationships.md) · [Migrations](docs/orm/migrations.md) · [Signals](docs/orm/signals.md) · [Object permissions](docs/orm/permissions.md) · [Testing](docs/orm/testing.md) |
| **API** | [Serializers](docs/api/serializers.md) · [ViewSets & routing](docs/api/viewsets.md) · [Authentication](docs/api/authentication.md) · [OAuth2 / OIDC](docs/api/oauth.md) · [Permissions](docs/api/permissions.md) · [Filtering](docs/api/filtering.md) · [Pagination](docs/api/pagination.md) · [Throttling](docs/api/throttling.md) · [Versioning](docs/api/versioning.md) · [Errors](docs/api/errors.md) |
| **Tools** | [CLI commands](docs/cli/commands.md) · [Agent functions](docs/cli/agents.md) |
| **Configuration** | [Settings](docs/configuration/settings.md) · [Database](docs/configuration/database.md) · [Logging](docs/configuration/logging.md) |
| **Reference** | [Field types](docs/reference/field-types.md) · [QuerySet API](docs/reference/queryset-api.md) · [Expressions](docs/reference/expressions.md) · [ORM feature reference](docs/orm/feature-reference.md) |

Runnable examples live at the repository root: [`example_basic.py`](example_basic.py),
[`example_queries.py`](example_queries.py), [`example_relationships.py`](example_relationships.py),
[`example_api.py`](example_api.py) and [`example_migrations.py`](example_migrations.py).
[`demo_blog/`](demo_blog) is a complete project.

### Docs that can't drift

Documentation coverage is **100%**, and the test suite keeps it there:

| Guard | What it enforces |
| --- | --- |
| [`test_docs_coverage.py`](tests/test_docs_coverage.py) | Every public export of every public module is documented under `docs/`. Every public module, class, function and method has a docstring. Every page is linked from the index. |
| [`test_docs_examples_drift.py`](tests/test_docs_examples_drift.py) | Every documented `import` resolves, every `python` block parses, and every relative link resolves (this README included). |
| [`test_agents_doc_drift.py`](tests/test_agents_doc_drift.py) | Every agent tool is documented with its real signature. |
| [`test_cli_doc_drift.py`](tests/test_cli_doc_drift.py) / [`test_settings_doc_drift.py`](tests/test_settings_doc_drift.py) | Every CLI command, option and setting is documented, and nothing invented is. |

## 🔧 Development

```bash
pip install -e ".[dev,sqlite]"
pytest                                   # the whole suite (asyncio mode is auto)
pytest --cov=zeeb_orm --cov=zeeb_api     # with coverage
ruff check .                             # lint
mypy zeeb_orm zeeb_api zeeb_agents       # advisory type check
```

When you add or change a feature, update `docs/` in the same change. The guards
above will tell you what's missing. Contributor and agent conventions are in
[AGENTS.md](AGENTS.md).

## 📄 License

MIT
