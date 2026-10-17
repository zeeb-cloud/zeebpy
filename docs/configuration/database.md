# Database Configuration

Configure database connections for your Zeeb project.

## Quick Start

```python
# settings.py
DATABASE = {
    "url": "sqlite+aiosqlite:///./db.sqlite3",
}
```

## Supported Databases

| Database | URL Format | Required Package |
|----------|------------|------------------|
| SQLite | `sqlite+aiosqlite:///path` | aiosqlite |
| PostgreSQL | `postgresql+asyncpg://...` | asyncpg |
| MySQL | `mysql+aiomysql://...` | aiomysql |
| MariaDB | `mysql+aiomysql://...` | aiomysql |

### Synchronous connections

Migrations, `showmigrations`, the agent layer's schema inspection and a
`Database` given a URL without an async driver all connect synchronously. They
never leave the driver to SQLAlchemy's default for the scheme — that default
moves between SQLAlchemy versions (2.1 made psycopg v3 the default for a bare
`postgresql://`) — but name the one zeebpy installs:

| Configured URL | Synchronous connection |
|----------------|------------------------|
| `postgresql+asyncpg://`, `postgresql://`, `postgres://` | `postgresql+psycopg2://` (psycopg2, `postgresql` extra) |
| `mysql+aiomysql://`, `mysql+asyncmy://`, `mysql://` | `mysql+pymysql://` (pymysql, `mysql` extra) |
| `sqlite+aiosqlite://` | `sqlite://` (standard library) |

When an async driver is swapped out, the query parameters only it understands
are translated too — libpq refuses a connection string with an option it does
not know:

| asyncpg parameter | Synchronous (psycopg2 / libpq) |
|-------------------|--------------------------------|
| `ssl=require` (`true`, `verify-full`, …) | `sslmode=require` (`require`, `verify-full`, …) |
| `timeout=5` | `connect_timeout=5` |
| `prepared_statement_cache_size`, `statement_cache_size`, `command_timeout`, … | dropped |

aiomysql/asyncmy-only parameters (`echo`, `loop`, `auth_plugin`) are dropped
for pymysql. Every other parameter, and the credentials, pass through byte for
byte.

A URL that already names a driver (`postgresql+psycopg://…`) is used as given.
`zeeb_orm.db.sync_database_url(url)` does the mapping, for your own sync
engines too.

A `Database` given a synchronous URL works — `QuerySet.iterator()` included —
but every query then runs on the event loop thread and blocks it, so
`connect()` emits a `RuntimeWarning`. Use an async driver for a server.
Whether a URL is async is decided from its parsed dialect, not by searching
the URL text.

### Async driver for a bare URL

The reverse matters for a server. Hosting platforms usually hand out a
libpq-style `DATABASE_URL` that names the backend but no driver
(`postgresql://…`, `postgres://…`). Read literally that means psycopg2, a
synchronous driver, and every query would block the event loop. So a
`Database` connects through `zeeb_orm.db.async_database_url(url)`, which gives
a bare PostgreSQL URL the async driver zeebpy ships:

| Configured URL | Async connection |
|----------------|------------------|
| `postgresql://…`, `postgres://…` | `postgresql+asyncpg://…` |
| `postgresql://…?sslmode=require` | `postgresql+asyncpg://…?ssl=require` |

The URL is upgraded only when nothing a driver swap could break is in it. It is
returned unchanged when:

- it already names a driver (`postgresql+psycopg2://`, `postgresql+asyncpg://`)
  — that is the author's choice;
- its scheme is anything other than a bare `postgresql`/`postgres`
  (`mysql://`, `sqlite://`, …);
- its query string carries any parameter besides `sslmode` — libpq's other
  options have no asyncpg equivalent and would refuse the connection;
- asyncpg is not installed.

A `Database` configured with `connect_args` skips the upgrade altogether: those
arguments were written for the driver the URL names today. Credentials and
host pass through untouched.

```python
from zeeb_orm.db import async_database_url

async_database_url("postgres://app:secret@db.internal/shop?sslmode=require")
# 'postgresql+asyncpg://app:secret@db.internal/shop?ssl=require'

async_database_url("postgresql+psycopg2://app:secret@db.internal/shop")
# unchanged: the URL names its driver
```

## SQLite

Best for development and small applications.

