# Installation

## Requirements

- Python 3.11 or higher
- pip or poetry

## Install from PyPI

```bash
# Install both packages
pip install zeeb-orm zeeb-api

# Or install individually
pip install zeeb-orm  # ORM only
pip install zeeb-api  # API framework (includes zeeb-orm)
```

## Install from Source

```bash
git clone https://github.com/zeeb-cloud/zeeb-orm.git
cd zeeb-orm
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
│   ├── settings.py      # Configuration
│   ├── asgi.py          # ASGI application
│   └── urls.py          # URL routing
├── apps/                # Your applications
├── migrations/          # Database migrations
│   └── versions/
├── logs/                # Application logs
├── manage.py            # Management CLI
└── requirements.txt
```

### Configure Database

Edit `myproject/settings.py`:

```python
# SQLite (default, good for development)
DATABASE = {
    "url": "sqlite+aiosqlite:///./db.sqlite3",
}

# PostgreSQL (recommended for production)
DATABASE = {
    "url": "postgresql+asyncpg://user:password@localhost:5432/mydb",
}

# MySQL
DATABASE = {
    "url": "mysql+aiomysql://user:password@localhost:3306/mydb",
}
```

### Initialize Database

```bash
# Create migrations directory
python manage.py init

# Generate initial migrations
python manage.py makemigrations

# Apply migrations
python manage.py migrate
```

### Run Development Server

```bash
python manage.py runserver
```

Server starts at `http://127.0.0.1:8000`

## Development Setup

For development, install with dev dependencies:

```bash
pip install zeeb-orm[dev] zeeb-api[dev]
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
