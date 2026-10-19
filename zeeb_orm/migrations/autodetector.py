"""
Auto-detect model changes by comparing the models against the migration files.

The existing migrations are replayed — in the order the executor applies them
to a fresh database — into an in-memory SQLite database, and Alembic's
``compare_metadata`` diffs the models against it for tables, columns, indexes
and constraints. A column the replay declared is compared against that
*declaration* instead (type, nullability, server default): SQLite reflection
cannot tell ``DateTime`` from ``DateTime(timezone=True)``, and a migration
must be right on every database, not only the one it was generated against.
The diffs are converted to Django-style Operation objects.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any

from zeeb_orm.migrations.operations import (
    NO_DEFAULT,
    AddConstraint,
    AddField,
    AddIndex,
    AlterField,
    CreateModel,
    DeleteModel,
    Operation,
    RemoveConstraint,
    RemoveField,
    RemoveIndex,
    copy_column,
)


def _default_clause_arg(default) -> Any:
    """A ``DefaultClause``'s value as ``AlterField`` takes it (``NO_DEFAULT`` for none)."""
    if default is None:
        return NO_DEFAULT
    arg = getattr(default, "arg", default)
    return NO_DEFAULT if arg is None else arg


def _named_unique_constraint(table_name: str, constraint):
    """Copy an unnamed UniqueConstraint with a deterministic name.

    The name has to be stable across runs, or the next autodetect sees a different
    constraint and re-emits it forever.
    """
    from sqlalchemy import UniqueConstraint

    columns = [col.key if hasattr(col, "key") else str(col) for col in constraint.columns]
    return UniqueConstraint(*columns, name=f"uq_{table_name}_{'_'.join(columns)}")


def _table_name_to_model_name(table_name: str) -> str:
    """Guess a model name from a table name (e.g. 'blog_posts' -> 'Post')."""
    from zeeb_orm.models.base import _model_registry
    # Try to find the model in the registry (keyed "app.Name"; migration
    # files carry the bare class name)
    for cls in list(_model_registry.values()):
        meta = getattr(cls, '_meta', None)
        if meta and getattr(meta, 'table_name', None) == table_name:
            return cls.__name__
    # Fallback: title-case the table name
    parts = table_name.replace("-", "_").split("_")
    return "".join(p.capitalize() for p in parts)


@dataclass
class PossibleRename:
    """A removed and an added column that look like one renamed field."""

    model_name: str
    table: str
    old_name: str
    new_name: str

    def as_dict(self) -> dict[str, str]:
        """JSON-ready form: ``{"model", "table", "old", "new"}``."""
        return {
            "model": self.model_name,
            "table": self.table,
            "old": self.old_name,
            "new": self.new_name,
        }

    def describe(self) -> str:
        """One-line summary, e.g. ``Post.title -> Post.headline``."""
        return f"{self.model_name}.{self.old_name} -> {self.model_name}.{self.new_name}"


@dataclass
class ChangeReport:
    """What :func:`detect_changes_with_report` found besides the operations.

    Attributes:
        possible_renames: Removed/added column pairs with the same
            definition on the same model. Written as RemoveField + AddField —
            which loses the column's data — unless ``accept_renames`` turned
            them into ``RenameField``.
        renamed: The pairs that were emitted as ``RenameField``.
        skipped: Operations the state replay did not run (``RunPython``
            always; ``RunSQL`` that SQLite cannot execute), each with the
            migration it came from and why. Their effect is missing from the
            comparison state.
    """

    possible_renames: list[PossibleRename] = field(default_factory=list)
    renamed: list[PossibleRename] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def warnings(self) -> list[str]:
        """Human-readable warnings, one per finding."""
        lines = []
        for rename in self.possible_renames:
            lines.append(
                f"{rename.model_name}.{rename.old_name} is removed and "
                f"{rename.model_name}.{rename.new_name} added with the same definition. "
                f"If this is a rename, the migration as written DROPS the data in "
                f"'{rename.old_name}' — re-run makemigrations with --accept-renames "
                f"to emit RenameField instead."
            )
        for rename in self.renamed:
            lines.append(
                f"{rename.model_name}.{rename.old_name} is treated as renamed to "
                f"'{rename.new_name}' (RenameField)."
            )
        lines.extend(self.skipped)
        return lines


