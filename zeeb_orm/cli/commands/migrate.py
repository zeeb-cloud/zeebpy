"""Migration commands - makemigrations, migrate, showmigrations.

Django-style migrations:
- Migrations are stored per-app in apps/{app}/migrations/
- Only INSTALLED_APPS are considered for migrations
- Built-in zeeb_auth app handles User/Permission models
"""

import os
import sys
import subprocess
import re
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Any


def find_project_root() -> Path | None:
    """Find the project root by looking for manage.py."""
    current = Path.cwd()
    
    while current != current.parent:
        if (current / "manage.py").exists():
            return current
        current = current.parent
    
    return None


def load_project_settings(project_root: Path) -> dict[str, Any]:
    """Load project settings dynamically."""
    settings: dict[str, Any] = {
        "DATABASE": {"url": "sqlite:///db.sqlite3"},
        "INSTALLED_APPS": [],
    }
    
    # Find settings module
    for item in project_root.iterdir():
        if item.is_dir() and (item / "settings.py").exists():
            settings_path = item / "settings.py"
            sys.path.insert(0, str(project_root))
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location("settings", settings_path)
                if spec and spec.loader:
                    settings_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(settings_module)
                    if hasattr(settings_module, "DATABASE"):
                        settings["DATABASE"] = settings_module.DATABASE
                    if hasattr(settings_module, "INSTALLED_APPS"):
                        settings["INSTALLED_APPS"] = settings_module.INSTALLED_APPS
            except Exception as e:
                print(f"Warning: Could not load settings: {e}")
            finally:
                if str(project_root) in sys.path:
                    sys.path.remove(str(project_root))
            break
    
    return settings


def get_installed_apps(project_root: Path) -> list[str]:
    """Get list of installed apps from settings, always including zeeb_auth."""
    settings = load_project_settings(project_root)
    apps = list(settings.get("INSTALLED_APPS", []))
    
    # Always include zeeb_auth for built-in User/Permission models
    if "zeeb_auth" not in apps:
        apps.insert(0, "zeeb_auth")
    
    return apps


def get_sync_url(db_url: str) -> str:
    """Convert async database URL to sync for Alembic."""
    return (db_url
        .replace("+aiosqlite", "")
        .replace("+asyncpg", "+psycopg2")
        .replace("+aiomysql", "+pymysql"))


def ensure_migrations_structure(project_root: Path) -> Path:
    """Ensure base migrations structure exists."""
    migrations_dir = project_root / "migrations"
    migrations_dir.mkdir(exist_ok=True)
    
    versions_dir = migrations_dir / "versions"
    versions_dir.mkdir(exist_ok=True)
    
    # Create alembic.ini if needed
    alembic_ini = migrations_dir / "alembic.ini"
    if not alembic_ini.exists():
        _create_alembic_ini(migrations_dir)
    
    # Create env.py if needed  
    env_py = migrations_dir / "env.py"
    if not env_py.exists():
        _create_env_py(migrations_dir)
    
    # Create script.py.mako if needed
    script_mako = migrations_dir / "script.py.mako"
    if not script_mako.exists():
        _create_script_mako(migrations_dir)
    
    return migrations_dir


def _create_alembic_ini(migrations_dir: Path) -> None:
    """Create alembic.ini configuration file."""
    alembic_ini = migrations_dir / "alembic.ini"
    alembic_ini.write_text("""[alembic]
script_location = migrations
version_locations = migrations/versions
sqlalchemy.url = sqlite:///db.sqlite3

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
""")


def _create_env_py(migrations_dir: Path) -> None:
    """Create env.py for Alembic."""
    env_py = migrations_dir / "env.py"
    env_py.write_text('''"""Alembic environment configuration - Django-style migrations."""

import sys
from pathlib import Path
from logging.config import fileConfig

from sqlalchemy import pool, create_engine
from sqlalchemy.engine import Connection
from alembic import context

# Add project root to path for model imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def load_settings():
    """Load project settings."""
    for item in project_root.iterdir():
        if item.is_dir() and (item / "settings.py").exists():
            settings_path = item / "settings.py"
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location("settings", settings_path)
                if spec and spec.loader:
                    settings_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(settings_module)
                    return settings_module
            except Exception:
                pass
    return None


def get_installed_apps():
    """Get installed apps from settings."""
    settings = load_settings()
    apps = list(getattr(settings, "INSTALLED_APPS", []))
    if "zeeb_auth" not in apps:
        apps.insert(0, "zeeb_auth")
    return apps


def get_auth_user_model():
    """Get the AUTH_USER_MODEL from settings, if set."""
    settings = load_settings()
    return getattr(settings, "AUTH_USER_MODEL", None)


def register_models():
    """Register all models from installed apps."""
    from zeeb_orm import Model
    
    # Check for custom user model
    auth_user_model = get_auth_user_model()
    
    # Register built-in auth models (Permission, UserPermission always needed)
    try:
        from zeeb_api.auth.models import Permission, UserPermission
        Permission._get_table()
        UserPermission._get_table()
        
        # Only register default User if no custom user model
        if not auth_user_model:
            from zeeb_api.auth.models import User
            User._get_table()
    except Exception as e:
        print(f"Warning: Could not import auth models: {e}")
    
    # Register models from installed apps (including custom user model)
    installed_apps = get_installed_apps()
    for app in installed_apps:
        if app == "zeeb_auth":
            continue  # Already registered above
        
        clean_name = app.replace("apps.", "")
        try:
            import importlib
            module_name = f"apps.{clean_name}.models"
            models_module = importlib.import_module(module_name)
            
            for name in dir(models_module):
                obj = getattr(models_module, name)
                if (isinstance(obj, type) and 
                    issubclass(obj, Model) and 
                    obj is not Model and
                    not getattr(obj._meta, 'abstract', False)):
                    obj._get_table()
        except Exception:
            # App might not have models yet
            pass


# Register all models
register_models()

from zeeb_orm.models.base import metadata as target_metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """Run migrations in offline mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with a connection."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in online mode."""
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
''')


