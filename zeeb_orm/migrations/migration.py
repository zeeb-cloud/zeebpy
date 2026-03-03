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

    # List of Operation instances
    operations: list[Operation] = []