class MigrationReplayError(Exception):
    """An existing migration's schema operation failed during state replay.

    Change detection compares the models against the schema the migration
    files produce. A schema operation that cannot be replayed leaves that
    state wrong, and diffing against a wrong state writes wrong (possibly
    destructive) migrations — so this is fatal, not a warning.
    """


def detect_changes(
    database_url: str | None = None,
    migrations_dir: str | None = None,
) -> list[Operation]:
    """
    Compare current model state against existing migration state and return operations.

    This is the core of ``makemigrations`` — it detects what changed. See
    :func:`detect_changes_with_report` for the renames and skipped replay
    operations it also finds (surfaced here as ``RuntimeWarning``).

    Args:
        database_url: Unused, kept for backward compatibility.
        migrations_dir: Path to the migrations directory. If None, auto-detected.

    Returns:
        List of Operation objects representing detected changes.
    """
    operations, report = detect_changes_with_report(migrations_dir=migrations_dir)
    for message in report.warnings():
        warnings.warn(f"makemigrations: {message}", RuntimeWarning, stacklevel=2)
    return operations


def detect_changes_with_report(
    migrations_dir: str | None = None,
    *,
    accept_renames: bool = False,
) -> tuple[list[Operation], ChangeReport]:
    """Detect model changes, reporting likely renames and skipped replay steps.

    Instead of comparing against the live database (which would re-detect
    already-migrated changes if migrations haven't been applied yet), this
    replays the existing migration files into an in-memory SQLite database,
    exactly as the executor would apply them to a fresh one (a squash in place
    of the migrations it replaces), and compares the model metadata against
    that state.

    Replay rules: a schema operation that fails raises
    :class:`MigrationReplayError`; ``RunPython`` is never executed (it is
    data, not schema, and may have side effects); ``RunSQL`` that SQLite
    cannot run is skipped. Both are listed in ``report.skipped``.

    Models with ``Meta.managed = False`` are left out of the comparison: no
    operation is generated for their tables.

    Args:
        migrations_dir: Path to the migrations directory. If None, auto-detected.
        accept_renames: Emit ``RenameField`` for each likely rename instead of
            ``RemoveField`` + ``AddField``.
    """
    from pathlib import Path

    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    from zeeb_orm.migrations.executor import MigrationGraph, _load_all, check_layout
    from zeeb_orm.migrations.operations import RunPython, RunSQL
    from zeeb_orm.models.base import metadata

    report = ChangeReport()

    # Resolve migrations directory
    if migrations_dir is not None:
        mig_dir = Path(migrations_dir)
    else:
        from zeeb_orm.migrations.state import find_project_root
        project_root = find_project_root() or Path.cwd()
        mig_dir = project_root / "migrations"

    # Build migration state in an in-memory SQLite database by replaying the
    # existing migrations the way the executor applies them to a fresh one.
    check_layout(mig_dir)
    loaded = _load_all(mig_dir)
    paths = {name: path for name, path, _ in loaded}
    graph = MigrationGraph(loaded)
    state = _TableState()
    mem_engine = create_engine("sqlite:///:memory:")
    try:
        if loaded:
            with mem_engine.connect() as conn:
                for name in graph.order:
                    mig, path = graph.migrations[name], paths[name]
                    for op in mig.operations:
                        if isinstance(op, RunPython):
                            report.skipped.append(
                                f"Skipped {op.describe()!r} from {path.name} while "
                                "replaying migration state (Python code is not run "
                                "for change detection)."
                            )
                            continue
                        try:
                            op.forward(conn)
                        except Exception as exc:
                            if isinstance(op, RunSQL):
                                report.skipped.append(
                                    f"Skipped {op.describe()!r} from {path.name} while "
                                    f"replaying migration state: {exc}; change "
                                    "detection may be incomplete."
                                )
                                continue
                            raise MigrationReplayError(
                                f"Replaying {op.describe()!r} from {path.name} failed: "
                                f"{exc}. The migration state cannot be rebuilt, so "
                                "no changes can be detected safely — fix or remove "
                                "that operation."
                            ) from exc
                        state.apply(op)
                    conn.commit()

        # Compare model metadata against the in-memory migration state.
        # ``compare_server_default`` surfaces DB-default changes as
        # ``modify_default`` diffs (matching the Alembic env template).
        unmanaged = {
            table.name
            for table in metadata.tables.values()
            if not table.info.get("managed", True)
        }

        def include_object(obj, name, type_, reflected, compare_to) -> bool:
            if type_ == "table":
                return name not in unmanaged
            table = getattr(obj, "table", None)
            return getattr(table, "name", None) not in unmanaged

        with mem_engine.connect() as conn:
            migration_ctx = MigrationContext.configure(
                conn,
                opts={"compare_server_default": True, "include_object": include_object},
            )
            diffs = compare_metadata(migration_ctx, metadata)
    finally:
        mem_engine.dispose()

    tables = [
        table
        for name, table in sorted(metadata.tables.items())
        if name not in unmanaged
    ]
    operations = _convert_diffs(diffs, state)
    operations += _declared_column_changes(tables, state, diffs)
    operations = _find_renames(operations, report, accept_renames)
    return operations, report