def _create_script_mako(migrations_dir: Path) -> None:
    """Create script.py.mako template."""
    script_mako = migrations_dir / "script.py.mako"
    script_mako.write_text('''"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# Revision identifiers
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """Upgrade database schema."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Downgrade database schema."""
    ${downgrades if downgrades else "pass"}
''')


def _update_alembic_ini_url(project_root: Path, sync_url: str) -> None:
    """Update alembic.ini with the correct database URL."""
    alembic_ini = project_root / "migrations" / "alembic.ini"
    if alembic_ini.exists():
        content = alembic_ini.read_text()
        content = re.sub(
            r"sqlalchemy\.url = .*",
            f"sqlalchemy.url = {sync_url}",
            content
        )
        alembic_ini.write_text(content)


def run_init(directory: str) -> int:
    """Initialize migrations for a project."""
    project_root = find_project_root() or Path.cwd()
    migrations_dir = project_root / directory
    
    if migrations_dir.exists() and (migrations_dir / "env.py").exists():
        print(f"Migrations already initialized in '{directory}'")
        return 0
    
    print(f"Initializing migrations in '{directory}'...")
    
    try:
        ensure_migrations_structure(project_root)
        
        print(f"✓ Migrations initialized in '{directory}'")
        print(f"\nCreated:")
        print(f"  {directory}/alembic.ini")
        print(f"  {directory}/env.py")
        print(f"  {directory}/script.py.mako")
        print(f"  {directory}/versions/")
        
        return 0
        
    except Exception as e:
        print(f"Error initializing migrations: {e}")
        return 1


def run_makemigrations(app: str | None, name: str | None, empty: bool) -> int:
    """Create new migration files for specified app or all installed apps.
    
    Unlike Django's per-app migrations, this creates a single migration file
    that includes all changes from all installed apps for simplicity with Alembic.
    The migration message indicates which apps have changes.
    """
    project_root = find_project_root()
    if project_root is None:
        print("Error: Could not find project root (no manage.py found)")
        return 1
    
    # Ensure migrations structure exists
    ensure_migrations_structure(project_root)
    
    settings = load_project_settings(project_root)
    db_url = settings.get("DATABASE", {}).get("url", "sqlite:///db.sqlite3")
    sync_url = get_sync_url(db_url)
    
    # Update alembic.ini with correct URL
    _update_alembic_ini_url(project_root, sync_url)
    
    # Get installed apps for display
    installed_apps = get_installed_apps(project_root)
    
    print("Checking for model changes in installed apps:")
    for app_name in installed_apps:
        clean_name = app_name.replace("apps.", "")
        print(f"  - {clean_name}")
    
    # Build migration message
    if name:
        message = name
    elif app:
        clean_app = app.replace("apps.", "")
        message = f"{clean_app}_changes"
    else:
        message = f"auto_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    try:
        original_dir = os.getcwd()
        os.chdir(project_root)
        
        migrations_dir = project_root / "migrations"
        
        cmd = [
            sys.executable, "-m", "alembic",
            "-c", str(migrations_dir / "alembic.ini"),
            "revision",
            "--autogenerate" if not empty else "",
            "-m", message,
        ]
        cmd = [c for c in cmd if c]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            output = result.stdout + result.stderr
            
            # Check if any changes were detected
            if "Detected added table" in output or "Detected added column" in output or \
               "Detected removed" in output or "Detected changed" in output:
                print("\n✓ Migration created successfully")
                
                # Show detected changes
                for line in output.split("\n"):
                    if "Detected" in line:
                        print(f"  {line.strip()}")
                    elif "Generating" in line:
                        print(f"\n  {line.strip()}")
                
                print("\nRun 'python manage.py migrate' to apply.")
            else:
                print("\nNo changes detected in any app models.")
        else:
            print("\nError creating migration:")
            if result.stderr:
                print(result.stderr)
            if result.stdout:
                print(result.stdout)
            return 1
        
        return 0
        
    except Exception as e:
        print(f"Error: {e}")
        return 1
    finally:
        os.chdir(original_dir)