```python
# Relative path
DATABASE = {
    "url": "sqlite+aiosqlite:///./db.sqlite3",
}

# Absolute path
DATABASE = {
    "url": "sqlite+aiosqlite:////var/data/myapp.db",
}

# In-memory (testing)
DATABASE = {
    "url": "sqlite+aiosqlite:///:memory:",
}
```

Install driver:
```bash
pip install aiosqlite
```

SQLite leaves foreign-key enforcement off unless each connection asks for it;
zeeb_orm does (`PRAGMA foreign_keys=ON` on every connection it opens), so
SQLite rejects a row pointing at nothing and applies `ON DELETE` actions just
like PostgreSQL and MySQL. Migrations run with enforcement off, because SQLite
alters a column by rebuilding its table and the rebuild must not cascade.

## PostgreSQL

Recommended for production.

```python
DATABASE = {
    "url": "postgresql+asyncpg://user:password@localhost:5432/mydb",
    "pool_size": 20,
    "max_overflow": 10,
}
```

Install driver:
```bash
pip install asyncpg
```

### Connection URL Format

```
postgresql+asyncpg://[user]:[password]@[host]:[port]/[database]
```

Examples:
```python
# Local
"postgresql+asyncpg://postgres:secret@localhost:5432/mydb"

# Remote
"postgresql+asyncpg://user:pass@db.example.com:5432/production"

# With SSL
"postgresql+asyncpg://user:pass@db.example.com/mydb?ssl=require"
```

## MySQL / MariaDB

```python
DATABASE = {
    "url": "mysql+aiomysql://user:password@localhost:3306/mydb",
    "pool_size": 10,
}
```

Install driver:
```bash
pip install aiomysql
```

### Connection URL Format

```
mysql+aiomysql://[user]:[password]@[host]:[port]/[database]
```

## Connection Pool Settings

```python
DATABASE = {
    "url": "postgresql+asyncpg://localhost/mydb",
    
    # Pool configuration
    "pool_size": 5,          # Base pool size
    "max_overflow": 10,       # Extra connections beyond pool
    "pool_timeout": 30,       # Seconds to wait for connection
    "pool_recycle": 1800,     # Recycle connections after (seconds)
    "pool_pre_ping": True,    # Test connections before use
}
```

### Pool Options

| Option | Default | Description |
|--------|---------|-------------|
| `pool_size` | 5 | Number of connections to keep in pool |
| `max_overflow` | 10 | Extra connections allowed beyond pool_size |
| `pool_timeout` | 30 | Seconds to wait for available connection |
| `pool_recycle` | 1800 | Recycle connections older than (seconds) |
| `pool_pre_ping` | True | Verify connections before use |

### Sizing Guidelines

| Application | pool_size | max_overflow |
|-------------|-----------|--------------|
| Development | 5 | 5 |
| Small (< 100 req/s) | 10 | 10 |
| Medium (< 1000 req/s) | 20 | 20 |
| Large (> 1000 req/s) | 50+ | 50+ |

## Debug Mode

Enable SQL query logging:

```python
DATABASE = {
    "url": "sqlite+aiosqlite:///./db.sqlite3",
    "echo": True,  # Log all SQL queries
}
```

## Environment Variables

Use environment variables for security:

```python
from zeeb_api.conf.env import env_str

DATABASE = {
    "url": env_str("DATABASE_URL", "sqlite+aiosqlite:///./db.sqlite3"),
}
```

`.env` file:
```
DATABASE_URL=postgresql+asyncpg://user:password@localhost/mydb
```

A scaffolded project already calls `load_env()` at the top of its `settings.py`,
so no third-party dotenv package is needed — `zeeb_api.conf.env` reads `.env`
using only the standard library. See
[Settings](settings.md) for the full set of `env_*` helpers.