class _TableState:
    """Table definitions as the replayed migration files declare them.

    Reflection from the in-memory SQLite database loses the declared types
    (a ``Uuid`` reads back as ``CHAR(32)``), so a ``DeleteModel`` records
    the columns from here — the definitions the migrations themselves used —
    and a rollback recreates the table with them on any backend.
    """

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, Any]] = {}

    def apply(self, op: Operation) -> None:
        from zeeb_orm.migrations import operations as ops

        if isinstance(op, ops.CreateModel):
            columns = {col.name: copy_column(col) for col in op.columns}
            for name in op.primary_key:
                # A primary key column is NOT NULL whatever the Column says.
                if name in columns:
                    columns[name].nullable = False
            self.tables[op.table] = {
                "columns": columns,
                "primary_key": list(op.primary_key),
                "constraints": list(op.constraints),
            }
            return
        if isinstance(op, ops.DeleteModel):
            self.tables.pop(op.table, None)
            return
        if isinstance(op, ops.RenameModel):
            if op.old_table in self.tables:
                self.tables[op.new_table] = self.tables.pop(op.old_table)
            return
        table = self.tables.get(getattr(op, "table", None))
        if table is None:
            return
        columns = table["columns"]
        if isinstance(op, ops.AddField):
            columns[op.name] = copy_column(op.column)
        elif isinstance(op, ops.RemoveField):
            columns.pop(op.name, None)
        elif isinstance(op, ops.RenameField) and op.old_name in columns:
            column = copy_column(columns.pop(op.old_name))
            column.name = column.key = op.new_name
            columns[op.new_name] = column
        elif isinstance(op, ops.AlterField) and op.name in columns:
            from sqlalchemy.schema import DefaultClause

            column = copy_column(columns[op.name])
            if op.column_type is not None:
                column.type = op.column_type
            if op.nullable is not None:
                column.nullable = op.nullable
            if op.server_default is ops.NO_DEFAULT:
                column.server_default = None
            elif op.server_default is not None:
                column.server_default = DefaultClause(op.server_default)
            columns[op.name] = column

    def column(self, table_name: str, column_name: str) -> Any:
        """A copy of the declared column, or ``None`` if the replay never saw it."""
        column = self.tables.get(table_name, {}).get("columns", {}).get(column_name)
        return copy_column(column) if column is not None else None

    def definition(self, table_name: str) -> dict[str, Any] | None:
        table = self.tables.get(table_name)
        if table is None:
            return None
        return {
            "columns": [copy_column(col) for col in table["columns"].values()],
            "primary_key": list(table["primary_key"]),
            "constraints": list(table["constraints"]),
        }


