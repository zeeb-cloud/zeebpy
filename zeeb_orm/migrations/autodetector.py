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
    AlterModelTable,
    CreateModel,
    DeleteModel,
    Operation,
    RemoveConstraint,
    RemoveField,
    RemoveIndex,
    copy_column,
)
from zeeb_orm.migrations.state import MigrationError


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


def _models_for_table(table_name: str) -> list[type]:
    """The registered model classes whose table is *table_name*."""
    from zeeb_orm.models.base import _model_registry

    return [
        cls
        for cls in list(_model_registry.values())
        if getattr(getattr(cls, "_meta", None), "table_name", None) == table_name
    ]


def _table_name_to_model_name(table_name: str, state: _TableState | None = None) -> str:
    """The model name for a table: the registered model's, else the migrations'.

    A table no model declares any more (one being dropped) is named as the
    migration that created it named it; only a table neither knows is guessed
    from its name (``'blog_posts'`` -> ``'BlogPosts'``).
    """
    # The registry is keyed "app.Name"; migration files carry the bare class name.
    models = _models_for_table(table_name)
    if models:
        return models[0].__name__
    declared = state.model_name(table_name) if state is not None else None
    if declared:
        return declared
    parts = table_name.replace("-", "_").split("_")
    return "".join(p.capitalize() for p in parts)


def _model_field(table_name: str, column_name: str) -> Any:
    """The model field stored in ``table_name.column_name``, or ``None``."""
    for cls in _models_for_table(table_name):
        for model_field in getattr(getattr(cls, "_meta", None), "local_fields", ()):
            if getattr(model_field, "db_column", None) == column_name:
                return model_field
    return None


def _backfill_for(table_name: str, column) -> Any:
    """The SQL that fills existing rows when a NOT NULL column is added, or ``None``.

    An ``auto_now``/``auto_now_add`` field has no default — the ORM stamps it
    on save — but "now" is the obvious value for the rows already there.
    """
    import sqlalchemy as sa

    if column.nullable or column.server_default is not None:
        return None
    model_field = _model_field(table_name, column.name)
    if not (
        getattr(model_field, "auto_now", False) or getattr(model_field, "auto_now_add", False)
    ):
        return None
    if isinstance(column.type, sa.DateTime):
        return sa.func.current_timestamp()
    if isinstance(column.type, sa.Date):
        return sa.func.current_date()
    if isinstance(column.type, sa.Time):
        return sa.func.current_time()
    return None


class NotNullFieldError(MigrationError):
    """A new NOT NULL field has nothing to fill the rows already in its table.

    Raised by ``makemigrations`` before anything is written: the migration
    could never be applied to a table with rows (and on SQLite not at all).

    Attributes:
        fields: ``"<Model>.<column>"`` for each such field.
    """

    def __init__(self, message: str, *, fields: list[str]) -> None:
        super().__init__(message)
        self.fields = list(fields)


def check_not_null_additions(
    operations: list[Operation], report: ChangeReport | None = None
) -> None:
    """Refuse ``AddField`` operations whose NOT NULL column cannot be backfilled.

    A field that is one half of a likely rename (in *report*) is named with
    the ``accept_renames`` way out: as a ``RenameField`` it keeps its data.

    Raises:
        NotNullFieldError: naming every such field.
    """
    renames = {
        (rename.table, rename.new_name): rename.old_name
        for rename in (report.possible_renames if report is not None else [])
    }
    unfillable = []
    hints = []
    for op in operations:
        if not isinstance(op, AddField):
            continue
        try:
            op.backfill_clause()
        except MigrationError:
            unfillable.append(f"{op.model_name}.{op.name}")
            old = renames.get((op.table, op.name))
            if old is not None:
                hints.append(
                    f" If {op.model_name}.{op.name} is {op.model_name}.{old} renamed, "
                    f"re-run makemigrations with --accept-renames."
                )
    if unfillable:
        raise NotNullFieldError(
            f"Cannot add the NOT NULL field(s) {', '.join(unfillable)} to existing "
            f"table(s): rows already there would have no value. Give each field "
            f"null=True or a static default (a callable default cannot fill "
            f"existing rows), then re-run makemigrations.{''.join(hints)}",
            fields=unfillable,
        )


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
        latest_migration: The migration a new one depends on — the last one
            a fresh database applies — or ``None`` when there are none.
    """

    possible_renames: list[PossibleRename] = field(default_factory=list)
    renamed: list[PossibleRename] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    latest_migration: str | None = None

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
    report.latest_migration = graph.order[-1] if graph.order else None
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

        def compare() -> list:
            with mem_engine.connect() as conn:
                migration_ctx = MigrationContext.configure(
                    conn,
                    opts={"compare_server_default": True, "include_object": include_object},
                )
                return compare_metadata(migration_ctx, metadata)

        diffs = compare()
        # A model that kept its name but changed Meta.table_name diffs as a
        # dropped and a created table. Rename it instead (dropping loses its
        # rows), then compare again for the changes made to it besides.
        table_renames = _table_renames(diffs, state)
        if table_renames:
            with mem_engine.connect() as conn:
                for op in table_renames:
                    op.forward(conn)
                    state.apply(op)
                conn.commit()
            diffs = compare()
    finally:
        mem_engine.dispose()

    tables = [
        table
        for name, table in sorted(metadata.tables.items())
        if name not in unmanaged
    ]
    operations: list[Operation] = list(table_renames)
    operations += _convert_diffs(diffs, state)
    operations += _declared_column_changes(tables, state, diffs)
    operations = _find_renames(operations, report, accept_renames)
    operations = order_operations(operations)
    return operations, report


def _table_renames(diffs: list, state: _TableState) -> list[Operation]:
    """``AlterModelTable`` for each model whose table name alone changed.

    A dropped table is the old table of an added one when the migrations
    name its model exactly like the model that declares the added table, and
    no other dropped or added table claims that model name.
    """
    added = [d[1].name for d in diffs if not isinstance(d, list) and d[0] == "add_table"]
    removed = [d[1].name for d in diffs if not isinstance(d, list) and d[0] == "remove_table"]
    added_by_model: dict[str, list[str]] = {}
    for table_name in added:
        models = {cls.__name__ for cls in _models_for_table(table_name)}
        if len(models) == 1:
            added_by_model.setdefault(models.pop(), []).append(table_name)
    removed_by_model: dict[str, list[str]] = {}
    for table_name in removed:
        model = state.model_name(table_name)
        if model:
            removed_by_model.setdefault(model, []).append(table_name)

    renames: list[Operation] = []
    for model, old_tables in sorted(removed_by_model.items()):
        new_tables = added_by_model.get(model, [])
        if len(old_tables) == 1 and len(new_tables) == 1:
            renames.append(
                AlterModelTable(name=model, old_table=old_tables[0], new_table=new_tables[0])
            )
    return renames


def _referenced_tables(op: Operation) -> set[str] | None:
    """The tables *op*'s foreign keys point at (``None``: unknowable)."""
    from zeeb_orm.migrations.optimizer import _tables

    tables = _tables(op)
    if tables is None:
        return None
    return tables - {getattr(op, "table", None)}


