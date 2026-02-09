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
    "pool_recycle": 3600,     # Recycle connections after (seconds)
    "pool_pre_ping": True,    # Test connections before use
}
```

### Pool Options

| Option | Default | Description |
|--------|---------|-------------|
| `pool_size` | 5 | Number of connections to keep in pool |
| `max_overflow` | 10 | Extra connections allowed beyond pool_size |
| `pool_timeout` | 30 | Seconds to wait for available connection |
| `pool_recycle` | 3600 | Recycle connections older than (seconds) |
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
import os

DATABASE = {
    "url": os.environ.get(
        "DATABASE_URL",
        "sqlite+aiosqlite:///./db.sqlite3"
    ),
}
```

`.env` file:
```
DATABASE_URL=postgresql+asyncpg://user:password@localhost/mydb
```

Load with python-dotenv:
```python
from dotenv import load_dotenv
load_dotenv()
```

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
from zeeb_orm.core import Database

# Initialize with settings
db = Database.from_settings()

# Or with explicit URL
db = Database("postgresql+asyncpg://localhost/mydb")

# Create tables
await db.init_db()

# Get async session
async with db.session() as session:
    result = await session.execute(...)
```

## Multiple Databases

For read replicas or multi-database setups:

```python
DATABASES = {
    "default": {
        "url": "postgresql+asyncpg://localhost/primary",
        "pool_size": 20,
    },
    "replica": {
        "url": "postgresql+asyncpg://localhost/replica",
        "pool_size": 10,
    },
}
```

Use in code:
```python
from zeeb_orm.core import get_database

# Default database
db = get_database()

# Specific database
replica_db = get_database("replica")
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
from zeeb_orm.core import Database

@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.init_db()
    yield database
    await database.close()

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
from sqlalchemy.exc import OperationalError

async def wait_for_db(max_retries=30, delay=1):
    """Wait for database to be ready."""
    from zeeb_orm.core import Database
    
    db = Database.from_settings()
    
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

## Next Steps

- [Migrations](../orm/migrations.md) - Database migrations
- [Models](../orm/models.md) - Define models
- [Settings](settings.md) - All settings
