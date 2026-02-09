# CLI Commands

Zeeb provides Django-style management commands via `manage.py` or the `zeeb` CLI.

## Project Commands

### startproject

Create a new Zeeb project.

```bash
zeeb startproject myproject
```

Creates:
```
myproject/
├── myproject/
│   ├── __init__.py
│   ├── settings.py
│   ├── asgi.py
│   └── urls.py
├── apps/
├── migrations/
│   └── versions/
├── logs/
├── manage.py
└── requirements.txt
```

### startapp

Create a new application within a project.

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
```

## Migration Commands

### init

Initialize migrations directory for a new project.

```bash
python manage.py init
```

Creates:
- `migrations/` directory
- `migrations/versions/` for migration files
- `migrations/env.py` Alembic environment
- `alembic.ini` configuration

### makemigrations

Generate migrations from model changes.

```bash
# Auto-detect changes in all apps
python manage.py makemigrations

# With description
python manage.py makemigrations -m "add user profile"

# Create empty migration (for manual edits)
python manage.py makemigrations --empty

# Show SQL without creating migration
python manage.py makemigrations --sql
```

**Options:**
| Option | Short | Description |
|--------|-------|-------------|
| `--message` | `-m` | Migration description |
| `--empty` | | Create empty migration |
| `--sql` | | Preview SQL |
| `--autogenerate` | | Auto-detect changes (default) |

### migrate

Apply migrations to the database.

```bash
# Apply all pending migrations
python manage.py migrate

# Apply up to specific revision
python manage.py migrate 003

# Show SQL without applying
python manage.py migrate --sql
```

**Options:**
| Option | Description |
|--------|-------------|
| `revision` | Target revision (optional) |
| `--sql` | Show SQL only |
| `--database` | Database alias |

### showmigrations

Display migration status.

```bash
python manage.py showmigrations
```

Output:
```
Migration History:
  [X] 001_initial (applied: 2024-01-15 10:30:00)
  [X] 002_add_profiles (applied: 2024-01-16 14:00:00)
  [ ] 003_add_comments (pending)

Current: 002_add_profiles
```

### rollback

Revert migrations.

```bash
# Rollback last migration
python manage.py rollback

# Rollback to specific revision
python manage.py rollback 001

# Rollback all migrations
python manage.py rollback base
```

## Server Commands

### runserver

Start the development server.

```bash
# Default: 127.0.0.1:8000
python manage.py runserver

# Custom host and port
python manage.py runserver 0.0.0.0:8080

# Custom port only
python manage.py runserver 3000

# With auto-reload disabled
python manage.py runserver --no-reload
```

**Options:**
| Option | Description |
|--------|-------------|
| `address` | Host:port (default: 127.0.0.1:8000) |
| `--no-reload` | Disable auto-reload |
| `--workers` | Number of workers |

Output:
```
Starting development server at http://127.0.0.1:8000/
ASGI app: myproject.asgi:app
Quit with CTRL+C

INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Started reloader process [12345]
```

### check

Validate project configuration.

```bash
# Basic check
python manage.py check

# Check deployment settings
python manage.py check --deploy
```

Checks:
- Settings validity
- Database connection
- Installed apps
- Migration status
- Security settings (with `--deploy`)

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

### changepassword

Change a user's password.

```bash
python manage.py changepassword username
```

## Shell Commands

### shell

Start an interactive Python shell with project context.

```bash
python manage.py shell
```

```python
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

### dbshell

Open database shell.

```bash
python manage.py dbshell
```

Opens appropriate shell for your database:
- SQLite: `sqlite3`
- PostgreSQL: `psql`
- MySQL: `mysql`

## MCP Commands

### mcp stdio

Start MCP server in stdio mode (for AI assistants).

```bash
python manage.py mcp stdio
```

### mcp serve

Start MCP server in HTTP mode.

```bash
python manage.py mcp serve --host 0.0.0.0 --port 8080
```

See [MCP Tools](mcp.md) for full documentation.

## Utility Commands

### showurls

Display all registered URL routes.

```bash
python manage.py showurls
```

Output:
```
URL Routes:
  GET    /api/articles/           ArticleViewSet.list
  POST   /api/articles/           ArticleViewSet.create
  GET    /api/articles/{id}/      ArticleViewSet.retrieve
  PUT    /api/articles/{id}/      ArticleViewSet.update
  DELETE /api/articles/{id}/      ArticleViewSet.destroy
  POST   /api/articles/{id}/publish/  ArticleViewSet.publish
```

### collectstatic

Collect static files (if configured).

```bash
python manage.py collectstatic
```

### clearsessions

Clear expired sessions.

```bash
python manage.py clearsessions
```

## Custom Commands

Create custom management commands:

```python
# apps/blog/management/commands/import_articles.py
from zeeb_orm.cli.base import BaseCommand


class Command(BaseCommand):
    help = "Import articles from external source"
    
    def add_arguments(self, parser):
        parser.add_argument(
            "source",
            help="Source file or URL",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Don't actually import",
        )
    
    async def handle(self, *args, **options):
        source = options["source"]
        dry_run = options["dry_run"]
        
        self.stdout.write(f"Importing from {source}...")
        
        if dry_run:
            self.stdout.write("Dry run - no changes made")
            return
        
        # Import logic here
        count = await import_articles(source)
        
        self.stdout.write(
            self.style.SUCCESS(f"Imported {count} articles")
        )
```

Run:
```bash
python manage.py import_articles data.json --dry-run
```

## Command Output Styling

```python
class Command(BaseCommand):
    async def handle(self, *args, **options):
        # Success (green)
        self.stdout.write(self.style.SUCCESS("Operation successful"))
        
        # Error (red)
        self.stdout.write(self.style.ERROR("Operation failed"))
        
        # Warning (yellow)
        self.stdout.write(self.style.WARNING("This might be a problem"))
        
        # Notice (cyan)
        self.stdout.write(self.style.NOTICE("FYI: Something happened"))
        
        # SQL (syntax highlighted)
        self.stdout.write(self.style.SQL("SELECT * FROM articles"))
```

## Global Options

Available for all commands:

```bash
python manage.py <command> --help        # Show help
python manage.py <command> --settings=myproject.settings  # Custom settings
python manage.py <command> --pythonpath=/path  # Add to Python path
python manage.py <command> --verbosity=2  # Verbosity level (0-3)
python manage.py <command> --no-color     # Disable colored output
```

## Environment Variables

```bash
# Set settings module
export ZEEB_SETTINGS_MODULE=myproject.settings

# Database URL override
export DATABASE_URL=postgresql+asyncpg://localhost/mydb

# Debug mode
export DEBUG=true
```

## Next Steps

- [MCP Tools](mcp.md) - AI assistant integration
- [Settings](../configuration/settings.md) - Configuration
- [Migrations](../orm/migrations.md) - Migration system