def order_operations(operations: list[Operation]) -> list[Operation]:
    """Order detected operations so each one's dependencies run first.

    Alembic lists dropped tables before the column changes of the tables that
    remain, so a model deleted together with the foreign key pointing at it
    came out as ``DeleteModel`` then ``RemoveField`` — on PostgreSQL the table
    cannot be dropped while the key exists, and a rollback re-added the key
    before its table. Here:

    - an operation dropping or changing a reference to a table runs before
      that table's ``DeleteModel`` (and so a referencing table is dropped
      before the one it references);
    - a table's ``CreateModel`` runs before anything that adds a reference
      to it or works on the new table;
    - ``AlterModelTable`` runs before anything that uses the new table name.

    Rollback runs a migration's operations in reverse, so it gets the reverse
    order for free. Otherwise the detected order is kept; on a cycle (two
    dropped tables referencing each other) the remaining operations keep it.
    """
    import heapq

    from zeeb_orm.migrations import operations as ops

    count = len(operations)
    after: list[set[int]] = [set() for _ in range(count)]  # i -> those after i
    for i, op in enumerate(operations):
        if isinstance(op, ops.DeleteModel):
            first, then, table = None, i, op.table
        elif isinstance(op, ops.CreateModel):
            first, then, table = i, None, op.table
        elif isinstance(op, ops.AlterModelTable):
            first, then, table = i, None, op.new_table
        else:
            continue
        for j, other in enumerate(operations):
            if j == i:
                continue
            refs = _referenced_tables(other)
            touches = getattr(other, "table", None) == table or bool(refs and table in refs)
            if not touches:
                continue
            if then is not None and not isinstance(other, (ops.CreateModel, ops.AddField)):
                after[j].add(then)
            if first is not None:
                after[first].add(j)

    indegree = [0] * count
    for targets in after:
        for j in targets:
            indegree[j] += 1
    ready = [i for i in range(count) if indegree[i] == 0]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        i = heapq.heappop(ready)
        order.append(i)
        for j in after[i]:
            indegree[j] -= 1
            if indegree[j] == 0:
                heapq.heappush(ready, j)
    if len(order) < count:  # a cycle: keep the detected order for the rest
        placed = set(order)
        order += [i for i in range(count) if i not in placed]
    return [operations[i] for i in order]