def run_migrate(app: str | None, migration: str | None, rollback: int | None, fake: bool) -> int:
    """Apply or rollback migrations."""
    project_root = find_project_root()
    if project_root is None:
        print("Error: Could not find project root")
        return 1
    
    migrations_dir = project_root / "migrations"
    
    if not migrations_dir.exists() or not (migrations_dir / "env.py").exists():
        print("Migrations not initialized. Run 'python manage.py makemigrations' first.")
        return 1
    
    settings = load_project_settings(project_root)
    db_url = settings.get("DATABASE", {}).get("url", "sqlite:///db.sqlite3")
    sync_url = get_sync_url(db_url)
    
    # Update alembic.ini with correct URL
    _update_alembic_ini_url(project_root, sync_url)
    
    # Show which apps will be affected
    installed_apps = get_installed_apps(project_root)
    print("Running migrations for:")
    for app_name in installed_apps:
        clean_name = app_name.replace("apps.", "")
        print(f"  - {clean_name}")
    print()
    
    try:
        original_dir = os.getcwd()
        os.chdir(project_root)
        
        if rollback is not None:
            # Rollback N migrations
            print(f"Rolling back {rollback} migration(s)...")
            target = f"-{rollback}" if rollback > 0 else "base"
            cmd = [
                sys.executable, "-m", "alembic",
                "-c", str(migrations_dir / "alembic.ini"),
                "downgrade", target,
            ]
        elif migration:
            # Migrate to specific revision
            print(f"Migrating to: {migration}")
            direction = "downgrade" if migration.startswith("-") else "upgrade"
            cmd = [
                sys.executable, "-m", "alembic",
                "-c", str(migrations_dir / "alembic.ini"),
                direction, migration,
            ]
        else:
            # Migrate to head
            print("Applying migrations...")
            cmd = [
                sys.executable, "-m", "alembic",
                "-c", str(migrations_dir / "alembic.ini"),
                "upgrade", "head",
            ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            output = result.stdout + result.stderr
            
            # Show what was done
            applied_any = False
            for line in output.split("\n"):
                if "Running upgrade" in line or "Running downgrade" in line:
                    print(f"  {line.strip()}")
                    applied_any = True
            
            if applied_any:
                print("\n✓ Migrations applied successfully")
            else:
                print("✓ No pending migrations to apply")
        else:
            print("Error applying migrations:")
            if result.stderr:
                print(result.stderr)
            if result.stdout:
                print(result.stdout)
            return 1
        
        return 0
        
    except Exception as e:
        print(f"Error: {e}")
        return 1
    finally:
        os.chdir(original_dir)


def run_showmigrations(app: str | None) -> int:
    """Show migration status."""
    project_root = find_project_root()
    if project_root is None:
        print("Error: Could not find project root")
        return 1
    
    migrations_dir = project_root / "migrations"
    versions_dir = migrations_dir / "versions"
    
    if not migrations_dir.exists():
        print("No migrations found. Run 'python manage.py makemigrations' first.")
        return 0
    
    settings = load_project_settings(project_root)
    db_url = settings.get("DATABASE", {}).get("url", "sqlite:///db.sqlite3")
    sync_url = get_sync_url(db_url)
    
    _update_alembic_ini_url(project_root, sync_url)
    
    # Get current revision
    current_rev = None
    try:
        original_dir = os.getcwd()
        os.chdir(project_root)
        
        cmd_current = [
            sys.executable, "-m", "alembic",
            "-c", str(migrations_dir / "alembic.ini"),
            "current",
        ]
        result_current = subprocess.run(cmd_current, capture_output=True, text=True)
        if result_current.returncode == 0 and result_current.stdout.strip():
            current_rev = result_current.stdout.strip().split()[0]
        
    finally:
        os.chdir(original_dir)
    
    # Show installed apps
    installed_apps = get_installed_apps(project_root)
    print("Installed apps:")
    for app_name in installed_apps:
        clean_name = app_name.replace("apps.", "")
        print(f"  - {clean_name}")
    
    print("\nMigrations:")
    print("-" * 50)
    
    # Find all migration files
    if versions_dir.exists():
        migration_files = sorted([
            f for f in versions_dir.glob("*.py") 
            if f.name != "__init__.py"
        ])
        
        if not migration_files:
            print("  (no migrations)")
        else:
            for mf in migration_files:
                # Extract revision from file content
                content = mf.read_text()
                rev_match = re.search(r"revision:\s*str\s*=\s*['\"]([^'\"]+)['\"]", content)
                rev = rev_match.group(1) if rev_match else "?"
                
                # Extract message from file content
                msg_match = re.search(r'^"""(.+?)$', content, re.MULTILINE)
                msg = msg_match.group(1) if msg_match else mf.stem
                
                # Check if applied
                is_applied = current_rev and (rev == current_rev or rev in str(current_rev))
                marker = "[X]" if is_applied else "[ ]"
                
                print(f"  {marker} {rev[:12]}... {msg}")
    else:
        print("  (no migrations)")
    
    print("-" * 50)
    if current_rev:
        print(f"Current: {current_rev}")
    else:
        print("Current: (none applied)")
    
    return 0
