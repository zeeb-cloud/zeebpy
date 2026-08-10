"""createsuperuser command - Create a superuser account."""

import asyncio
import getpass
import os
import sys
from pathlib import Path


def find_project_root() -> Path | None:
    """Find the project root by looking for manage.py."""
    current = Path.cwd()

    while current != current.parent:
        if (current / "manage.py").exists():
            return current
        current = current.parent

    return None


def load_settings(project_root: Path) -> dict:
    """Load project settings."""
    settings = {"DATABASE": {"url": "sqlite+aiosqlite:///db.sqlite3"}}

    for item in project_root.iterdir():
        if item.is_dir() and (item / "settings.py").exists():
            sys.path.insert(0, str(project_root))
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "settings", item / "settings.py"
                )
                if spec and spec.loader:
                    settings_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(settings_module)
                    if hasattr(settings_module, "DATABASE"):
                        settings["DATABASE"] = settings_module.DATABASE
            except Exception as exc:
                import warnings

                warnings.warn(
                    f"Could not load settings from {item / 'settings.py'}: {exc}",
                    stacklevel=2,
                )
            break

    return settings


async def create_superuser_async(email: str, password: str, username: str | None = None):
    """Create superuser in database."""
    from zeeb_api.auth.backends import create_superuser, get_user_model
    from zeeb_orm import Database

    project_root = find_project_root() or Path.cwd()
    settings = load_settings(project_root)
    db_url = settings["DATABASE"]["url"]

    # Initialize database
    db = Database(db_url)
    await db.connect()

    try:
        # Check if user exists
        User = get_user_model()  # noqa: N806  (a class, despite the assignment)
        existing = await User.objects.filter(email=email).first()
        if existing:
            print(f"Error: User with email '{email}' already exists")
            return False

        # Create superuser
        user = await create_superuser(
            email=email,
            password=password,
            username=username,
            first_name="Admin",
            last_name="User",
        )
        print(f"Superuser created successfully: {email} (id: {user.id})")
        return True

    except Exception as e:
        print(f"Error creating superuser: {e}")
        return False
    finally:
        await db.disconnect()


def run_createsuperuser(
    email: str | None = None,
    password: str | None = None,
    username: str | None = None,
    noinput: bool = False,
) -> int:
    """Create a superuser account."""
    project_root = find_project_root()

    if project_root is None:
        print("Error: Could not find project root (no manage.py found)")
        print("Make sure you're in a Zeeb project directory")
        return 1

    # Add project to path
    sys.path.insert(0, str(project_root))
    os.chdir(project_root)

    # Interactive mode
    if not noinput:
        if not email:
            email = input("Email address: ").strip()
            if not email:
                print("Error: Email is required")
                return 1

        if not password:
            while True:
                password = getpass.getpass("Password: ")
                if len(password) < 8:
                    print("Error: Password must be at least 8 characters")
                    continue
                password2 = getpass.getpass("Password (again): ")
                if password != password2:
                    print("Error: Passwords don't match")
                    continue
                break

        if username is None:
            username = input("Username (optional, press Enter to skip): ").strip() or None
    else:
        # Non-interactive mode requires email and password
        if not email or not password:
            print("Error: --email and --password are required with --noinput")
            return 1

    # Validate email
    if not email or "@" not in email:
        print("Error: Invalid email address")
        return 1

    # Create user
    try:
        success = asyncio.run(create_superuser_async(email, password, username))
        return 0 if success else 1
    except KeyboardInterrupt:
        print("\nOperation cancelled")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        return 1