class _TableState:
    """Table definitions as the replayed migration files declare them.

    Reflection from the in-memory SQLite database loses the declared types
    (a ``Uuid`` reads back as ``CHAR(32)``), so a ``DeleteModel`` records
    the columns from here — the definitions the migrations themselves used —
    and a rollback recreates the table with them on any backend.
    """

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, Any]] = {}

    def model_name(self, table_name: str) -> str | None:
        """The model name the migrations gave *table_name*, if they created it."""
        return self.tables.get(table_name, {}).get("model")

    def _rename_table(self, old: str, new: str, model: str | None) -> None:
        if old not in self.tables:
            return
        self.tables[new] = self.tables.pop(old)
        if model:
            self.tables[new]["model"] = model
        # Foreign keys follow the renamed table (as the database's do).
        for table in self.tables.values():
            for name, column in list(table["columns"].items()):
                targets = [str(fk.target_fullname) for fk in column.foreign_keys]
                if any(t.rpartition(".")[0] == old for t in targets):
                    table["columns"][name] = _retarget(column, old, new)

    def apply(self, op: Operation) -> None:
        from zeeb_orm.migrations import operations as ops
        from zeeb_orm.migrations.serializer import constraint_columns

        if isinstance(op, ops.CreateModel):
            columns = {col.name: copy_column(col) for col in op.columns}
            for name in op.primary_key:
                # A primary key column is NOT NULL whatever the Column says.
                if name in columns:
                    columns[name].nullable = False
            self.tables[op.table] = {
                "model": op.name,
                "columns": columns,
                "primary_key": list(op.primary_key),
                "constraints": list(op.constraints),
            }
            return
        if isinstance(op, ops.DeleteModel):
            self.tables.pop(op.table, None)
            return
        if isinstance(op, ops.RenameModel):
            self._rename_table(op.old_table, op.new_table, op.new_name)
            return
        if isinstance(op, ops.AlterModelTable):
            self._rename_table(op.old_table, op.new_table, op.name)
            return
        table = self.tables.get(getattr(op, "table", None))
        if table is None:
            return
        columns = table["columns"]
        if isinstance(op, ops.AddField):
            columns[op.name] = copy_column(op.column)
        elif isinstance(op, ops.RemoveField):
            columns.pop(op.name, None)
            # A constraint over a dropped column goes with it.
            table["constraints"] = [
                c for c in table["constraints"] if op.name not in constraint_columns(c)
            ]
        elif isinstance(op, ops.AddConstraint):
            table["constraints"].append(op.constraint)
        elif isinstance(op, ops.RemoveConstraint):
            table["constraints"] = [
                c for c in table["constraints"] if getattr(c, "name", None) != op.name
            ]
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

    def constraint(self, table_name: str, name: str) -> Any:
        """The declared constraint called *name* on *table_name*, or ``None``."""
        for constraint in self.tables.get(table_name, {}).get("constraints", ()):
            if getattr(constraint, "name", None) == name:
                return constraint
        return None

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


def _retarget(column, old_table: str, new_table: str):
    """Copy *column* with its foreign keys to *old_table* pointing at *new_table*."""
    from sqlalchemy import Column, ForeignKey

    foreign_keys = []
    for fk in column.foreign_keys:
        target_table, _, target_column = str(fk.target_fullname).rpartition(".")
        if target_table == old_table:
            target_table = new_table
        foreign_keys.append(
            ForeignKey(
                f"{target_table}.{target_column}", ondelete=fk.ondelete, onupdate=fk.onupdate
            )
        )
    return Column(
        column.name,
        column.type,
        *foreign_keys,
        nullable=column.nullable,
        unique=column.unique,
        index=column.index,
        primary_key=column.primary_key,
        default=column.default,
        server_default=column.server_default,
        autoincrement=column.autoincrement,
    )


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
                    model_name=_table_name_to_model_name(table.name, state),
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
        model_name = _table_name_to_model_name(table.name, state)
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
        model_name = _table_name_to_model_name(table.name, state)
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
        model_name = _table_name_to_model_name(table_name, state)
        return AddField(
            model_name=model_name,
            table=table_name,
            name=column.name,
            column=copy_column(column),
            backfill=_backfill_for(table_name, column),
        )

    elif diff_type == "remove_column":
        table_name, column = diff[2], diff[3]
        model_name = _table_name_to_model_name(table_name, state)
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
        model_name = _table_name_to_model_name(table_name, state)
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
        model_name = _table_name_to_model_name(table_name, state)
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
        model_name = _table_name_to_model_name(table_name, state)
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
        model_name = _table_name_to_model_name(table_name, state)
        if diff_type == "add_constraint":
            return AddConstraint(
                model_name=model_name, table=table_name, constraint=constraint,
            )
        # Record the constraint so a rollback can recreate it: as the
        # migrations declared it when the replay saw it, else as reflected.
        from zeeb_orm.migrations.serializer import constraint_columns

        declared = (
            state.constraint(table_name, str(constraint.name)) if state is not None else None
        )
        source = declared if declared is not None else constraint
        return RemoveConstraint(
            model_name=model_name, table=table_name,
            name=str(constraint.name), constraint_type="unique",
            constraint=UniqueConstraint(
                *constraint_columns(source), name=str(constraint.name)
            ),
        )

    elif diff_type == "add_index":
        index = diff[1]
        table_name = index.table.name if index.table is not None else ""
        model_name = _table_name_to_model_name(table_name, state)
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
        model_name = _table_name_to_model_name(table_name, state)
        return RemoveIndex(
            model_name=model_name,
            table=table_name,
            name=index.name,
            columns=[col.name for col in index.columns],
            unique=bool(index.unique),
        )

    return None
