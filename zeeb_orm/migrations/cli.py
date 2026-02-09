"""Migration CLI commands using Alembic."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any


def get_alembic_config(migrations_dir: str | None = None) -> Any:
    """Create Alembic config programmatically."""
    from alembic.config import Config

    from zeeb_orm.conf.settings import get_settings

    settings = get_settings()
    migrations_dir = migrations_dir or settings.migrations_dir

    # Create alembic.ini content in memory
    config = Config()
    config.set_main_option("script_location", migrations_dir)
    config.set_main_option("sqlalchemy.url", settings.database.url)

    return config


def init_migrations(directory: str = "migrations") -> None:
    """
    Initialize migrations directory structure.

    Similar to Django's: python manage.py migrate --fake-initial
    Or Alembic's: alembic init migrations
    """
    from alembic import command

    from zeeb_orm.migrations.templates import create_migrations_env

    migrations_path = Path(directory)

    if migrations_path.exists():
        print(f"Migrations directory '{directory}' already exists.")
        return

    # Create directory structure
    migrations_path.mkdir(parents=True)
    (migrations_path / "versions").mkdir()

    # Create env.py
    create_migrations_env(migrations_path)

    # Create script.py.mako template
    mako_template = '''"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
'''
    (migrations_path / "script.py.mako").write_text(mako_template)

    print(f"Created migrations directory: {directory}")
    print("  - versions/     (migration files)")
    print("  - env.py        (Alembic environment)")
    print("  - script.py.mako (migration template)")


def makemigrations(
    message: str = "auto",
    autogenerate: bool = True,
    empty: bool = False,
    migrations_dir: str | None = None,
) -> str | None:
    """
    Create a new migration file.

    Similar to Django's: python manage.py makemigrations
    Or Alembic's: alembic revision --autogenerate -m "message"

    Returns the revision ID if successful.
    """
    from alembic import command
    from alembic.script import ScriptDirectory

    from zeeb_orm.conf.settings import get_settings

    settings = get_settings()
    migrations_dir = migrations_dir or settings.migrations_dir

    # Ensure migrations directory exists
    if not Path(migrations_dir).exists():
        print(f"Migrations directory '{migrations_dir}' not found.")
        print("Run 'zeeb init' first to initialize migrations.")
        return None

    config = get_alembic_config(migrations_dir)

    if empty:
        # Create empty migration
        command.revision(config, message=message, autogenerate=False)
    else:
        # Auto-generate migration from model changes
        command.revision(config, message=message, autogenerate=autogenerate)

    # Get the latest revision
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()

    print(f"Created new migration: {head}")
    return head


def migrate(
    revision: str = "head",
    migrations_dir: str | None = None,
    sql: bool = False,
) -> None:
    """
    Apply migrations to the database.

    Similar to Django's: python manage.py migrate
    Or Alembic's: alembic upgrade head

    Args:
        revision: Target revision ('head' for latest, or specific revision)
        migrations_dir: Path to migrations directory
        sql: If True, output SQL instead of executing
    """
    from alembic import command

    from zeeb_orm.conf.settings import get_settings

    settings = get_settings()
    migrations_dir = migrations_dir or settings.migrations_dir

    if not Path(migrations_dir).exists():
        print(f"Migrations directory '{migrations_dir}' not found.")
        print("Run 'zeeb init' first to initialize migrations.")
        return

    config = get_alembic_config(migrations_dir)

    if sql:
        # Output SQL only
        command.upgrade(config, revision, sql=True)
    else:
        command.upgrade(config, revision)
        print(f"Migrated to: {revision}")


def rollback(
    revision: str = "-1",
    migrations_dir: str | None = None,
    sql: bool = False,
) -> None:
    """
    Rollback migrations.

    Similar to Django's: python manage.py migrate app_name 0001
    Or Alembic's: alembic downgrade -1

    Args:
        revision: Target revision ('-1' for one step back, 'base' for all)
        migrations_dir: Path to migrations directory
        sql: If True, output SQL instead of executing
    """
    from alembic import command

    from zeeb_orm.conf.settings import get_settings

    settings = get_settings()
    migrations_dir = migrations_dir or settings.migrations_dir

    if not Path(migrations_dir).exists():
        print(f"Migrations directory '{migrations_dir}' not found.")
        return

    config = get_alembic_config(migrations_dir)

    if sql:
        command.downgrade(config, revision, sql=True)
    else:
        command.downgrade(config, revision)
        print(f"Rolled back to: {revision}")


def showmigrations(migrations_dir: str | None = None) -> None:
    """
    Show all migrations and their status.

    Similar to Django's: python manage.py showmigrations
    Or Alembic's: alembic history
    """
    from alembic import command
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine

    from zeeb_orm.conf.settings import get_settings

    settings = get_settings()
    migrations_dir = migrations_dir or settings.migrations_dir

    if not Path(migrations_dir).exists():
        print(f"Migrations directory '{migrations_dir}' not found.")
        return

    config = get_alembic_config(migrations_dir)
    script = ScriptDirectory.from_config(config)

    # Get current revision from database
    # Convert async URL to sync for checking
    url = settings.database.url
    sync_url = url.replace("+asyncpg", "").replace("+aiomysql", "").replace("+aiosqlite", "")

    try:
        engine = create_engine(sync_url)
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            current_rev = context.get_current_revision()
    except Exception:
        current_rev = None

    print("Migrations:")
    print("-" * 50)

    for rev in script.walk_revisions():
        status = "[X]" if rev.revision == current_rev else "[ ]"
        print(f"  {status} {rev.revision[:12]} - {rev.doc or 'No description'}")


def current(migrations_dir: str | None = None) -> str | None:
    """
    Show current migration revision.

    Similar to Alembic's: alembic current
    """
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    from zeeb_orm.conf.settings import get_settings

    settings = get_settings()
    migrations_dir = migrations_dir or settings.migrations_dir

    # Convert async URL to sync
    url = settings.database.url
    sync_url = url.replace("+asyncpg", "").replace("+aiomysql", "").replace("+aiosqlite", "")

    try:
        engine = create_engine(sync_url)
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            current_rev = context.get_current_revision()
            print(f"Current revision: {current_rev or 'None (no migrations applied)'}")
            return current_rev
    except Exception as e:
        print(f"Error getting current revision: {e}")
        return None


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="zeeb",
        description="Zeeb ORM - Django-like migrations powered by Alembic",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize migrations directory")
    init_parser.add_argument(
        "-d",
        "--directory",
        default="migrations",
        help="Migrations directory name (default: migrations)",
    )

    # makemigrations command
    make_parser = subparsers.add_parser("makemigrations", help="Create new migration")
    make_parser.add_argument(
        "-m",
        "--message",
        default="auto",
        help="Migration message",
    )
    make_parser.add_argument(
        "--empty",
        action="store_true",
        help="Create empty migration (no auto-detection)",
    )
    make_parser.add_argument(
        "-d",
        "--directory",
        help="Migrations directory",
    )

    # migrate command
    migrate_parser = subparsers.add_parser("migrate", help="Apply migrations")
    migrate_parser.add_argument(
        "revision",
        nargs="?",
        default="head",
        help="Target revision (default: head)",
    )
    migrate_parser.add_argument(
        "--sql",
        action="store_true",
        help="Output SQL instead of executing",
    )
    migrate_parser.add_argument(
        "-d",
        "--directory",
        help="Migrations directory",
    )

    # rollback command
    rollback_parser = subparsers.add_parser("rollback", help="Rollback migrations")
    rollback_parser.add_argument(
        "revision",
        nargs="?",
        default="-1",
        help="Target revision (default: -1, use 'base' for all)",
    )
    rollback_parser.add_argument(
        "--sql",
        action="store_true",
        help="Output SQL instead of executing",
    )
    rollback_parser.add_argument(
        "-d",
        "--directory",
        help="Migrations directory",
    )

    # showmigrations command
    show_parser = subparsers.add_parser("showmigrations", help="Show migration status")
    show_parser.add_argument(
        "-d",
        "--directory",
        help="Migrations directory",
    )

    # current command
    current_parser = subparsers.add_parser("current", help="Show current revision")
    current_parser.add_argument(
        "-d",
        "--directory",
        help="Migrations directory",
    )

    args = parser.parse_args()

    if args.command == "init":
        init_migrations(args.directory)
    elif args.command == "makemigrations":
        makemigrations(
            message=args.message,
            empty=args.empty,
            migrations_dir=args.directory,
        )
    elif args.command == "migrate":
        migrate(
            revision=args.revision,
            migrations_dir=args.directory,
            sql=args.sql,
        )
    elif args.command == "rollback":
        rollback(
            revision=args.revision,
            migrations_dir=args.directory,
            sql=args.sql,
        )
    elif args.command == "showmigrations":
        showmigrations(migrations_dir=args.directory)
    elif args.command == "current":
        current(migrations_dir=args.directory)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
