"""
Django-style migration operations.

Each operation represents a single schema change (create table, add column, etc.)
and knows how to apply itself forward and backward, and how to describe itself
for a migration file (:meth:`Operation.deconstruct`, rendered by
:mod:`zeeb_orm.migrations.serializer`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


class _NoDefault:
    """The ``server_default`` value meaning "the column has no server default"."""

    _instance: _NoDefault | None = None

    def __new__(cls) -> _NoDefault:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "operations.NO_DEFAULT"

    def __reduce__(self) -> str:
        return "NO_DEFAULT"


#: Pass as ``AlterField(server_default=...)`` / ``old_server_default`` to say the
#: column has *no* server default (``None`` there means "leave it unchanged").
NO_DEFAULT = _NoDefault()


def _quote(connection, identifier: str) -> str:
    """Quote a SQL identifier (table/column name) for the active dialect."""
    return connection.dialect.identifier_preparer.quote(identifier)


def _default_clause(column):
    """A ``DefaultClause`` rendering the column's default, or ``None`` if it has none.

    Strings are passed through as-is so SQLAlchemy renders (and escapes) them as
    quoted literals; other scalars are emitted as raw SQL text, which keeps
    booleans and numbers correct across SQLite and Postgres.
    """
    from sqlalchemy import DefaultClause, text

    value = _scalar_default(column)
    if value is None:
        return None
    if isinstance(value, str):
        return DefaultClause(value)
    if isinstance(value, bool):
        return DefaultClause(text("true" if value else "false"))
    return DefaultClause(text(str(value)))


def copy_column(column):
    """Copy a Column, preserving its foreign keys.

    ``Column.copy()`` / ``._copy()`` silently drop ``ForeignKey`` constructs once the
    column is detached from its Table (their target resolution is table-bound), so
    copying a column on the way into a migration operation strips the relation —
    tables then get plain columns with no REFERENCES clause and no referential
    integrity at all. Re-attach the foreign keys from their ``target_fullname``,
    which is a plain ``"table.column"`` string and needs no table binding.
    """
    from sqlalchemy import Column, ForeignKey

    foreign_keys = [
        ForeignKey(fk.target_fullname, ondelete=fk.ondelete, onupdate=fk.onupdate)
        for fk in column.foreign_keys
    ]
    copied = column._copy() if hasattr(column, "_copy") else column.copy()
    if not foreign_keys:
        return copied
    return Column(
        copied.name,
        copied.type,
        *foreign_keys,
        nullable=copied.nullable,
        unique=copied.unique,
        index=copied.index,
        primary_key=copied.primary_key,
        default=copied.default,
        server_default=copied.server_default,
        autoincrement=copied.autoincrement,
    )


def _register_fk_target_stubs(metadata, table_name: str, columns) -> None:
    """Add minimal stand-ins for tables this table's foreign keys point at.

    A migration operation builds its table in a throwaway ``MetaData`` holding only
    itself, so a ``ForeignKey("other.id")`` has nothing to resolve against and
    SQLAlchemy raises ``NoReferencedTableError``. Registering a one-column stub for
    each referenced table makes the reference resolvable; the stubs are never
    created (only the real table's ``.create()`` is called), and the referenced
    tables already exist in the database because the compiler orders relation
    targets first.
    """
    from sqlalchemy import Column, Table

    for column in columns:
        for fk in getattr(column, "foreign_keys", ()):
            target = str(fk.target_fullname)
            target_table, _, target_column = target.rpartition(".")
            # A self-reference resolves against the table being built.
            if not target_table or target_table == table_name or target_table in metadata.tables:
                continue
            Table(target_table, metadata, Column(target_column, column.type))


def _with_server_default(column, server_default):
    """Copy ``column`` with ``server_default`` attached.

    A copy, because the original belongs to the migration file's operation object
    and may be reused (e.g. by ``backward``); mutating it would leak the one-off
    backfill default into later use.
    """
    copied = copy_column(column)
    copied.server_default = server_default
    return copied


def _alembic_ops(connection):
    """Alembic's ``Operations`` bound to *connection*."""
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    return Operations(MigrationContext.configure(connection))


def _sqlite_add_needs_rebuild(column) -> bool:
    """Whether SQLite's ``ALTER TABLE ADD COLUMN`` cannot add *column* as declared.

    SQLite adds a plain column in place, but not one carrying a constraint
    Alembic emits separately (a foreign key, ``UNIQUE``), one with a
    non-constant default (``CURRENT_TIMESTAMP``), or a ``NOT NULL`` column
    without a default; those need the table rebuilt (Alembic batch mode).
    """
    from sqlalchemy.sql.elements import ClauseElement

    if column.foreign_keys or column.unique:
        return True
    default = column.server_default
    if default is None:
        return not column.nullable
    return isinstance(getattr(default, "arg", None), ClauseElement)


def _add_column(connection, table: str, column) -> None:
    """Add *column* to *table*, rebuilding the table on SQLite where it must.

    Elsewhere Alembic's ``add_column`` adds the column and then its foreign
    key with ``ALTER TABLE ... ADD CONSTRAINT``, which SQLite does not have —
    there the table is copied into a new one that declares the column (as
    ``AlterField`` does). Batch mode only takes named constraints, so the
    foreign key gets one there.

    A ``unique=True`` column gets the constraint ``CreateModel`` would have
    given it, ``uq_<table>_<column>``, on every database — so a later
    ``RemoveConstraint`` finds it by that name everywhere.
    """
    from sqlalchemy import Column, ForeignKey

    op = _alembic_ops(connection)
    unique_name = f"uq_{table}_{column.name}" if column.unique else None
    sqlite = connection.dialect.name == "sqlite"
    if unique_name or (sqlite and column.foreign_keys):
        foreign_keys = []
        for fk in column.foreign_keys:
            target = str(fk.target_fullname)
            name = fk.name
            if sqlite and not name:
                name = f"fk_{table}_{column.name}_{target.rpartition('.')[0]}"
            foreign_keys.append(
                ForeignKey(target, ondelete=fk.ondelete, onupdate=fk.onupdate, name=name)
            )
        copied = column._copy()
        column = Column(
            copied.name,
            copied.type,
            *foreign_keys,
            nullable=copied.nullable,
            default=copied.default,
            server_default=copied.server_default,
            comment=copied.comment,
        )
    if sqlite and (_sqlite_add_needs_rebuild(column) or unique_name):
        with op.batch_alter_table(table, recreate="always") as batch_op:
            batch_op.add_column(column)
            if unique_name:
                batch_op.create_unique_constraint(unique_name, [column.name])
        return
    op.add_column(table, column)
    if unique_name:
        op.create_unique_constraint(unique_name, table, [column.name])


def _drop_column(connection, table: str, name: str) -> None:
    """Drop column *name* from *table*, rebuilding the table on SQLite.

    SQLite's ``DROP COLUMN`` refuses a column that a foreign key, a unique
    constraint, an index or the primary key uses; batch mode copies the
    table without it (and without the constraints and indexes on it).
    """
    op = _alembic_ops(connection)
    if connection.dialect.name == "sqlite":
        with op.batch_alter_table(table, recreate="always") as batch_op:
            batch_op.drop_column(name)
        return
    op.drop_column(table, name)


def _drop_server_default(connection, table: str, column) -> None:
    """Drop the server default of *column* (batch mode on SQLite)."""
    op = _alembic_ops(connection)
    kwargs = {
        "server_default": None,
        "existing_type": column.type,
        "existing_nullable": bool(column.nullable),
    }
    if connection.dialect.name == "sqlite":
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(column.name, **kwargs)
        return
    op.alter_column(table, column.name, **kwargs)


def _irreversible(op: Operation, why: str):
    from zeeb_orm.migrations.state import IrreversibleError

    return IrreversibleError(f"Operation {op.describe()!r} cannot be reversed: {why}")


def _create_table(connection, table_name: str, columns, primary_key, constraints) -> None:
    """``CREATE TABLE`` from recorded columns (shared by CreateModel/DeleteModel)."""
    from sqlalchemy import MetaData, PrimaryKeyConstraint, Table

    tmp_meta = MetaData()
    cols = [copy_column(col) for col in columns]
    items = list(cols)
    if primary_key:
        items.append(PrimaryKeyConstraint(*primary_key))
    for constraint in constraints or ():
        items.append(constraint)
    _register_fk_target_stubs(tmp_meta, table_name, cols)
    Table(table_name, tmp_meta, *items).create(connection, checkfirst=True)


class Operation:
    """Base class for migration operations.

    Subclasses must implement ``describe``, ``forward`` and ``backward``.
    An operation whose ``reversible`` is false raises
    :class:`~zeeb_orm.migrations.state.IrreversibleError` from ``backward``
    rather than silently doing nothing; the executor checks the whole plan
    first, so a rollback never stops half-way.
    """

    reversible = True

    def describe(self) -> str:
        """Human-readable one-line summary, used in migration output and errors."""
        raise NotImplementedError

    def forward(self, connection) -> None:
        """Execute forward migration."""
        raise NotImplementedError

    def backward(self, connection) -> None:
        """Execute backward migration."""
        raise NotImplementedError

    def deconstruct(self) -> dict[str, Any]:
        """The keyword arguments that rebuild this operation, defaults left out.

        This is what a migration file records; every argument that changes what
        the operation does must be in it.
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        from zeeb_orm.migrations.serializer import SourceWriter

        try:
            return SourceWriter(copy_functions=False).operation(self)
        except Exception:
            try:
                return f"<{type(self).__name__}: {self.describe()}>"
            except Exception:
                return f"<{type(self).__name__}>"


class CreateModel(Operation):
    """
    Create a new database table.

    Usage in migration file::

        operations.CreateModel(
            name='Post',
            table='posts',
            columns=[
                sa.Column('id', sa.Uuid(), nullable=False),
                sa.Column('title', sa.String(200), nullable=False),
            ],
            primary_key=['id'],
        )
    """

    def __init__(
        self,
        name: str,
        table: str,
        columns: list,
        primary_key: list[str] | None = None,
        constraints: list | None = None,
    ):
        self.name = name
        self.table = table
        self.columns = columns
        self.primary_key = primary_key or []
        self.constraints = constraints or []

    def describe(self) -> str:
        return f"Create model {self.name}"

    def forward(self, connection) -> None:
        from sqlalchemy import MetaData, PrimaryKeyConstraint, Table
        tmp_meta = MetaData()
        cols = list(self.columns)
        if self.primary_key:
            cols.append(PrimaryKeyConstraint(*self.primary_key))
        for c in self.constraints:
            cols.append(c)
        _register_fk_target_stubs(tmp_meta, self.table, self.columns)
        table = Table(self.table, tmp_meta, *cols)
        table.create(connection, checkfirst=True)

    def backward(self, connection) -> None:
        from sqlalchemy import text
        connection.execute(text(f"DROP TABLE IF EXISTS {_quote(connection, self.table)}"))

    def deconstruct(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "name": self.name,
            "table": self.table,
            "columns": list(self.columns),
        }
        if self.primary_key:
            kwargs["primary_key"] = list(self.primary_key)
        if self.constraints:
            kwargs["constraints"] = list(self.constraints)
        return kwargs


class DeleteModel(Operation):
    """Drop a database table.

    Pass the dropped table's ``columns`` (and ``primary_key``/``constraints``)
    to make it reversible — ``makemigrations`` records them. Without them,
    unapplying raises :class:`~zeeb_orm.migrations.state.IrreversibleError`.
    Reversing recreates the table's schema; its rows are gone.
    """

    def __init__(
        self,
        name: str,
        table: str,
        columns: list | None = None,
        primary_key: list[str] | None = None,
        constraints: list | None = None,
    ):
        self.name = name
        self.table = table
        self.columns = list(columns or [])
        self.primary_key = list(primary_key or [])
        self.constraints = list(constraints or [])

    @property
    def reversible(self):
        """Whether ``backward`` can undo this operation."""
        return bool(self.columns)

    def describe(self) -> str:
        return f"Delete model {self.name}"

    def forward(self, connection) -> None:
        from sqlalchemy import text
        connection.execute(text(f"DROP TABLE IF EXISTS {_quote(connection, self.table)}"))

    def backward(self, connection) -> None:
        if not self.columns:
            raise _irreversible(
                self, "the migration does not record the dropped table's columns"
            )
        _create_table(
            connection, self.table, self.columns, self.primary_key, self.constraints
        )

    def deconstruct(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"name": self.name, "table": self.table}
        if self.columns:
            kwargs["columns"] = list(self.columns)
        if self.primary_key:
            kwargs["primary_key"] = list(self.primary_key)
        if self.constraints:
            kwargs["constraints"] = list(self.constraints)
        return kwargs


class AddField(Operation):
    """Add a column to an existing table.

    A ``NOT NULL`` column needs a value for the rows already there. It comes
    from ``backfill`` — a SQL expression such as ``sa.func.current_timestamp()``,
    which ``makemigrations`` writes for ``auto_now``/``auto_now_add`` fields —
    or else from the column's scalar ``default``. Either is used only while the
    column is added; the column itself keeps no server default.
    """

    def __init__(
        self, model_name: str, table: str, name: str, column: Any, backfill: Any = None
    ):
        self.model_name = model_name
        self.table = table
        self.name = name
        self.column = column
        self.backfill = backfill

    def describe(self) -> str:
        return f"Add field {self.name} to {self.model_name}"

    def backfill_clause(self):
        """The ``DefaultClause`` that fills existing rows, or ``None`` if none is needed.

        Raises:
            MigrationError: the column is ``NOT NULL`` and nothing can fill it.
        """
        column = self.column
        if column.nullable or column.server_default is not None:
            return None
        if self.backfill is not None:
            from sqlalchemy import DefaultClause

            return DefaultClause(self.backfill)
        backfill = _default_clause(column)
        if backfill is None:
            from zeeb_orm.migrations.state import MigrationError

            raise MigrationError(
                f"Cannot add NOT NULL column '{self.table}.{self.name}' with no default: "
                f"existing rows have no value to fall back on. Give the field a "
                f"default, or make it nullable."
            )
        return backfill

    def forward(self, connection) -> None:
        # A NOT NULL column cannot simply be appended to a table that already has
        # rows — Postgres rejects it with NotNullViolation and the migration then
        # fails on every retry, wedging the project. Emit the backfill as a SQL
        # DEFAULT so the database fills existing rows in the same ALTER.
        column = self.column
        backfill = self.backfill_clause()
        if backfill is not None:
            column = _with_server_default(column, backfill)
        _add_column(connection, self.table, column)
        # The backfill DEFAULT is a means of adding the column, not part of the
        # schema the migration declares: drop it again, so the database matches
        # the migration files (and change detection, which compares the models
        # against those files, sees no phantom server default). On SQLite
        # dropping a default rebuilds the table, so a constant default stays
        # there, unused by the ORM, which always writes the column; a SQL
        # expression (``CURRENT_TIMESTAMP``) is dropped there too.
        if backfill is None:
            return
        if connection.dialect.name != "sqlite" or self.backfill is not None:
            _drop_server_default(connection, self.table, column)

    def backward(self, connection) -> None:
        _drop_column(connection, self.table, self.name)

    def deconstruct(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model_name": self.model_name,
            "table": self.table,
            "name": self.name,
            "column": self.column,
        }
        if self.backfill is not None:
            kwargs["backfill"] = self.backfill
        return kwargs


class RemoveField(Operation):
    """Remove a column from an existing table.

    Pass ``field`` (a ``sqlalchemy.Column``) to enable reversal.
    """

    def __init__(self, model_name: str, table: str, name: str, field: Any = None):
        self.model_name = model_name
        self.table = table
        self.name = name
        self.field = field  # Optional: stored column for reversal

    @property
    def reversible(self):
        """Whether ``backward`` can undo this operation."""
        return self.field is not None

    def describe(self) -> str:
        return f"Remove field {self.name} from {self.model_name}"

    def forward(self, connection) -> None:
        _drop_column(connection, self.table, self.name)

    def backward(self, connection) -> None:
        if self.field is None:
            raise _irreversible(self, "the migration does not record the removed column")
        column = self.field
        if (
            not column.nullable
            and column.server_default is None
            and _scalar_default(column) is not None
        ):
            # Re-adding a NOT NULL column backfills the rows from its default.
            AddField(self.model_name, self.table, self.name, column).forward(connection)
            return
        # Otherwise only an empty table can take the column back NOT NULL.
        _add_column(connection, self.table, column)

    def deconstruct(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model_name": self.model_name,
            "table": self.table,
            "name": self.name,
        }
        if self.field is not None:
            kwargs["field"] = self.field
        return kwargs


class AlterField(Operation):
    """Alter a column's type, nullable, server default, etc.

    ``None`` means "leave unchanged" for every argument. To drop a server
    default, pass ``server_default=NO_DEFAULT``; for a column that had none,
    ``old_server_default=NO_DEFAULT``. Pass the ``old_*`` values to enable
    reversal. Further keyword arguments (``existing_nullable=...``) are handed
    to Alembic's ``alter_column`` as they are.
    """

    def __init__(
        self,
        model_name: str,
        table: str,
        name: str,
        column_type: Any = None,
        nullable: bool | None = None,
        old_column_type: Any = None,
        old_nullable: bool | None = None,
        server_default: Any = None,
        old_server_default: Any = None,
        **kwargs,
    ):
        self.model_name = model_name
        self.table = table
        self.name = name
        self.column_type = column_type
        self.nullable = nullable
        self.old_column_type = old_column_type
        self.old_nullable = old_nullable
        self.server_default = server_default
        self.old_server_default = old_server_default
        self.kwargs = kwargs

    @property
    def reversible(self):
        """Whether ``backward`` can undo this operation."""
        return (
            self.old_column_type is not None
            or self.old_nullable is not None
            or self.old_server_default is not None
        )

    def describe(self) -> str:
        return f"Alter field {self.name} on {self.model_name}"

    def _apply_alter(self, connection, alter_kwargs: dict[str, Any]) -> None:
        """Run ``alter_column`` with the given kwargs, using batch mode on SQLite.

        Plain SQLite has no ``ALTER COLUMN``; Alembic's ``batch_alter_table``
        emulates it via copy-and-swap. Other dialects alter in place.
        """
        if not alter_kwargs:
            return
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        ctx = MigrationContext.configure(connection)
        op = Operations(ctx)
        if connection.dialect.name == "sqlite":
            with op.batch_alter_table(self.table) as batch_op:
                batch_op.alter_column(self.name, **alter_kwargs)
        else:
            op.alter_column(self.table, self.name, **alter_kwargs)

    def forward(self, connection) -> None:
        alter_kwargs: dict[str, Any] = {}
        if self.column_type is not None:
            alter_kwargs["type_"] = self.column_type
        if self.nullable is not None:
            alter_kwargs["nullable"] = self.nullable
        if self.server_default is not None:
            alter_kwargs["server_default"] = _alembic_server_default(self.server_default)
        alter_kwargs.update(self.kwargs)
        self._apply_alter(connection, alter_kwargs)

    def backward(self, connection) -> None:
        if not self.reversible:
            raise _irreversible(self, "the migration does not record the previous definition")
        alter_kwargs: dict[str, Any] = {}
        if self.old_column_type is not None:
            alter_kwargs["type_"] = self.old_column_type
        if self.old_nullable is not None:
            alter_kwargs["nullable"] = self.old_nullable
        if self.old_server_default is not None:
            alter_kwargs["server_default"] = _alembic_server_default(self.old_server_default)
        if alter_kwargs:
            alter_kwargs.update(
                (key, value) for key, value in self.kwargs.items() if key.startswith("existing_")
            )
        self._apply_alter(connection, alter_kwargs)

    def deconstruct(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model_name": self.model_name,
            "table": self.table,
            "name": self.name,
        }
        for key in (
            "column_type",
            "nullable",
            "server_default",
            "old_column_type",
            "old_nullable",
            "old_server_default",
        ):
            value = getattr(self, key)
            if value is not None:
                kwargs[key] = value
        kwargs.update(self.kwargs)
        return kwargs


def _alembic_server_default(value: Any) -> Any:
    """An ``AlterField`` server default as Alembic's ``alter_column`` takes it.

    ``NO_DEFAULT`` becomes ``None`` (Alembic's "drop the default"); a string is
    a literal value and anything else (``sa.text(...)``) SQL, as on a Column.
    """
    return None if value is NO_DEFAULT else value


class AddIndex(Operation):
    """Create an index."""

    def __init__(
        self, model_name: str, table: str, name: str, columns: list[str], unique: bool = False
    ):
        self.model_name = model_name
        self.table = table
        self.name = name
        self.columns = columns
        self.unique = unique

    def describe(self) -> str:
        return f"Create index {self.name} on {self.model_name}"

    def forward(self, connection) -> None:
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        ctx = MigrationContext.configure(connection)
        op = Operations(ctx)
        op.create_index(self.name, self.table, self.columns, unique=self.unique)

    def backward(self, connection) -> None:
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        ctx = MigrationContext.configure(connection)
        op = Operations(ctx)
        op.drop_index(self.name, table_name=self.table)

    def deconstruct(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model_name": self.model_name,
            "table": self.table,
            "name": self.name,
            "columns": list(self.columns),
        }
        if self.unique:
            kwargs["unique"] = True
        return kwargs


class RemoveIndex(Operation):
    """Drop an index.

    Pass the index's ``columns`` (and ``unique``) to make it reversible —
    ``makemigrations`` records them.
    """

    def __init__(
        self,
        model_name: str,
        table: str,
        name: str,
        columns: list[str] | None = None,
        unique: bool = False,
    ):
        self.model_name = model_name
        self.table = table
        self.name = name
        self.columns = list(columns or [])
        self.unique = unique

    @property
    def reversible(self):
        """Whether ``backward`` can undo this operation."""
        return bool(self.columns)

    def describe(self) -> str:
        return f"Remove index {self.name} from {self.model_name}"

    def forward(self, connection) -> None:
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        ctx = MigrationContext.configure(connection)
        op = Operations(ctx)
        op.drop_index(self.name, table_name=self.table)

    def backward(self, connection) -> None:
        if not self.columns:
            raise _irreversible(self, "the migration does not record the index's columns")
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        ctx = MigrationContext.configure(connection)
        op = Operations(ctx)
        op.create_index(self.name, self.table, self.columns, unique=self.unique)

    def deconstruct(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model_name": self.model_name,
            "table": self.table,
            "name": self.name,
        }
        if self.columns:
            kwargs["columns"] = list(self.columns)
        if self.unique:
            kwargs["unique"] = True
        return kwargs


class RunSQL(Operation):
    """Execute raw SQL.

    Without ``reverse_sql`` the operation is irreversible; pass
    ``reverse_sql=RunSQL.noop`` when unapplying needs no SQL.
    """

    #: ``reverse_sql``/``sql`` value meaning "nothing to run" (Django parity).
    noop = ""

    def __init__(self, sql: str, reverse_sql: str | None = None):
        self.sql = sql
        self.reverse_sql = reverse_sql

    @property
    def reversible(self):
        """Whether ``backward`` can undo this operation."""
        return self.reverse_sql is not None

    def describe(self) -> str:
        return "Run SQL"

    def forward(self, connection) -> None:
        if not self.sql:
            return
        from sqlalchemy import text
        connection.execute(text(self.sql))

    def backward(self, connection) -> None:
        if self.reverse_sql is None:
            raise _irreversible(self, "no reverse_sql was given (use RunSQL.noop for none)")
        if self.reverse_sql:
            from sqlalchemy import text
            connection.execute(text(self.reverse_sql))

    def deconstruct(self) -> dict[str, Any]:
        from zeeb_orm.migrations.serializer import Source

        def spelled(sql: str) -> Any:
            # ``RunSQL.noop`` reads better than ``''`` and says what it means.
            return Source("operations.RunSQL.noop") if sql == self.noop else sql

        kwargs: dict[str, Any] = {"sql": spelled(self.sql)}
        # ``reverse_sql=RunSQL.noop`` is reversible and ``None`` is not: the
        # distinction must survive into the file.
        if self.reverse_sql is not None:
            kwargs["reverse_sql"] = spelled(self.reverse_sql)
        return kwargs


class RunPython(Operation):
    """Execute a Python callable.

    Without ``reverse_code`` the operation is irreversible; pass
    ``reverse_code=RunPython.noop`` when unapplying needs no code.
    """

    @staticmethod
    def noop(connection) -> None:
        """A callable that does nothing (Django parity)."""

    def __init__(self, code: Any, reverse_code: Any = None):
        self.code = code
        self.reverse_code = reverse_code

    @property
    def reversible(self):
        """Whether ``backward`` can undo this operation."""
        return self.reverse_code is not None

    def describe(self) -> str:
        name = getattr(self.code, '__name__', 'function')
        return f"Run Python {name}"

    def forward(self, connection) -> None:
        self.code(connection)

    def backward(self, connection) -> None:
        if self.reverse_code is None:
            raise _irreversible(self, "no reverse_code was given (use RunPython.noop for none)")
        self.reverse_code(connection)

    def deconstruct(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"code": self.code}
        if self.reverse_code is not None:
            kwargs["reverse_code"] = self.reverse_code
        return kwargs


class RenameModel(Operation):
    """Rename a database table."""

    def __init__(self, old_name: str, new_name: str, old_table: str, new_table: str):
        self.old_name = old_name
        self.new_name = new_name
        self.old_table = old_table
        self.new_table = new_table

    def describe(self) -> str:
        return f"Rename model {self.old_name} to {self.new_name}"

    def forward(self, connection) -> None:
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        ctx = MigrationContext.configure(connection)
        op = Operations(ctx)
        op.rename_table(self.old_table, self.new_table)

    def backward(self, connection) -> None:
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        ctx = MigrationContext.configure(connection)
        op = Operations(ctx)
        op.rename_table(self.new_table, self.old_table)

    def deconstruct(self) -> dict[str, Any]:
        return {
            "old_name": self.old_name,
            "new_name": self.new_name,
            "old_table": self.old_table,
            "new_table": self.new_table,
        }


class RenameField(Operation):
    """Rename a column on an existing table."""

    def __init__(self, model_name: str, table: str, old_name: str, new_name: str):
        self.model_name = model_name
        self.table = table
        self.old_name = old_name
        self.new_name = new_name

    def describe(self) -> str:
        return f"Rename field {self.old_name} to {self.new_name} on {self.model_name}"

    def forward(self, connection) -> None:
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        ctx = MigrationContext.configure(connection)
        op = Operations(ctx)
        op.alter_column(self.table, self.old_name, new_column_name=self.new_name)

    def backward(self, connection) -> None:
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        ctx = MigrationContext.configure(connection)
        op = Operations(ctx)
        op.alter_column(self.table, self.new_name, new_column_name=self.old_name)

    def deconstruct(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "table": self.table,
            "old_name": self.old_name,
            "new_name": self.new_name,
        }


class AddConstraint(Operation):
    """Add a named constraint (UniqueConstraint, CheckConstraint, etc.) to a table."""

    def __init__(self, model_name: str, table: str, constraint: Any):
        name = getattr(constraint, 'name', None)
        if not name:
            raise ValueError(
                "AddConstraint requires the constraint to have a non-empty name. "
                "Pass name=... when constructing UniqueConstraint / CheckConstraint."
            )
        self.model_name = model_name
        self.table = table
        self.constraint = constraint

    def describe(self) -> str:
        name = getattr(self.constraint, 'name', None) or 'unnamed'
        return f"Add constraint {name} to {self.model_name}"

    def _unique_column_names(self) -> list[str]:
        columns = [
            col.key if hasattr(col, 'key') else str(col)
            for col in self.constraint.columns
        ]
        if columns:
            return columns
        pending = getattr(self.constraint, "_pending_colargs", ())
        return [col.name if hasattr(col, "name") else str(col) for col in pending]

    def _get_bound_constraint(self, connection):
        from sqlalchemy import MetaData, Table

        constraint_table = getattr(self.constraint, 'table', None)
        if constraint_table is not None:
            return self.constraint

        from sqlalchemy import CheckConstraint, UniqueConstraint
        if isinstance(self.constraint, (UniqueConstraint, CheckConstraint)):
            raise ValueError(
                "Unbound UniqueConstraint and CheckConstraint "
                "should be applied via Alembic operations."
            )

        metadata = MetaData()
        table = Table(self.table, metadata, autoload_with=connection)
        return self.constraint.copy(target_table=table)

    def forward(self, connection) -> None:
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        from sqlalchemy import CheckConstraint, UniqueConstraint
        from sqlalchemy.schema import AddConstraint as SAAddConstraint

        ctx = MigrationContext.configure(connection)
        op = Operations(ctx)
        is_sqlite = connection.dialect.name == "sqlite"
        name = str(self.constraint.name)  # __init__ guarantees a non-empty name

        if isinstance(self.constraint, UniqueConstraint):
            # SQLite cannot ALTER TABLE ADD CONSTRAINT; use Alembic batch mode.
            if is_sqlite:
                with op.batch_alter_table(self.table) as batch_op:
                    batch_op.create_unique_constraint(
                        name, self._unique_column_names()
                    )
            else:
                op.create_unique_constraint(
                    name, self.table, self._unique_column_names()
                )
            return

        if isinstance(self.constraint, CheckConstraint):
            if is_sqlite:
                with op.batch_alter_table(self.table) as batch_op:
                    batch_op.create_check_constraint(
                        name, str(self.constraint.sqltext)
                    )
            else:
                op.create_check_constraint(
                    name, self.table, str(self.constraint.sqltext)
                )
            return

        constraint = self._get_bound_constraint(connection)
        connection.execute(SAAddConstraint(constraint))

    def backward(self, connection) -> None:
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        from sqlalchemy import CheckConstraint, UniqueConstraint
        from sqlalchemy.schema import DropConstraint

        ctx = MigrationContext.configure(connection)
        op = Operations(ctx)
        is_sqlite = connection.dialect.name == "sqlite"
        name = str(self.constraint.name)  # __init__ guarantees a non-empty name

        if isinstance(self.constraint, (UniqueConstraint, CheckConstraint)):
            type_ = "unique" if isinstance(self.constraint, UniqueConstraint) else "check"
            if is_sqlite:
                with op.batch_alter_table(self.table) as batch_op:
                    batch_op.drop_constraint(name, type_=type_)
            else:
                op.drop_constraint(name, self.table, type_=type_)
            return

        constraint = self._get_bound_constraint(connection)
        connection.execute(DropConstraint(constraint))

    def deconstruct(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "table": self.table,
            "constraint": self.constraint,
        }


class RemoveConstraint(Operation):
    """Drop a named constraint from a table.

    Pass the dropped ``constraint`` itself (an ``sa.UniqueConstraint`` /
    ``sa.CheckConstraint`` with the same name) to make it reversible —
    ``makemigrations`` records it. Without it, unapplying raises
    :class:`~zeeb_orm.migrations.state.IrreversibleError`.
    """

    def __init__(
        self,
        model_name: str,
        table: str,
        name: str,
        constraint_type: str = "unique",
        constraint: Any = None,
    ):
        """
        Args:
            model_name: Model name for documentation
            table: Table name
            name: Constraint name
            constraint_type: Type of constraint ('unique', 'check', 'foreignkey', 'primary')
                           Defaults to 'unique' for backward compatibility.
            constraint: The constraint as it was, to recreate it on rollback.
        """
        self.model_name = model_name
        self.table = table
        self.name = name
        self.constraint_type = constraint_type
        self.constraint = constraint

    @property
    def reversible(self):
        """Whether ``backward`` can undo this operation."""
        return self.constraint is not None

    def describe(self) -> str:
        return f"Remove constraint {self.name} from {self.model_name}"

    def forward(self, connection) -> None:
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        ctx = MigrationContext.configure(connection)
        op = Operations(ctx)
        # SQLite cannot ALTER TABLE DROP CONSTRAINT; use Alembic batch mode.
        if connection.dialect.name == "sqlite":
            with op.batch_alter_table(self.table) as batch_op:
                batch_op.drop_constraint(self.name, type_=self.constraint_type)
        else:
            op.drop_constraint(self.name, self.table, type_=self.constraint_type)

    def backward(self, connection) -> None:
        if self.constraint is None:
            raise _irreversible(
                self, "the migration does not record the constraint's definition"
            )
        AddConstraint(self.model_name, self.table, self.constraint).forward(connection)

    def deconstruct(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model_name": self.model_name,
            "table": self.table,
            "name": self.name,
            "constraint_type": self.constraint_type,
        }
        if self.constraint is not None:
            kwargs["constraint"] = self.constraint
        return kwargs


class AlterModelTable(Operation):
    """Rename a model's table (``Meta.table_name`` changed), keeping its rows.

    ``makemigrations`` emits it when a model keeps its name but declares a
    different table; dropping the old table and creating the new one would
    lose every row.
    """

    def __init__(self, name: str, old_table: str, new_table: str):
        self.name = name
        self.old_table = old_table
        self.new_table = new_table

    def describe(self) -> str:
        return f"Rename table {self.old_table} to {self.new_table} for {self.name}"

    def forward(self, connection) -> None:
        _alembic_ops(connection).rename_table(self.old_table, self.new_table)

    def backward(self, connection) -> None:
        _alembic_ops(connection).rename_table(self.new_table, self.old_table)

    def deconstruct(self) -> dict[str, Any]:
        return {"name": self.name, "old_table": self.old_table, "new_table": self.new_table}


# --- Rendering helpers (kept for callers; the serializer does the work) ---

def _repr_constraint(constraint) -> str:
    """Render a table constraint as an ``sa.*`` constructor."""
    from zeeb_orm.migrations.serializer import SourceWriter

    return SourceWriter().constraint(constraint)


def _repr_column(col) -> str:
    """Render a SQLAlchemy Column as an ``sa.Column(...)`` call."""
    from zeeb_orm.migrations.serializer import SourceWriter

    return SourceWriter().value(col)


def _scalar_default(col):
    """The column's Python-side default when it is a plain scalar, else ``None``.

    Callables (``datetime.now``, sequences, …) cannot be rendered into DDL or a
    migration file, so they are deliberately ignored.
    """
    default = getattr(col, "default", None)
    if default is None:
        return None
    arg = getattr(default, "arg", default)
    if callable(arg) or isinstance(arg, (list, dict, set)):
        return None
    if isinstance(arg, Decimal):
        # Exact text, as a DecimalField default written as a string always
        # was: a float would round it, and a migration file needs no import.
        return str(arg)
    return arg if isinstance(arg, (str, int, float, bool)) else None


def _repr_sa_type(sa_type) -> str:
    """Render a SQLAlchemy type, dialect variants and arguments included.

    Dialect variants (``BigInteger().with_variant(Integer, "sqlite")``, which
    ``BigAutoField`` uses) must survive, or the broken ``BIGINT`` primary key
    comes back on SQLite; so must every argument (``DateTime(timezone=True)``).
    Raises :class:`~zeeb_orm.migrations.serializer.MigrationSerializationError`
    for a type that cannot be written faithfully.
    """
    from zeeb_orm.migrations.serializer import SourceWriter

    return SourceWriter().type_(sa_type)