`zeeb_orm` also reads the `DATABASE_URL` and `DATABASE_ECHO` environment
variables directly, so the default connection works even before any
`settings.py` is loaded (see [`DatabaseConfig.from_env()`](#databaseconfig)).

## Programmatic Configuration

### DatabaseConfig

`zeeb_orm.conf.DatabaseConfig` is the dataclass that holds one connection's
configuration. The keys of the `DATABASE` setting are its fields, the ORM's
settings carry one as `get_settings().database`, and `Database(config=...)`
accepts one directly.

| Field | Type | Default |
|-------|------|---------|
| `url` | `str` | required |
| `echo` | `bool` | `False` |
| `pool_size` | `int` | `5` |
| `max_overflow` | `int` | `10` |
| `pool_timeout` | `int` | `30` |
| `pool_recycle` | `int` | `1800` |
| `pool_pre_ping` | `bool` | `True` |
| `connect_args` | `dict` | `{}` |

```python
from zeeb_orm import Database, configure
from zeeb_orm.conf import DatabaseConfig

config = DatabaseConfig(
    url="postgresql+asyncpg://app:secret@localhost/shop",
    pool_size=20,
    connect_args={"ssl": "require"},
)

configure(database=config)        # the ORM's default connection
replica = Database(config=config)  # or one connection of your own
```

`zeeb_orm.configure(database=...)` also takes a plain dict, which is turned
into a `DatabaseConfig` — a key that is not one of the fields above raises
`TypeError`. Leaving `database` out of `configure()` reads the environment.

`DatabaseConfig.from_env(prefix="DATABASE")` builds a config from environment
variables; it is what the ORM uses when nothing else configured it:

| Variable | Field | Default when unset |
|----------|-------|--------------------|
| `DATABASE_URL` | `url` | `sqlite+aiosqlite:///./db.sqlite3` |
| `DATABASE_ECHO` | `echo` | `False` (only `true`, any case, enables it) |
| `DATABASE_POOL_SIZE` | `pool_size` | `5` |
| `DATABASE_MAX_OVERFLOW` | `max_overflow` | `10` |
| `DATABASE_POOL_TIMEOUT` | `pool_timeout` | `30` |
| `DATABASE_POOL_RECYCLE` | `pool_recycle` | `1800` |
| `DATABASE_POOL_PRE_PING` | `pool_pre_ping` | `True` (only `false`, any case, disables it) |

`connect_args` is not read from the environment. Another `prefix` reads another
set of variables — `DatabaseConfig.from_env("REPORTING")` reads
`REPORTING_URL`, `REPORTING_POOL_SIZE`, and so on. A non-integer pool value
raises `ValueError`.

### database_kwargs

`zeeb_api.conf.orm.database_kwargs(database_config)` takes a `DATABASE`
settings dict and returns only the connection options — `echo`, `pool_size`,
`max_overflow`, `pool_timeout`, `pool_recycle`, `pool_pre_ping` and
`connect_args`, each only if present. `url` and any other key are left out, so
the result can be splatted into `Database(...)` or `setup_database(...)`
alongside the URL. `create_app()`'s default lifespan opens the default
connection exactly this way:

```python
from zeeb_api.conf.orm import database_kwargs
from zeeb_orm import setup_database

DATABASE = {
    "url": "postgresql+asyncpg://localhost/shop",
    "pool_size": 20,
    "pool_pre_ping": True,
}

database_kwargs(DATABASE)
# {'pool_size': 20, 'pool_pre_ping': True}

db = await setup_database(DATABASE["url"], **database_kwargs(DATABASE))
```

## Project Discovery

Tooling that works on a project on disk — the `manage.py` commands, the
programmatic migration API, the generated test harness, the auth backend's
user-model lookup — finds the project, its settings and its database through
one module, `zeeb_orm.conf.project`. Use it rather than writing your own
search; [Settings](settings.md) describes the rules from the command line's
point of view.

```python
from zeeb_orm.conf.project import (
    SettingsImportError,
    find_project_root,
    find_settings_module,
    find_settings_path,
    load_settings_module,
    resolve_database_url,
)

root = find_project_root()               # e.g. Path("/srv/shop")
find_settings_module(root)               # "shop.settings"
find_settings_path(root)                 # Path("/srv/shop/shop/settings.py")

try:
    settings = load_settings_module(root)
    url = resolve_database_url(root)
except SettingsImportError as exc:
    print(f"{exc.module} is broken: {exc.cause!r}")
```

### find_project_root

```text
find_project_root(start=None, *, allow_migrations_dir=False) -> Path | None
```

The nearest directory at or above `start` (default: the current working
directory) that contains a `manage.py` file; `None` when there is none. With
`allow_migrations_dir=True` — what the programmatic migration API uses, so it
also serves projects without a `manage.py` — the nearest directory with a
`migrations/` subdirectory is accepted, but only when no `manage.py` exists
anywhere above: a `manage.py` further up always wins over a nested
`migrations/`.

### find_settings_module

```text
find_settings_module(project_root) -> str | None
```

The dotted name of the project's settings module, or `None`:

1. `settings_module` under `[tool.zeeb]` in the project's `pyproject.toml`
   (written by `startproject`), when the file it names exists;
2. otherwise the first top-level directory, in sorted order, that contains a
   `settings.py` — skipping `apps`, `migrations`, `tests`, `logs`,
   `node_modules`, `venv` and any directory whose name starts with `.` or `_`.

### find_settings_path

```text
find_settings_path(project_root) -> Path | None
```

The file `find_settings_module()` names (`shop.settings` →
`<root>/shop/settings.py`), or `None`.

### load_settings_module

```text
load_settings_module(project_root) -> ModuleType | None
```

Imports the settings module and returns it, or `None` when the project has no
settings module. The module is executed fresh on every call and is not stored
in `sys.modules`, so two projects whose packages share a name never share
settings. The project root is put on `sys.path` for the duration of the import
(when it is not there already, and removed afterwards) so the settings can
import project-local packages.

Raises `SettingsImportError` when the settings file exists but raises on
import — a broken `settings.py` is never papered over with defaults.

### resolve_database_url

```text
resolve_database_url(project_root) -> str
```

The database URL tooling should use: `DATABASE["url"]` from the project's
settings when they declare one, otherwise the ORM's own configuration
(`zeeb_orm.get_settings().database.url` — `DATABASE_URL` from the environment,
or the SQLite default). Pass `None` to skip the project and get the ORM's
answer directly. Raises `SettingsImportError` when the settings module exists
but does not import.

### SettingsImportError

A subclass of `RuntimeError`, raised by `load_settings_module()` and
`resolve_database_url()` (and therefore by everything built on them, such as
`get_migration_state()`) when `settings.py` exists but could not be imported.
Its message names the module, the file and the original error, which is also
chained as `__cause__`.

| Attribute | Value |
|-----------|-------|
| `path` | The settings file (`Path`) |
| `module` | Its dotted name, e.g. `"shop.settings"` |
| `cause` | The exception the import raised |

The CLI turns it into a failure that points at `python manage.py check`
rather than falling back to a default database.

## Database Operations

### Initialize Database

```bash
python manage.py migrate
```

### Create Migrations

After model changes:
```bash
python manage.py makemigrations
```

### Check Migration Status

```bash
python manage.py showmigrations
```

### Access Database Programmatically

```python
from zeeb_orm import Database, close_all_connections, get_connection

# The default connection is created lazily from settings
db = await get_connection()

# Or build one explicitly
db = Database("postgresql+asyncpg://localhost/mydb")
await db.connect()

# Create tables from the model registry
await db.create_all()

# Get an async session
async with db.session() as session:
    result = await session.execute(...)

# Release every registered connection (e.g. on shutdown)
await close_all_connections()
```

`get_connection()`, `close_all_connections()` and `Database.connect()` /
`.disconnect()` / `.create_all()` / `.drop_all()` / `.execute()` are all
coroutines — `Database.session()` is the one exception, an async context
manager.

## Multiple Databases

There is a single `DATABASE` setting, which configures the `default`
connection. Additional connections — read replicas, a reporting database — are
registered at runtime rather than declared in settings:

```python
from zeeb_orm import Database, register_database

replica = Database(
    "postgresql+asyncpg://localhost/replica",
    pool_size=10,
)
await replica.connect()
register_database(replica, alias="replica")
```

Once registered, route a queryset at it with `using()`:

```python
articles = await Article.objects.using("replica").all()
```

`zeeb_orm.db.get_database(alias="default")` returns the `Database` registered
under an alias, or `None`. It is a plain lookup: it never connects and never
creates a connection. The `default` connection that `get_connection()` builds
lazily from settings is registered only once something has used it, so
`get_database()` can still be `None` early in a process — call
`await get_connection(alias)` when you need the connection either way.

```python
from zeeb_orm.db import get_database

replica = get_database("replica")
if replica is None:
    ...  # not registered (yet)
```

Registering an alias again replaces its connection and disposes the old
engine; `setup_database()` does the same for `default`. Calling
`zeeb_orm.configure()` with a different `database` drops a default that was
created lazily from the old settings, so the next query uses the new one.

Relations stay on the instance's database: `await post.author`, reverse
managers (`author.posts`) and many-to-many managers all query the alias the
instance was loaded from. An `atomic()` block is per database — a write to
another alias inside it runs in its own transaction.

An alias that was never registered raises `ConnectionDoesNotExist` rather than
silently falling back to the default database:

```python
from zeeb_orm import ConnectionDoesNotExist

try:
    await Article.objects.using("reporting").all()
except ConnectionDoesNotExist:
    ...  # register_database(Database(...), alias="reporting") first
```

## SSL/TLS Connections

### PostgreSQL with SSL

```python
DATABASE = {
    "url": "postgresql+asyncpg://user:pass@host/db?ssl=require",
}

# Or with SSL mode
DATABASE = {
    "url": "postgresql+asyncpg://user:pass@host/db",
    "connect_args": {
        "ssl": "require",  # or "verify-full"
    },
}
```

### MySQL with SSL

```python
DATABASE = {
    "url": "mysql+aiomysql://user:pass@host/db",
    "connect_args": {
        "ssl": {
            "ca": "/path/to/ca-cert.pem",
        },
    },
}
```

## Cloud Databases

### AWS RDS PostgreSQL

```python
DATABASE = {
    "url": "postgresql+asyncpg://user:password@mydb.xxxxx.us-east-1.rds.amazonaws.com:5432/mydb",
    "connect_args": {
        "ssl": "require",
    },
    "pool_size": 20,
}
```

### Google Cloud SQL

```python
DATABASE = {
    "url": "postgresql+asyncpg://user:password@/mydb?host=/cloudsql/project:region:instance",
}
```

### Heroku Postgres

```python
import os

# Heroku provides DATABASE_URL automatically
DATABASE = {
    "url": os.environ["DATABASE_URL"].replace("postgres://", "postgresql+asyncpg://"),
}
```

### DigitalOcean

```python
DATABASE = {
    "url": "postgresql+asyncpg://doadmin:password@db-xxxx.ondigitalocean.com:25060/defaultdb",
    "connect_args": {
        "ssl": "require",
    },
}
```

## Testing Configuration

### SQLite In-Memory

```python
# test_settings.py
DATABASE = {
    "url": "sqlite+aiosqlite:///:memory:",
}
```

### pytest Fixture

```python
# conftest.py
import pytest
from zeeb_orm import Database

@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.connect()
    await database.create_all()
    yield database
    await database.disconnect()

@pytest.fixture
async def session(db):
    async with db.session() as session:
        yield session
```

## Docker Configuration

### docker-compose.yml

```yaml
version: "3.8"

services:
  app:
    build: .
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:secret@db:5432/mydb
    depends_on:
      - db
    
  db:
    image: postgres:15
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: secret
      POSTGRES_DB: mydb
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"

volumes:
  pgdata:
```

### Wait for Database

```python
# manage.py or startup script
import asyncio
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

async def wait_for_db(max_retries=30, delay=1):
    """Wait for database to be ready."""
    from zeeb_orm import get_connection

    db = await get_connection()

    for i in range(max_retries):
        try:
            async with db.session() as session:
                await session.execute(text("SELECT 1"))
            return True
        except OperationalError:
            print(f"Waiting for database... ({i+1}/{max_retries})")
            await asyncio.sleep(delay)
    
    raise Exception("Database not available")
```

## Troubleshooting

### Connection Refused

```
sqlalchemy.exc.OperationalError: connection refused
```

- Check database is running
- Verify host/port in URL
- Check firewall rules
- For Docker, use service name as host

### Authentication Failed

```
asyncpg.InvalidPasswordError: password authentication failed
```

- Verify username/password
- Check database user permissions
- For PostgreSQL, check pg_hba.conf

### Pool Exhausted

```
sqlalchemy.exc.TimeoutError: QueuePool limit reached
```

- Increase `pool_size` and `max_overflow`
- Check for connection leaks (use context managers)
- Reduce connection hold time

### SSL Required

```
asyncpg.InvalidParameterValueError: SSL connection is required
```

- Add `?ssl=require` to URL
- Or set `connect_args: {"ssl": "require"}`

### Connection Is Closed

```
asyncpg.exceptions.InterfaceError: connection is closed
```

This happens when a pooled connection is closed by the server (idle timeout, restart, network issue) before SQLAlchemy detects it. `pool_pre_ping` (enabled by default) prevents this by testing each connection before use. If you encounter this error, ensure `pool_pre_ping` is not set to `False`, and consider lowering `pool_recycle` to match your database's idle connection timeout.

## Next Steps

- [Migrations](../orm/migrations.md) - Database migrations
- [Models](../orm/models.md) - Define models
- [Settings](settings.md) - All settings
