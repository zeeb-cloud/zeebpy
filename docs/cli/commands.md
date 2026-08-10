# CLI Commands

Zeeb provides project management commands via `manage.py` or the `zeeb` CLI.

## Project Commands

### startproject

Create a new Zeeb project. The result is a running, authenticated API — you only
have to create and apply its migrations.

```bash
zeeb startproject myproject
```

Creates:
```
myproject/
├── myproject/
│   ├── __init__.py
│   ├── settings.py      # Environment-driven configuration
│   ├── asgi.py          # ASGI application
│   └── urls.py          # Auth + OAuth + app routers
├── apps/
│   └── accounts/        # Your user model, wired via AUTH_USER_MODEL
│       ├── __init__.py
│       ├── models.py
│       ├── serializers.py
│       ├── views.py
│       └── urls.py
├── tests/               # conftest.py with the shared fixtures + a smoke suite
│   ├── __init__.py
│   ├── conftest.py
│   └── test_smoke.py
├── migrations/          # Migration files live flat in here
├── logs/
├── .cursor/rules/       # The AGENTS.md body, for Cursor
├── .env                 # Generated signing key — gitignored, mode 0600
├── .env.example         # Every supported variable
├── .gitignore
├── AGENTS.md            # Conventions, for humans and coding agents
├── CLAUDE.md            # Pointer to AGENTS.md
├── manage.py
├── pyproject.toml       # [tool.zeeb] identity + ruff config
├── pytest.ini
├── README.md
└── requirements.txt
```

`pytest` passes immediately, before any migration has been created: the `db`
fixture builds the schema from the model registry.

Already configured and on, all of it overridable from `.env`: JWT
authentication with `/auth/login`, `/auth/register`, `/auth/refresh`,
`/auth/logout` and `/auth/me`; CORS; per-client rate limiting; API versioning;
`/health` and `/ready` probes; the standard error envelope; and rotating logs.
OAuth2/OIDC is wired but stays unmounted until a provider client id is present
in the environment. See [Settings](../configuration/settings.md).

### startapp

Create a new application within a project, and register it.

```bash
cd myproject
python manage.py startapp blog
```

Creates:
```
apps/blog/
├── __init__.py
├── models.py
├── serializers.py
├── views.py
└── urls.py
tests/test_blog.py       # in tests/, where the shared fixtures apply
```

The files carry the canonical example in their module docstring rather than as
commented-out code, and import nothing they do not use — a freshly scaffolded
project passes `ruff check .`.

