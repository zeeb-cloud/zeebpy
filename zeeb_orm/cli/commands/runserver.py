"""runserver command - Start FastAPI development server."""

import sys
import os
from pathlib import Path


def find_project_root() -> Path | None:
    """Find the project root by looking for manage.py."""
    current = Path.cwd()
    
    while current != current.parent:
        if (current / "manage.py").exists():
            return current
        current = current.parent
    
    return None


def find_asgi_app(project_root: Path) -> str | None:
    """Find the ASGI application path."""
    # Look for asgi.py in project subdirectory
    for item in project_root.iterdir():
        if item.is_dir() and (item / "asgi.py").exists():
            return f"{item.name}.asgi:app"
    return None


def check_migrations_before_start(project_root: Path) -> bool:
    """
    Check migration status before starting server.
    Returns True if OK to start, False if should abort.
    """
    from zeeb_orm.migrations.state import (
        get_migration_state, 
        MigrationError,
        find_project_root as find_migrations_root
    )
    
    migrations_dir = project_root / "migrations"
    versions_dir = migrations_dir / "versions"
    
    separator = "=" * 60
    
    # Check 1: Does migrations directory exist?
    if not migrations_dir.exists():
        print(f"\n{separator}")
        print("ERROR: Migrations not initialized!")
        print(separator)
        print("\nYour project needs to be initialized with migrations.")
        print("\nRun the following commands:")
        print("  python manage.py init           # Initialize migrations")
        print("  python manage.py makemigrations  # Create initial migrations")
        print("  python manage.py migrate         # Apply migrations to database")
        print(f"\n{separator}\n")
        return False
    
    # Check 2: Are there any migration files?
    migration_files = list(versions_dir.glob("*.py")) if versions_dir.exists() else []
    migration_files = [f for f in migration_files if not f.name.startswith("__")]
    
    if not migration_files:
        print(f"\n{separator}")
        print("ERROR: No migrations found!")
        print(separator)
        print("\nYou have initialized migrations but haven't created any yet.")
        print("This means your database tables won't exist.")
        print("\nRun the following commands:")
        print("  python manage.py makemigrations  # Create migrations for your models")
        print("  python manage.py migrate         # Apply migrations to database")
        print(f"\n{separator}\n")
        return False
    
    # Check 3: Are all migrations applied?
    try:
        state = get_migration_state(project_root)
        
        if state.current_revision != state.head_revision:
            pending = state.pending_migrations or "some"
            print(f"\n{separator}")
            print("ERROR: Unapplied migrations!")
            print(separator)
            print(f"\nYou have {pending} unapplied migration(s).")
            print("\nRun the following command:")
            print("  python manage.py migrate         # Apply migrations to database")
            print(f"\n{separator}\n")
            return False
            
    except Exception as e:
        # If we can't check migration state (e.g., alembic not configured),
        # warn but allow startup
        print(f"\n{separator}")
        print("WARNING: Could not verify migration status")
        print(separator)
        print(f"\nReason: {e}")
        print("\nMake sure you have run:")
        print("  python manage.py init")
        print("  python manage.py makemigrations")
        print("  python manage.py migrate")
        print(f"\n{separator}\n")
        # Continue anyway - let the app fail if tables don't exist
    
    return True


def run_server(addrport: str, reload: bool) -> int:
    """Start the development server using uvicorn."""
    project_root = find_project_root()
    
    if project_root is None:
        print("Error: Could not find project root (no manage.py found)")
        return 1
    
    # Find ASGI app
    asgi_app = find_asgi_app(project_root)
    if asgi_app is None:
        print("Error: Could not find ASGI application")
        print("Expected: <project_name>/asgi.py with 'app' variable")
        return 1
    
    # Check if migrations should be enforced
    enforce_migrations = True
    try:
        # Try to load project settings
        sys.path.insert(0, str(project_root))
        project_name = asgi_app.split(".")[0]
        settings_module = __import__(f"{project_name}.settings", fromlist=["ENFORCE_MIGRATIONS"])
        enforce_migrations = getattr(settings_module, "ENFORCE_MIGRATIONS", True)
    except Exception:
        pass  # Default to enforcing migrations
    
    # Check migrations before starting (unless disabled)
    if enforce_migrations and not check_migrations_before_start(project_root):
        return 1
    
    # Parse host:port
    if ":" in addrport:
        host, port_str = addrport.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            print(f"Error: Invalid port number: {port_str}")
            return 1
    else:
        try:
            port = int(addrport)
            host = "127.0.0.1"
        except ValueError:
            host = addrport
            port = 8000
    
    # Add project root to path
    sys.path.insert(0, str(project_root))
    os.chdir(project_root)
    
    print(f"Starting development server at http://{host}:{port}/")
    print(f"ASGI app: {asgi_app}")
    print("Quit with CTRL+C")
    print()
    
    try:
        import uvicorn
        
        uvicorn.run(
            asgi_app,
            host=host,
            port=port,
            reload=reload,
            reload_dirs=[str(project_root)] if reload else None,
        )
        return 0
        
    except ImportError:
        print("Error: uvicorn is not installed")
        print("Install it with: pip install uvicorn[standard]")
        return 1
    except KeyboardInterrupt:
        print("\nServer stopped")
        return 0
    except Exception as e:
        print(f"Error starting server: {e}")
        return 1
