# Installation

## Requirements

- Python 3.11 or higher
- pip or poetry

## Install from GitHub

```bash
# Install the latest version directly from GitHub
pip install git+https://github.com/zeeb-cloud/zeebpy.git

# With database drivers (PEP 508 extras syntax)
pip install "zeebpy[postgresql] @ git+https://github.com/zeeb-cloud/zeebpy.git"  # asyncpg + psycopg2
pip install "zeebpy[mysql] @ git+https://github.com/zeeb-cloud/zeebpy.git"       # aiomysql + pymysql
pip install "zeebpy[sqlite] @ git+https://github.com/zeeb-cloud/zeebpy.git"      # aiosqlite
pip install "zeebpy[all] @ git+https://github.com/zeeb-cloud/zeebpy.git"         # All drivers
```

## Install from Source (for contributors)

```bash
git clone https://github.com/zeeb-cloud/zeebpy.git
cd zeebpy
pip install -e .
```

## Dependencies

### zeeb_orm
- `sqlalchemy[asyncio]>=2.0` - Async ORM
- `alembic>=1.13` - Database migrations
- `aiosqlite` - SQLite async driver (included)

### zeeb_api
- `fastapi>=0.100` - Web framework
- `uvicorn[standard]` - ASGI server
- `pydantic>=2.0` - Data validation
- `python-jose[cryptography]` - JWT tokens
- `passlib[bcrypt]` - Password hashing

## Database Drivers

Install the appropriate async driver for your database:

```bash
# PostgreSQL
pip install asyncpg

# MySQL
pip install aiomysql

# SQLite (included by default)
pip install aiosqlite
```

## Verify Installation

```bash
# Check CLI is available
zeeb --version

# Or in a project
python manage.py --help
```

## Project Setup

### Create a New Project

```bash
zeeb startproject myproject
cd myproject
```

This creates:
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
├── tests/               # Shared fixtures + a smoke suite that already passes
├── migrations/          # Migration files live flat in here
├── logs/
├── .cursor/rules/       # The AGENTS.md body, for Cursor
├── .env                 # Generated signing key — gitignored, mode 0600
├── .env.example         # Every supported variable
├── .gitignore
├── AGENTS.md            # Conventions, for humans and coding agents
├── CLAUDE.md            # Pointer to AGENTS.md
├── manage.py
├── pyproject.toml
├── pytest.ini
├── README.md
└── requirements.txt
```

Authentication, CORS, rate limiting, API versioning, health probes and logging
are configured and on. The `.env` holds a signing key generated for this
project, which is what lets it run with `DEBUG=false` without further setup.

`pytest` passes right away — before any migration exists — so you have a working
verification loop from the start.

### Configure Database

Set `DATABASE_URL` in `.env` — no code change needed:

```bash
# SQLite (default, good for development)
DATABASE_URL=sqlite+aiosqlite:///db.sqlite3

# PostgreSQL (recommended for production)
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/mydb

# MySQL
DATABASE_URL=mysql+aiomysql://user:password@localhost:3306/mydb
```

### Initialize Database

```bash
# Generate initial migrations
python manage.py makemigrations

# Apply migrations
python manage.py migrate

# Create an admin account
python manage.py createsuperuser
```

### Run Development Server

```bash
python manage.py runserver
```

Server starts at `http://127.0.0.1:8000`

## Development Setup

For development, install with dev dependencies:

```bash
pip install "zeebpy[dev] @ git+https://github.com/zeeb-cloud/zeebpy.git"
```

This includes:
- `pytest` and `pytest-asyncio` for testing
- `black` and `ruff` for formatting/linting
- `mypy` for type checking

## Docker Setup

Example `Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Run migrations and start server
CMD ["sh", "-c", "python manage.py migrate && python manage.py runserver 0.0.0.0:8000"]
```

Example `docker-compose.yml`:

```yaml
version: '3.8'

services:
  web:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/mydb
    depends_on:
      - db

  db:
    image: postgres:15
    environment:
      - POSTGRES_DB=mydb
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=postgres
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

## Next Steps

- [Quick Start Tutorial](quickstart.md) - Build your first API
- [Models](orm/models.md) - Learn about data models
- [Configuration](configuration/settings.md) - Configure your project
