"""The files that tell a coding agent what a generated project is.

A checked-out project used to contain nothing that oriented an agent: all the
framework knowledge lived behind the MCP layer, so Claude Code, Cursor, Codex
or anything else working with plain file and shell tools started from zero and
filled the gap with half-remembered Django and DRF idioms — the exact
vocabulary this framework does not have.

``AGENTS.md`` is the cross-vendor convention and the single source of truth.
``CLAUDE.md`` and ``.cursor/rules/zeebpy.mdc`` are the vendor entry points; the
cursor rule carries the *same rendered body*, generated from the same constant,
so the second surface cannot drift from the first.

Rendered by :func:`str.replace` on ``@PROJECT@`` rather than by ``str.format``:
these are markdown documents full of JSON, f-strings and ``{id}`` path params,
and doubling every brace in prose is a standing correction hazard with no
``compile()`` to catch a mistake.

Deliberately absent: an inventory of the project's apps, models or routes. An
agent trusts an instruction file more than it trusts data, so a stale app list
misleads worse than no list at all. The file names the command that reports the
live inventory instead.
"""

from __future__ import annotations

PROJECT_PLACEHOLDER = "@PROJECT@"

AGENTS_MD = """\
# @PROJECT@

An async HTTP API built on **zeebpy**: a Django-shaped ORM over SQLAlchemy 2.0
and a DRF-shaped API layer over FastAPI. It is *shaped* like Django, it is not
Django. `django.db`, `rest_framework`, `settings.configure()` and
`collectstatic` do not exist here — never `pip install django`, and never reach
for a Django or DRF import when something is missing.

## Already built — do not rebuild any of this

- **JWT authentication.** `POST /api/v1/auth/register`, `/auth/login`,
  `/auth/refresh`, `/auth/logout` and `GET /auth/me`. Login returns
  `access_token` + `refresh_token`; send `Authorization: Bearer <token>`.
- **This project's user model** in `apps/accounts/models.py`, already wired as
  `AUTH_USER_MODEL = "accounts.User"`.
- **Permission classes** in `zeeb_api.permissions`: `AllowAny`,
  `IsAuthenticated`, `IsAdminUser`, `IsAuthenticatedOrReadOnly`, `IsOwner`,
  `IsOwnerOrReadOnly`, `ModelPermissions`. They are ANDed.
- **CORS**, from `CORS_ALLOW_ORIGINS` or `CORS_ALLOW_ORIGIN_REGEX` in `.env`.
- **Rate limiting**: 120/min anonymous, 1200/min authenticated, 10/min on login.
- **API versioning** through the `X-API-Version` header; an unknown version is
  rejected with 400.
- **Health probes** `GET /health` and `GET /ready`, at the **root** — not under
  the API prefix.
- **A standard error envelope** on every error, validation to crash:
  `{"success": false, "error": {"code": ..., "message": ...}}`.
- **Rotating logs** in `logs/`.

All of it is configured through `.env` (see `.env.example`). Do not add a second
auth stack, a second CORS middleware, or an error shape of your own — anything
that bypasses the envelope breaks every client that branches on `error.code`.

## Commands

```bash
pip install -r requirements.txt
pytest -q                              # passes on a fresh clone, no DB setup needed
python manage.py makemigrations        # after any model change
python manage.py migrate
python manage.py check                 # names the exact next command when it fails
python manage.py runserver             # http://127.0.0.1:9000

python manage.py startapp <name>               # a new app
python manage.py startapp <name> --model Post  # ...plus a working resource and a test
python manage.py showurls                      # what can I call?
python manage.py inspect --json                # live apps, models, routes, settings
python manage.py frontend-brief                # paste into a frontend generator
python manage.py createsuperuser
python manage.py makemigrations --check        # "did I forget a migration?" (CI)
ruff check .
```

Add `--json` to `check`, `inspect`, `showurls`, `showmigrations`,
`makemigrations`, `migrate`, `startapp` and `frontend-brief` to get one parseable
object on stdout instead of prose. Every failure carries an `error_code` and a
`next_command`.

## Layout

```
manage.py                  every command runs through this
@PROJECT@/settings.py      all configuration, every value read from .env
@PROJECT@/urls.py          the project router; startapp appends app includes here
@PROJECT@/asgi.py          create_app("@PROJECT@.settings")
apps/accounts/             this project's user model — already wired
apps/<app>/                models.py, serializers.py, views.py, urls.py
tests/                     conftest.py holds the shared fixtures; pytest collects here only
migrations/                FLAT — there is no versions/ subdirectory
logs/                      rotating application logs
.env                       secrets and configuration (gitignored)
```

## Adding an entity end to end

`python manage.py startapp blog --model Post` does all of this for you. By hand:

**1. The model** — `apps/blog/models.py`

```python
from zeeb_orm import Model, fields


class Post(Model):
    title = fields.CharField(max_length=200)
    body = fields.TextField(blank=True)
    published = fields.BooleanField(default=False)
    owner = fields.ForeignKey("accounts.User", on_delete="CASCADE", related_name="posts")
    created_at = fields.DateTimeField(auto_now_add=True)

    class Meta:
        table_name = "blog_post"
        ordering = ["-created_at"]
```

**2. The serializer** — `apps/blog/serializers.py`

```python
from zeeb_api import serializers

from .models import Post


class PostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ["id", "title", "body", "published", "owner", "created_at"]
        read_only_fields = ["id", "owner", "created_at"]
```

**3. The viewset** — `apps/blog/views.py`

```python
from zeeb_api import permissions, viewsets

from .models import Post
from .serializers import PostSerializer


class PostViewSet(viewsets.ModelViewSet):
    queryset = Post.objects.all()
    serializer_class = PostSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    async def perform_create(self, serializer: PostSerializer) -> None:
        await serializer.save(owner_id=self.request.state.user.id)
```

**4. The route** — `apps/blog/urls.py`

```python
router.register("posts", PostViewSet)
```

**5. Migrate and verify**

```bash
python manage.py makemigrations && python manage.py migrate
pytest -q
```

That serves `GET/POST /api/v1/posts`, `GET/PUT/PATCH/DELETE /api/v1/posts/{id}`
and `POST /api/v1/posts/query`.

## Conventions

- **Everything is async.** Every ORM call is awaited; viewset methods are
  `async def`. Never a blocking database call, never `time.sleep`.
- `queryset = Post.objects.all()` — a queryset, not the bare manager.
- Foreign keys to the user model use the labelled name `"accounts.User"`, never
  a bare `"User"`: the framework ships a class with that name and the bare form
  resolves by import order. Cross-app references work the same: `"blog.Post"`.
- `read_only_fields` is the write boundary. Server-owned values (owner,
  timestamps, status) are stamped in `perform_create`, never taken from the body.
- Canonical paths have **no trailing slash** (`/api/v1/posts`). A trailing slash
  is also served, but generate clients from the slash-less form.
- Raise from `zeeb_api.exceptions` (`NotFound`, `ValidationError`,
  `PermissionDenied`, `ResourceConflictException`) — never a bare
  `HTTPException`. That is what keeps responses inside the error envelope.
- Keep every setting in `@PROJECT@/settings.py` on **one line**: the tooling rewrites
  settings with a single-line pattern and cannot see a wrapped assignment.
- Configuration comes from `.env`; the literal in `@PROJECT@/settings.py` is the
  default.

## Don't

- Don't hand-edit anything in `migrations/`. Change the model, re-run
  `makemigrations`.
- Don't create `migrations/versions/` — migrations are flat.
- Don't put secrets in `@PROJECT@/settings.py`. They belong in `.env`, which is
  gitignored.
- Don't rename or remove the module-level `router` symbol or `get_routes()` in
  `@PROJECT@/urls.py`. The wiring tools refuse a file without them, and
  `startapp` then cannot register anything.
- Don't commit `.env`.

## Reading the framework itself

zeebpy is installed and importable — read its source when the exact behaviour
matters, rather than guessing:

```bash
python -c "import zeeb_api, os; print(os.path.dirname(zeeb_api.__file__))"
```

Start with `viewsets/base.py`, `serializers/pydantic.py`, `routers/default.py`
and `exceptions.py`.

## Verifying a change

```bash
pytest -q
python manage.py makemigrations --check   # fails if a model change has no migration
python manage.py check
ruff check .
```
"""

CLAUDE_MD = """\
# CLAUDE.md

See @AGENTS.md — the single source of truth for this project's conventions,
commands and layout.

Edit `AGENTS.md`; this file is only a pointer.
"""

CURSOR_RULES_FRONTMATTER = """\
---
description: >-
  Conventions, commands and layout for this zeebpy project. Generated by
  `zeeb startproject` from the same source as AGENTS.md — edit AGENTS.md and
  keep the two in sync.
alwaysApply: true
---

"""


def render_agents_md(project_name: str) -> str:
    """``AGENTS.md`` for a project named *project_name*."""
    return AGENTS_MD.replace(PROJECT_PLACEHOLDER, project_name)


def render_cursor_rules(project_name: str) -> str:
    """The Cursor rule file: frontmatter plus the rendered ``AGENTS.md`` body.

    One body, two files. Cursor does not reliably follow a pointer to another
    document, so the content is duplicated — but it is generated, never written
    twice, and a test holds the two byte-identical below the frontmatter.
    """
    return CURSOR_RULES_FRONTMATTER + render_agents_md(project_name)
