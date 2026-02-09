"""startproject command - Create new Zeeb project."""

import os
from pathlib import Path
from typing import Any


# Project template files

MANAGE_PY = '''#!/usr/bin/env python3
"""Zeeb project management script."""

import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

if __name__ == "__main__":
    from zeeb_orm.cli.main import main
    sys.exit(main())
'''

SETTINGS_PY = '''"""
{project_name} settings.

Configure your database, installed apps, and other settings here.
"""

import os

# Debug mode - set to False in production
DEBUG = os.getenv("DEBUG", "true").lower() == "true"

# Secret key for JWT tokens - CHANGE IN PRODUCTION!
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")

# Database configuration
DATABASE = {{
    "url": os.getenv(
        "DATABASE_URL",
        "sqlite+aiosqlite:///db.sqlite3"
    ),
    # PostgreSQL example:
    # "url": "postgresql+asyncpg://user:password@localhost:5432/dbname",
    # MySQL example:
    # "url": "mysql+aiomysql://user:password@localhost:3306/dbname",
}}

# Installed apps - list your app modules here
INSTALLED_APPS = [
    # "apps.myapp",
]

# Custom user model (like Django's AUTH_USER_MODEL)
# Set to your custom user model path, e.g., "apps.accounts.CustomUser"
# If not set, uses the default zeeb_api.auth.models.User
AUTH_USER_MODEL = None  # Example: "apps.accounts.CustomUser"

# Logging configuration
# Logs are automatically written to the logs/ directory with daily rotation
LOGGING = {{
    "level": os.getenv("LOG_LEVEL", "DEBUG" if DEBUG else "INFO"),
    "json_logs": os.getenv("JSON_LOGS", "false" if DEBUG else "true").lower() == "true",
    "log_file": os.getenv("LOG_FILE", "logs/app.log"),
    "log_rotation": True,           # Rotate logs at midnight
    "log_retention_days": 30,       # Keep logs for 30 days
}}

# Middleware (for FastAPI)
MIDDLEWARE = [
    # "zeeb_api.middleware.CORSMiddleware",
]

# CORS settings
CORS_ALLOW_ORIGINS = [
    "http://localhost:3000",
]

# API settings
API_TITLE = "{project_name} API"
API_VERSION = "1.0.0"
API_PREFIX = "/api/v1"

# Pagination (limit/offset)
DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# Migration enforcement
# When True, server will fail to start if there are unapplied migrations
# Automatically disabled in DEBUG mode for development convenience
ENFORCE_MIGRATIONS = os.getenv("ENFORCE_MIGRATIONS", "true").lower() == "true"

# JWT settings
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 15
JWT_REFRESH_TOKEN_EXPIRE_DAYS = 7
'''

URLS_PY = '''"""
{project_name} URL configuration.

Register your app routers here.
"""

from zeeb_api.routers import DefaultRouter

# Main router
router = DefaultRouter()

# Include app routers
# from apps.myapp.urls import router as myapp_router
# router.include(myapp_router, prefix="myapp")


def get_routes():
    """Return all routes for the FastAPI app."""
    return router.routes
'''

ASGI_PY = '''"""
{project_name} ASGI application.

FastAPI app factory for the project.
"""

from fastapi import FastAPI
from contextlib import asynccontextmanager

from zeeb_orm import setup_database, close_all_connections, check_migrations_applied, MigrationError
from zeeb_api.logging import configure_logging, get_logger

# Import settings
from {project_name}.settings import (
    DATABASE, API_TITLE, API_VERSION, API_PREFIX, DEBUG,
    CORS_ALLOW_ORIGINS, MIDDLEWARE, ENFORCE_MIGRATIONS, LOGGING,
)
from {project_name}.urls import get_routes

# Configure logging from settings (with rotation at midnight)
configure_logging(
    level=LOGGING.get("level", "INFO"),
    json_logs=LOGGING.get("json_logs", False),
    log_file=LOGGING.get("log_file"),
    log_rotation=LOGGING.get("log_rotation", True),
    log_retention_days=LOGGING.get("log_retention_days", 30),
)

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - setup and teardown."""
    logger.info("Starting application", api_title=API_TITLE, debug=DEBUG)
    
    # Check migrations before startup (like Django)
    if ENFORCE_MIGRATIONS and not DEBUG:
        try:
            check_migrations_applied(raise_on_pending=True)
        except MigrationError as e:
            separator = "=" * 60
            logger.error("Unapplied migrations detected", error=str(e))
            print(f"\\n{{separator}}")
            print("ERROR: Unapplied migrations detected!")
            print(str(e))
            print("\\nRun: python manage.py migrate")
            print(f"{{separator}}\\n")
            raise SystemExit(1)
    
    # Startup
    await setup_database(DATABASE["url"])
    logger.info("Database connected")
    
    yield
    
    # Shutdown
    logger.info("Shutting down application")
    await close_all_connections()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        debug=DEBUG,
        lifespan=lifespan,
    )

    # Add CORS middleware if configured
    if CORS_ALLOW_ORIGINS:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=CORS_ALLOW_ORIGINS,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Include routes with API prefix
    for route in get_routes():
        app.include_router(route, prefix=API_PREFIX)

    logger.info("Application configured", routes=len(get_routes()))
    return app


# Create app instance
app = create_app()
'''

