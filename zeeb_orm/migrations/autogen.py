"""
``makemigrations``, implemented once.

``python manage.py makemigrations``, :func:`zeeb_orm.migrations.makemigrations`
and the agent layer's ``make_migrations`` are front ends that report
differently; what they *do* is :func:`make_migration`. The models must be
registered first (:func:`zeeb_orm.migrations.cli._register_models`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zeeb_orm.migrations.autodetector import ChangeReport
    from zeeb_orm.migrations.operations import Operation


@dataclass
class MigrationDraft:
    """What :func:`make_migration` found, and what it wrote.

    Attributes:
        operations: The detected operations (empty for ``empty=True``).
        report: Likely renames, applied renames and skipped replay steps.
        name: The migration's name (``"0003_posts_slug"``); ``None`` when
            there was nothing to write.
        path: The file written; ``None`` for a dry run or when nothing changed.
    """

    operations: list[Operation] = field(default_factory=list)
    report: ChangeReport | None = None
    name: str | None = None
    path: Path | None = None

    @property
    def changed(self) -> bool:
        """Whether there is a migration to write (or one was written)."""
        return self.name is not None

    def warnings(self) -> list[str]:
        """The change report's warnings (likely renames first)."""
        return self.report.warnings() if self.report is not None else []


def make_migration(
    migrations_dir: Path | str,
    *,
    name: str | None = None,
    empty: bool = False,
    dry_run: bool = False,
    accept_renames: bool = False,
) -> MigrationDraft:
    """Detect model changes and write them as the next migration.

    Args:
        migrations_dir: The project's migrations directory.
        name: Human-readable suffix for the file name (auto-generated if None).
        empty: Write a migration with no operations, for editing by hand.
        dry_run: Detect and name the migration, but write nothing.
        accept_renames: Emit ``RenameField`` for likely renames instead of
            ``RemoveField`` + ``AddField`` (which drops the column's data).

    Raises:
        MigrationReplayError: an existing migration could not be replayed.
        MigrationLayoutError: the migrations sit in ``migrations/versions/``.
        MigrationSerializationError: a change cannot be written faithfully.
        NotNullFieldError: a new NOT NULL field has no default to fill the
            rows already in its table (``auto_now``/``auto_now_add`` fields
            are backfilled with the current time instead); nothing is written.
    """
    from zeeb_orm.migrations.autodetector import (
        ChangeReport,
        check_not_null_additions,
        detect_changes_with_report,
    )
    from zeeb_orm.migrations.executor import (
        check_layout,
        latest_migration,
        list_migration_files,
    )
    from zeeb_orm.migrations.writer import migration_name, write_migration

    migrations_dir = Path(migrations_dir)
    if empty:
        check_layout(migrations_dir)
        operations: list[Operation] = []
        report = ChangeReport(latest_migration=latest_migration(migrations_dir))
        name = name or "empty"
    else:
        operations, report = detect_changes_with_report(
            migrations_dir=str(migrations_dir), accept_renames=accept_renames
        )
        if not operations:
            return MigrationDraft(operations=[], report=report)
        # A NOT NULL field nothing can backfill would write a migration that
        # never applies (and wedges every later makemigrations): refuse first.
        check_not_null_additions(operations, report)

    initial = not list_migration_files(migrations_dir)
    planned = migration_name(migrations_dir, operations, name=name, initial=initial)
    if dry_run:
        return MigrationDraft(operations=operations, report=report, name=planned)

    # The replay already loaded every migration; do not load them all again.
    latest = report.latest_migration
    path = write_migration(
        migrations_dir,
        operations=operations,
        name=name,
        dependencies=[latest] if latest else [],
        initial=initial,
    )
    return MigrationDraft(operations=operations, report=report, name=path.stem, path=path)