def _column_signature(column) -> tuple:
    """What a column *is*, minus its name — equal signatures suggest a rename."""
    from sqlalchemy.dialects import sqlite

    from zeeb_orm.migrations.operations import _scalar_default

    # Compared as SQLite renders them: the removed column may come from
    # reflection of the replayed state (TEXT) and the added one from a model
    # (Text), which are the same column.
    try:
        type_key = column.type.compile(dialect=sqlite.dialect())
    except Exception:
        type_key = repr(column.type)
    server_default = getattr(column.server_default, "arg", column.server_default)
    return (
        type_key,
        bool(column.nullable),
        bool(column.unique),
        bool(column.primary_key),
        _scalar_default(column),
        str(server_default) if server_default is not None else None,
        tuple(sorted((str(fk.target_fullname), fk.ondelete) for fk in column.foreign_keys)),
    )


def _find_renames(
    operations: list[Operation], report: ChangeReport, accept: bool
) -> list[Operation]:
    """Pair RemoveField/AddField on one table that look like a rename.

    A pair qualifies when, on the same table, exactly one removed column and
    exactly one added column share a definition (type, nullability,
    uniqueness, defaults, foreign keys). Anything more ambiguous is left
    alone. With ``accept`` the pair becomes a ``RenameField``, which keeps
    the column's data; otherwise it is only reported.
    """
    from zeeb_orm.migrations.operations import AddField, RemoveField, RenameField

    removed: dict[str, list[RemoveField]] = {}
    added: dict[str, list[AddField]] = {}
    for op in operations:
        if isinstance(op, RemoveField) and op.field is not None:
            removed.setdefault(op.table, []).append(op)
        elif isinstance(op, AddField):
            added.setdefault(op.table, []).append(op)

    pairs: dict[int, tuple[RemoveField, AddField]] = {}
    for table, removals in removed.items():
        additions = added.get(table, [])
        for removal in removals:
            signature = _column_signature(removal.field)
            matches = [a for a in additions if _column_signature(a.column) == signature]
            if len(matches) != 1:
                continue
            addition = matches[0]
            rivals = [
                r for r in removals if _column_signature(r.field) == signature
            ]
            if len(rivals) != 1:
                continue
            pairs[id(removal)] = (removal, addition)

    if not pairs:
        return operations

    renames = [
        PossibleRename(
            model_name=removal.model_name,
            table=removal.table,
            old_name=removal.name,
            new_name=addition.name,
        )
        for removal, addition in pairs.values()
    ]
    if not accept:
        report.possible_renames.extend(renames)
        return operations

    report.renamed.extend(renames)
    dropped_additions = {id(addition) for _removal, addition in pairs.values()}
    result: list[Operation] = []
    for op in operations:
        if id(op) in pairs:
            removal, addition = pairs[id(op)]
            result.append(
                RenameField(
                    model_name=removal.model_name,
                    table=removal.table,
                    old_name=removal.name,
                    new_name=addition.name,
                )
            )
        elif id(op) not in dropped_additions:
            result.append(op)
    return result


def _server_default_key(column) -> tuple | None:
    """What a column's server default renders as, comparable across sources."""
    default = column.server_default
    if default is None:
        return None
    arg = getattr(default, "arg", default)
    if isinstance(arg, str):
        return ("literal", arg)
    text = getattr(arg, "text", None)
    if text is not None:
        return ("sql", text.strip())
    try:
        from sqlalchemy.dialects import sqlite

        return ("sql", str(arg.compile(dialect=sqlite.dialect())))
    except Exception:
        return ("sql", str(arg))


def _server_default_arg(column) -> Any:
    """A column's server default as ``AlterField`` takes it (``NO_DEFAULT`` for none)."""
    return _default_clause_arg(column.server_default)