It also wires the app up, because an unregistered app silently does nothing:
`"apps.blog"` is appended to `INSTALLED_APPS` (without it `makemigrations`
never sees the app's models) and its router is included in the project
`urls.py` (without it every endpoint the app registers 404s). Both edits are
idempotent.

```bash
python manage.py startapp blog --no-wire   # scaffold the files only
```

**Options:**

| Option | Description |
|--------|-------------|
| `--model NAME` | Also generate a complete working resource: model, serializer, viewset, `router.register(...)` and a test that passes |
| `--no-wire` | Only scaffold the files; skip INSTALLED_APPS and the urls.py include |
| `--json` | Print one JSON object on stdout instead of prose |

```bash
python manage.py startapp blog --model Post
python manage.py makemigrations && python manage.py migrate
pytest tests/test_blog.py -q       # green
# GET/POST /api/v1/posts is now served
```

## Migration Commands

### init

Initialize migrations directory for a new project.

```bash
python manage.py init
```

Creates the `migrations/` directory. Migration files live flat inside it
(`0001_initial.py`, `0002_...`). `startproject` already creates it, and
`makemigrations` creates it on demand, so this command is rarely needed.

### makemigrations

Generate migrations from model changes.

```bash
python manage.py makemigrations                    # detect changes in all apps
python manage.py makemigrations --name add_posts   # name the file
python manage.py makemigrations --empty            # an empty file to edit by hand
python manage.py makemigrations --dry-run          # show it without writing
python manage.py makemigrations --check            # exit 1 if a change has no migration
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--name` | `-n` | Name for the migration file |
| `--empty` | | Create an empty migration to edit by hand |
| `--dry-run` | | Show what would be written; write nothing |
| `--check` | | Exit 1 when model changes have no migration. This is the CI check |
| `--json` | | Print one JSON object on stdout instead of prose |

### migrate

Apply or roll back migrations.

```bash
python manage.py migrate                  # apply everything pending
python manage.py migrate 0003_add_posts   # migrate to a specific file
python manage.py migrate zero             # unapply everything
python manage.py migrate --rollback 1     # step back one migration
python manage.py migrate --plan           # what would run, without running it
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `migration` | | Target migration name, or `zero` |
| `--rollback N` | `-r` | Roll back N migrations |
| `--plan` | | List what would run; change nothing |
| `--fake` | | Record as applied without executing |
| `--fake-initial` | | Skip the initial migration when the tables already exist |
| `--noinput` | | Accepted and ignored: migrate never prompts |
| `--json` | | Print one JSON object on stdout instead of prose |

### showmigrations

Display migration status.

```bash
python manage.py showmigrations
```

Output:
```
 [X] 0001_initial
 [X] 0002_add_profiles
 [ ] 0003_add_comments
```

With `--json`, the same information as
`{"migrations": [{"name": ..., "applied": true}, ...], "applied": 2, "pending": 1}`.

### squashmigrations

Collapse a range of migrations into a single file.

```bash
python manage.py squashmigrations 0001_initial 0005_add_views
python manage.py squashmigrations 0001_initial 0005_add_views --name initial_squashed
python manage.py squashmigrations 0001_initial 0005_add_views --no-optimize
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--name` | `-n` | Name for the squashed migration |
| `--no-optimize` | | Keep every operation instead of collapsing them |

## Server Commands

### runserver

Start the development server.

```bash
python manage.py runserver                 # 127.0.0.1:9000
python manage.py runserver 0.0.0.0:8080    # custom host and port
python manage.py runserver 3000            # custom port only
python manage.py runserver --no-reload
```

**Options:**

| Option | Description |
|--------|-------------|
| `addrport` | Host:port (default: `127.0.0.1:9000`) |
| `--reload` / `--no-reload` | Auto-reload on file changes (on by default) |

Output:
```
Starting development server at http://127.0.0.1:9000/
ASGI app: myproject.asgi:app
Quit with CTRL+C
```

The server refuses to start while migrations are unapplied
(`ENFORCE_MIGRATIONS`, on by default; a warning instead under `DEBUG`).

### check

Verify the project can actually serve a request, and say what to run if it
cannot. This is the command to run after a change.

```bash
python manage.py check
python manage.py check --deploy    # also apply the production bar
python manage.py check --json
```

It reports on:

- the settings module and whether it imports
- the installed apps and their models modules
- **migration state, pending migrations included** — a project several
  migrations behind fails this check
- the configured database (its scheme only; the URL routinely carries a
  password and is never printed)
- with `--deploy`: `DEBUG` off, a real `SECRET_KEY`, and a database other than
  SQLite

Every issue carries the exact command that resolves it:

```
Checking project: myproject

  ✗ 1 migration(s) created but not applied.

1 issue(s) found. Run, in order:
  python manage.py migrate
```

In `--json` mode each entry of `data["issues"]` is
`{"code": ..., "message": ..., "next_command": ...}`.

## User Commands

### createsuperuser

Create an admin user interactively.

```bash
python manage.py createsuperuser
```

Prompts:
```
Username: admin
Email: admin@example.com
Password: 
Password (again): 
Superuser created successfully.
```

## Shell Commands

### shell

Start an interactive Python shell with project context.

```bash
python manage.py shell
```

```pycon
>>> from apps.blog.models import Article
>>> await Article.objects.count()
42
>>> articles = await Article.objects.filter(published=True)[:5]
```

The shell:
- Loads project settings
- Imports common modules
- Configures async support
- Connects to database

## Inspection Commands

These read the live project rather than a checked-in inventory, so they cannot
be out of date. All three accept `--json`.

### showurls

Every route the project serves, with its methods.

```bash
python manage.py showurls
```

Output:
```
GET,POST              /api/v1/posts          posts-list
POST                  /api/v1/posts/query    posts-query
DELETE,GET,PATCH,PUT  /api/v1/posts/{id}     posts-detail
POST                  /api/v1/auth/login     login
GET                   /health                _health
```

Read from the running application's route table, not from the OpenAPI schema:
`/health` and `/ready` are deliberately excluded from the schema but very much
served. The trailing-slash aliases are collapsed, so each endpoint appears once
under its canonical slash-less path.

When the project cannot be imported the command fails with the exception text —
the same import chain `runserver` would die on, so the message is the diagnostic.

### inspect

The project's apps, models, routes, settings and migration state, as JSON.

```bash
python manage.py inspect            # a short human summary
python manage.py inspect --json     # the full payload
```

```json
{"project": {"name": "myproject", "settings_module": "myproject.settings"},
 "settings": {"api_prefix": "/api/v1", "auth_user_model": "accounts.User",
              "database": {"dialect": "sqlite+aiosqlite"}, "secret_key_set": true},
 "apps": [{"name": "blog", "installed": true,
           "models": [{"name": "Post", "table": "blog_post", "fields": [...]}]}],
 "routes": [...], "migrations": {"applied": 3, "pending": 0},
 "degraded": false, "errors": []}
```

Two properties worth relying on:

- **It never hard-fails.** A project that will not import is exactly when
  orientation matters most, so when `create_app()` raises it falls back to
  reading `settings.py` off disk, sets `"degraded": true`, puts the exception in
  `"errors"` and still exits 0 with parseable JSON.
- **It never prints a secret.** `SECRET_KEY`, `JWT_SECRET_KEY` and any
  `*_SECRET` are reported only as "set" or "not set"; the database URL is
  stripped of credentials.

### frontend-brief

An integration brief to hand to a frontend developer or paste into an AI app
builder such as Lovable, v0 or Bolt.

```bash
python manage.py frontend-brief
python manage.py frontend-brief --json     # the brief plus the machine payload
```

It covers the base URL, the auth flow (`Authorization: Bearer`, no cookies),
the error envelope with the code families a client should branch on, the
version header, the CORS requirement, and the live endpoint table. Set
`ZEEB_PREVIEW_URL` in `.env` to have it emit your deployed base URL rather than
`http://127.0.0.1:9000`.

For a frontend on a preview URL that changes per build, set
`CORS_ALLOW_ORIGIN_REGEX` rather than listing every deployment — see
[Settings](../configuration/settings.md).

## JSON Output

`check`, `inspect`, `showurls`, `showmigrations`, `makemigrations`, `migrate`,
`startproject`, `startapp` and `frontend-brief` accept `--json`. They then print
**exactly one** object on stdout:

```json
{"success": false,
 "message": "No migrations directory to apply.",
 "data": {"error_code": "file_not_found",
          "recoverable": true,
          "state_changed": false,
          "next_command": "python manage.py makemigrations",
          "searched_in": "/srv/myproject/migrations"}}
```

The shape is the same one the agent tools return, and `error_code` uses the same
vocabulary, so a caller that knows one knows both. Every failure carries a
`next_command`. In human mode that same line is printed to stderr as
`Next: python manage.py …`.

`error_code` is a **closed set** (`zeeb_orm.cli.output.CLI_ERROR_CODES`),
validated at call time so a typo cannot reach a caller that branches on it:

| Code | Meaning |
|---|---|
| `project_not_found` | Not inside a Zeeb project (no `manage.py` / `[tool.zeeb]` marker found) |
| `file_not_found` | An expected file or directory is missing |
| `app_not_found` | The named app is not in `INSTALLED_APPS` or not on disk |
| `model_not_found` | The named model does not exist in that app |
| `invalid_identifier` | A name is not a valid Python identifier, or is reserved |
| `invalid_input` | An argument was well-formed but unusable |
| `already_exists` | The target is already there (creation would overwrite) |
| `setting_not_found` | A required setting is absent from `settings.py` |
| `dependency_missing` | An optional package the command needs is not installed |
| `permission_denied` | The filesystem refused the read or write |
| `partial_failure` | Some steps applied before one failed — `data` carries the resume metadata |

The flag belongs **after** the subcommand (`manage.py check --json`, not
`manage.py --json check`).

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Database connection string; overrides `DATABASE["url"]` |
| `ZEEB_SETTINGS_MODULE` | Settings module for `create_app()`. `manage.py` commands find settings by path and do not read this |
| `ZEEB_PREVIEW_URL` | Public base URL, used by `frontend-brief` |
| `DEBUG`, `SECRET_KEY`, `CORS_*`, `JWT_*`, … | Every setting is environment-driven — see [Settings](../configuration/settings.md) and `.env.example` |

Real environment variables always win over `.env`.

## Next Steps

- [Settings](../configuration/settings.md) - Configuration
- [Migrations](../orm/migrations.md) - Migration system
