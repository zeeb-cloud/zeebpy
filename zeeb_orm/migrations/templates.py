"""Migration templates for Alembic integration."""

from __future__ import annotations

from pathlib import Path


def create_migrations_env(migrations_path: Path) -> None:
    """Create the Alembic env.py file with Zeeb ORM integration."""
    env_content = '''"""Alembic environment configuration for Zeeb ORM."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import Zeeb ORM metadata
from zeeb_orm.models.base import metadata as target_metadata

# Alembic Config object
config = context.config

# Configure logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well. By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = config.get_main_option("sqlalchemy.url")

    # Convert async URL to sync
    if url:
        url = url.replace("+asyncpg", "")
        url = url.replace("+aiomysql", "")
        url = url.replace("+aiosqlite", "")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    # Get URL and convert to sync driver
    url = config.get_main_option("sqlalchemy.url")
    if url:
        url = url.replace("+asyncpg", "")
        url = url.replace("+aiomysql", "")
        url = url.replace("+aiosqlite", "")

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
'''

    (migrations_path / "env.py").write_text(env_content)


def get_migration_template() -> str:
    """Get the default migration file template."""
    return '''"""${message}

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
    """Upgrade database schema."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Downgrade database schema."""
    ${downgrades if downgrades else "pass"}
'''