def _declared_column_changes(tables: list, state: _TableState, diffs: list) -> list[Operation]:
    """``AlterField`` for model columns that differ from their declaration.

    One operation per changed column, carrying every changed aspect (type,
    nullability, server default) with its previous value — so it is
    reversible — and the unchanged ones as Alembic ``existing_*`` arguments,
    which MySQL needs to restate a column without losing them.

    Types are compared by the DDL they produce on every supported dialect.
    """
    from zeeb_orm.migrations.serializer import type_ddl

    new_tables = {diff[1].name for diff in diffs if not isinstance(diff, list)
                  and diff[0] == "add_table"}
    operations: list[Operation] = []
    for table in tables:
        if table.name in new_tables or state.definition(table.name) is None:
            continue
        for column in table.columns:
            declared = state.column(table.name, column.name)
            if declared is None:
                continue  # added, or created outside the replayed operations
            changes: dict[str, Any] = {}
            existing: dict[str, Any] = {}
            if type_ddl(declared.type) != type_ddl(column.type):
                changes["column_type"] = column.type
                changes["old_column_type"] = declared.type
            else:
                existing["existing_type"] = declared.type
            if bool(declared.nullable) != bool(column.nullable):
                changes["nullable"] = bool(column.nullable)
                changes["old_nullable"] = bool(declared.nullable)
            else:
                existing["existing_nullable"] = bool(declared.nullable)
            if _server_default_key(declared) != _server_default_key(column):
                changes["server_default"] = _server_default_arg(column)
                changes["old_server_default"] = _server_default_arg(declared)
            elif declared.server_default is not None:
                existing["existing_server_default"] = _server_default_arg(declared)
            if not changes:
                continue
            operations.append(
                AlterField(
                    model_name=_table_name_to_model_name(table.name),
                    table=table.name,
                    name=column.name,
                    **changes,
                    **existing,
                )
            )
    return operations


_COLUMN_DIFFS = ("modify_type", "modify_nullable", "modify_default")


def _convert_diffs(diffs: list, state: _TableState | None = None) -> list[Operation]:
    """Convert Alembic diff tuples to Operation objects.

    Column changes (``modify_*``) on a column the replay declared are left to
    :func:`_declared_column_changes`, which compares the declaration instead.
    """
    operations: list[Operation] = []

    for diff in diffs:
        # Alembic wraps *column-level* diffs (``modify_type``,
        # ``modify_nullable``, ``modify_default``) in a list of tuples, while
        # *table-level* diffs (``add_table``, ``add_column``, ...) are bare
        # tuples. Flatten so each individual diff tuple reaches the dispatcher
        # — otherwise list-wrapped diffs never match and are silently dropped.
        inner_diffs = diff if isinstance(diff, list) else [diff]
        for inner in inner_diffs:
            if (
                inner[0] in _COLUMN_DIFFS
                and state is not None
                and state.column(inner[2], inner[3]) is not None
            ):
                continue
            op = _convert_single_diff(inner, state)
            if op is not None:
                if isinstance(op, list):
                    operations.extend(op)
                else:
                    operations.append(op)

    return operations


