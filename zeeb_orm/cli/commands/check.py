"""check command - Verify project configuration and migration state."""

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


def check_migrations(project_root: Path) -> tuple[bool, list[str]]:
    """Check if there are unapplied migrations."""
    issues = []
    migrations_dir = project_root / "migrations"
    
    if not migrations_dir.exists():
        issues.append("migrations: Directory not found. Run 'zeeb init' first.")
        return False, issues
    
    versions_dir = migrations_dir / "versions"
    if not versions_dir.exists():
        issues.append("migrations: versions/ directory not found.")
        return False, issues
    
    # Count migration files
    migration_files = list(versions_dir.glob("*.py"))
    migration_files = [f for f in migration_files if not f.name.startswith("__")]
    
    if not migration_files:
        issues.append("migrations: No migrations found. Run 'zeeb makemigrations' first.")
        return False, issues
    
    return True, []


def check_settings(project_root: Path) -> tuple[bool, list[str]]:
    """Check project settings."""
    issues = []
    
    # Find settings module
    settings_found = False
    for item in project_root.iterdir():
        if item.is_dir() and (item / "settings.py").exists():
            settings_found = True
            settings_path = item / "settings.py"
            
            # Check for required settings
            content = settings_path.read_text()
            if "DATABASE" not in content:
                issues.append(f"settings: DATABASE configuration missing in {item.name}/settings.py")
            if "INSTALLED_APPS" not in content:
                issues.append(f"settings: INSTALLED_APPS missing in {item.name}/settings.py")
            break
    
    if not settings_found:
        issues.append("settings: No settings.py found in any project subdirectory.")
        return False, issues
    
    return len(issues) == 0, issues


def check_database(project_root: Path, deploy: bool = False) -> tuple[bool, list[str]]:
    """Check database configuration and state."""
    issues = []
    
    # Load settings
    sys.path.insert(0, str(project_root))
    
    try:
        for item in project_root.iterdir():
            if item.is_dir() and (item / "settings.py").exists():
                import importlib.util
                spec = importlib.util.spec_from_file_location("settings", item / "settings.py")
                if spec and spec.loader:
                    settings = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(settings)
                    
                    db_config = getattr(settings, "DATABASE", {})
                    db_url = db_config.get("url", "")
                    
                    if not db_url:
                        issues.append("database: No database URL configured.")
                    elif deploy:
                        # Check for production-ready database
                        if "sqlite" in db_url.lower():
                            issues.append("database: SQLite is not recommended for production. Consider PostgreSQL or MySQL.")
                    
                    # Check DEBUG mode for deploy
                    if deploy:
                        debug = getattr(settings, "DEBUG", True)
                        if debug:
                            issues.append("settings: DEBUG is True. Set DEBUG=False for production.")
                        
                        secret_key = getattr(settings, "SECRET_KEY", None)
                        if not secret_key:
                            issues.append("settings: SECRET_KEY not configured.")
                break
    except Exception as e:
        issues.append(f"database: Could not load settings: {e}")
    finally:
        if str(project_root) in sys.path:
            sys.path.remove(str(project_root))
    
    return len(issues) == 0, issues


def check_apps(project_root: Path) -> tuple[bool, list[str]]:
    """Check installed apps configuration."""
    issues = []
    apps_dir = project_root / "apps"
    
    if not apps_dir.exists():
        issues.append("apps: 'apps/' directory not found.")
        return False, issues
    
    # Count apps
    apps = [d for d in apps_dir.iterdir() if d.is_dir() and (d / "__init__.py").exists()]
    
    if not apps:
        # Not an error, just info
        pass
    else:
        # Check each app has models.py
        for app in apps:
            if not (app / "models.py").exists():
                issues.append(f"apps: {app.name}/ missing models.py")
    
    return len(issues) == 0, issues


def run_check(deploy: bool = False) -> int:
    """Run project checks."""
    project_root = find_project_root()
    
    if project_root is None:
        print("Error: Could not find project root (no manage.py found)")
        return 1
    
    print(f"Checking project: {project_root.name}")
    print("=" * 50)
    
    all_ok = True
    
    # Check settings
    print("\n📋 Checking settings...")
    ok, issues = check_settings(project_root)
    if ok:
        print("   ✓ Settings OK")
    else:
        all_ok = False
        for issue in issues:
            print(f"   ✗ {issue}")
    
    # Check apps
    print("\n📦 Checking apps...")
    ok, issues = check_apps(project_root)
    if ok:
        apps_dir = project_root / "apps"
        apps = [d.name for d in apps_dir.iterdir() if d.is_dir() and (d / "__init__.py").exists()]
        if apps:
            print(f"   ✓ Found apps: {', '.join(apps)}")
        else:
            print("   ✓ No apps yet (run 'zeeb startapp <name>' to create one)")
    else:
        all_ok = False
        for issue in issues:
            print(f"   ✗ {issue}")
    
    # Check migrations
    print("\n🔄 Checking migrations...")
    ok, issues = check_migrations(project_root)
    if ok:
        migrations_dir = project_root / "migrations" / "versions"
        migration_files = [f.stem for f in migrations_dir.glob("*.py") if not f.name.startswith("__")]
        print(f"   ✓ Found {len(migration_files)} migration(s)")
    else:
        all_ok = False
        for issue in issues:
            print(f"   ✗ {issue}")
    
    # Check database
    print("\n🗄️  Checking database...")
    ok, issues = check_database(project_root, deploy)
    if ok:
        print("   ✓ Database configuration OK")
    else:
        all_ok = False
        for issue in issues:
            print(f"   ✗ {issue}")
    
    # Summary
    print("\n" + "=" * 50)
    if all_ok:
        print("✅ All checks passed!")
        if deploy:
            print("   Project is ready for deployment.")
        return 0
    else:
        print("❌ Some checks failed. Please fix the issues above.")
        return 1
