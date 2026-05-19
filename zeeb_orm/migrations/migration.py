"""
Django-style Migration base class.

Migration files subclass this to define dependencies and operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zeeb_orm.migrations.operations import Operation


class Migration:
    """
    Base class for migration files.

    Usage::

        from zeeb_orm.migrations import Migration, operations

        class Migration(Migration):
            dependencies = ['0001_initial']
            operations = [
                operations.AddField(
                    model_name='Post',
                    table='posts',
                    name='views',
                    column=sa.Column('views', sa.Integer(), nullable=False, server_default='0'),
                ),
            ]
    """

    # If True, this is the initial migration (creates all tables from scratch)
    initial: bool = False

    # List of migration names this depends on (e.g. ['0001_initial'])
    dependencies: list[str] = []

    # List of migration names this squashed migration replaces (e.g. ['0001_initial', '0002_add_field'])
    # Used by squashmigrations to indicate which migrations are superseded by this one
    replaces: list[str] = []

    # List of Operation instances
    operations: list[Operation] = []

    # If True (default), all operations in this migration run inside a single
    # database transaction. Set to False for migrations that cannot run inside
    # a transaction (e.g. CREATE INDEX CONCURRENTLY on PostgreSQL).
    atomic: bool = True

    def pre_migrate(self, connection) -> None:
        """Hook called before this migration's operations are executed.

        Override in subclasses to perform setup work or validations.
        Receives the active database connection.
        """

    def post_migrate(self, connection) -> None:
        """Hook called after this migration's operations are executed.

        Override in subclasses to perform cleanup or follow-up work.
        Receives the active database connection.
        """