def _convert_single_diff(
    diff: tuple, state: _TableState | None = None
) -> Operation | list[Operation] | None:
    """Convert a single Alembic diff tuple to an Operation."""
    diff_type = diff[0]

    if diff_type == "add_table":
        from sqlalchemy import UniqueConstraint
        table = diff[1]
        model_name = _table_name_to_model_name(table.name)
        columns = [copy_column(col) for col in table.columns]
        pk_cols = [col.name for col in table.primary_key.columns]
        # Preserve table-level unique constraints so the written migration
        # round-trips (otherwise the constraint is re-detected forever). A
        # constraint from ``Column(unique=True)`` is unnamed, so requiring a name
        # dropped it silently — the column reached the database with no UNIQUE at
        # all and duplicates were accepted. Give those a deterministic name instead.
        constraints = [
            c if c.name else _named_unique_constraint(table.name, c)
            for c in table.constraints
            if isinstance(c, UniqueConstraint) and len(c.columns) > 0
        ]
        return CreateModel(
            name=model_name,
            table=table.name,
            columns=columns,
            primary_key=pk_cols,
            constraints=constraints or None,
        )

    elif diff_type == "remove_table":
        from sqlalchemy import UniqueConstraint

        table = diff[1]
        model_name = _table_name_to_model_name(table.name)
        # Record what is dropped so the operation can be reversed: the
        # migrations' own column definitions when the replay saw them,
        # otherwise the reflected ones.
        definition = state.definition(table.name) if state is not None else None
        if definition is None:
            definition = {
                "columns": [copy_column(col) for col in table.columns],
                "primary_key": [col.name for col in table.primary_key.columns],
                "constraints": [
                    c if c.name else _named_unique_constraint(table.name, c)
                    for c in table.constraints
                    if isinstance(c, UniqueConstraint) and len(c.columns) > 0
                ],
            }
        return DeleteModel(name=model_name, table=table.name, **definition)

    elif diff_type == "add_column":
        table_name, column = diff[2], diff[3]
        model_name = _table_name_to_model_name(table_name)
        return AddField(
            model_name=model_name,
            table=table_name,
            name=column.name,
            column=copy_column(column),
        )

    elif diff_type == "remove_column":
        table_name, column = diff[2], diff[3]
        model_name = _table_name_to_model_name(table_name)
        # Keep a copy of the dropped column so the operation is reversible —
        # as the migrations declared it when the replay saw it (reflection
        # from SQLite loses the declared type).
        declared = state.column(table_name, column.name) if state is not None else None
        return RemoveField(
            model_name=model_name,
            table=table_name,
            name=column.name,
            field=declared if declared is not None else copy_column(column),
        )

    elif diff_type == "modify_type":
        table_name, col_name = diff[2], diff[3]
        old_type, new_type = diff[5], diff[6]
        model_name = _table_name_to_model_name(table_name)
        return AlterField(
            model_name=model_name,
            table=table_name,
            name=col_name,
            column_type=new_type,
            old_column_type=old_type,
        )

    elif diff_type == "modify_nullable":
        table_name, col_name = diff[2], diff[3]
        old_nullable, new_nullable = diff[5], diff[6]
        model_name = _table_name_to_model_name(table_name)
        return AlterField(
            model_name=model_name,
            table=table_name,
            name=col_name,
            nullable=new_nullable,
            old_nullable=old_nullable,
        )

    elif diff_type == "modify_default":
        # Only for a column the replay did not declare: the old default is
        # reflected, so it is SQL text (``'0'`` with its quotes) — passed on
        # as such, not as a string that would be quoted a second time.
        table_name, col_name = diff[2], diff[3]
        old_default, new_default = diff[5], diff[6]
        model_name = _table_name_to_model_name(table_name)
        return AlterField(
            model_name=model_name,
            table=table_name,
            name=col_name,
            server_default=_default_clause_arg(new_default),
            old_server_default=_default_clause_arg(old_default),
        )

    elif diff_type in ("add_constraint", "remove_constraint"):
        from sqlalchemy import UniqueConstraint
        constraint = diff[1]
        # Only *named unique* constraints are auto-migrated; unnamed and
        # non-unique (check/fk) constraints are silently skipped — on SQLite
        # they surface as perpetual reflection noise and cannot be ALTERed in
        # place anyway, so a manual migration is required.
        if not isinstance(constraint, UniqueConstraint) or not constraint.name:
            return None
        table = constraint.table
        table_name = table.name if table is not None else ""
        model_name = _table_name_to_model_name(table_name)
        if diff_type == "add_constraint":
            return AddConstraint(
                model_name=model_name, table=table_name, constraint=constraint,
            )
        return RemoveConstraint(
            model_name=model_name, table=table_name,
            name=str(constraint.name), constraint_type="unique",
        )

    elif diff_type == "add_index":
        index = diff[1]
        table_name = index.table.name if index.table is not None else ""
        model_name = _table_name_to_model_name(table_name)
        return AddIndex(
            model_name=model_name,
            table=table_name,
            name=index.name,
            columns=[col.name for col in index.columns],
            unique=index.unique,
        )

    elif diff_type == "remove_index":
        index = diff[1]
        table_name = index.table.name if index.table is not None else ""
        model_name = _table_name_to_model_name(table_name)
        return RemoveIndex(
            model_name=model_name,
            table=table_name,
            name=index.name,
            columns=[col.name for col in index.columns],
            unique=bool(index.unique),
        )

    return None