PROJECT_INIT_PY = '''"""
{project_name} - A Zeeb project.
"""

__version__ = "0.1.0"
'''

APPS_INIT_PY = '''"""
Apps package - your application modules go here.

Create a new app with: zeeb startapp <name>
"""
'''

REQUIREMENTS_TXT = '''# Zeeb ORM and API
zeebpy[all]>=0.1.0
zeeb-api>=0.1.0

# FastAPI
fastapi>=0.100.0
uvicorn[standard]>=0.23.0

# Development
pytest>=7.0.0
pytest-asyncio>=0.21.0
httpx>=0.24.0
'''

GITIGNORE = '''# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# Virtual environments
.venv/
venv/
ENV/

# IDE
.idea/
.vscode/
*.swp
*.swo

# Testing
.pytest_cache/
.coverage
htmlcov/

# Database
*.sqlite3
*.db

# Environment
.env
.env.local

# Logs
logs/*.log

# Migrations
migrations/versions/__pycache__/
'''

README_MD = '''# {project_name}

A Zeeb project.

## Setup

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\\Scripts\\activate on Windows

# Install dependencies
pip install -r requirements.txt
```

## Running

```bash
# Run development server
python manage.py runserver

# Or use zeeb-manage from anywhere in the project
zeeb-manage runserver

# Or with uvicorn directly
uvicorn {project_name}.asgi:app --reload
```

## Management Commands

All commands work with `python manage.py`, `zeeb-manage`, or `zeeb`:

```bash
# Create a new app
zeeb-manage startapp myapp

# Initialize migrations (first time only)
zeeb-manage init

# Create migrations after model changes
zeeb-manage makemigrations

# Apply migrations
zeeb-manage migrate

# Check project configuration
zeeb-manage check

# Create a superuser
zeeb-manage createsuperuser

# Show migration status
zeeb-manage showmigrations

# Rollback migrations
zeeb-manage migrate --rollback 1

# Interactive shell
zeeb-manage shell
```

## Project Structure

```
{project_name}/
├── manage.py
├── {project_name}/
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   └── asgi.py
├── apps/
│   └── (your apps here)
├── migrations/
└── requirements.txt
```
'''


def run_startproject(name: str, directory: str) -> int:
    """Create a new Zeeb project."""
    # Validate project name
    if not name.isidentifier():
        print(f"Error: '{name}' is not a valid Python identifier")
        return 1

    base_path = Path(directory).resolve()
    project_path = base_path / name

    if project_path.exists():
        print(f"Error: Directory '{project_path}' already exists")
        return 1

    print(f"Creating project '{name}' in {base_path}...")

    try:
        # Create directory structure
        project_path.mkdir(parents=True)
        (project_path / name).mkdir()
        (project_path / "apps").mkdir()
        (project_path / "migrations").mkdir()
        (project_path / "migrations" / "versions").mkdir()
        (project_path / "logs").mkdir()
        
        # Create .gitkeep in logs to preserve empty dir
        (project_path / "logs" / ".gitkeep").write_text("")

        # Create files
        files = {
            "manage.py": MANAGE_PY,
            f"{name}/__init__.py": PROJECT_INIT_PY.format(project_name=name),
            f"{name}/settings.py": SETTINGS_PY.format(project_name=name),
            f"{name}/urls.py": URLS_PY.format(project_name=name),
            f"{name}/asgi.py": ASGI_PY.format(project_name=name),
            "apps/__init__.py": APPS_INIT_PY,
            "requirements.txt": REQUIREMENTS_TXT,
            ".gitignore": GITIGNORE,
            "README.md": README_MD.format(project_name=name),
        }

        for filepath, content in files.items():
            file_path = project_path / filepath
            file_path.write_text(content)
            print(f"  Created {filepath}")

        # Make manage.py executable
        manage_path = project_path / "manage.py"
        manage_path.chmod(manage_path.stat().st_mode | 0o111)

        print(f"\nProject '{name}' created successfully!")
        print(f"\nNext steps:")
        print(f"  cd {name}")
        print(f"  python -m venv .venv")
        print(f"  source .venv/bin/activate")
        print(f"  pip install -r requirements.txt")
        print(f"  python manage.py startapp myapp")
        print(f"  python manage.py runserver")

        return 0

    except Exception as e:
        print(f"Error creating project: {e}")
        # Cleanup on error
        import shutil
        if project_path.exists():
            shutil.rmtree(project_path)
        return 1
