"""
Django-style migration operations.

Each operation represents a single schema change (create table, add column, etc.)
and knows how to apply itself forward and backward.
"""

from __future__ import annotations

from typing import Any


def _quote(connection, identifier: str) -> str:
    """Quote a SQL identifier (table/column name) for the active dialect."""
    return connection.dialect.identifier_preparer.quote(identifier)


class Operation:
    """Base class for migration operations.

    Subclasses must implement ``describe``, ``forward`` and ``backward``.
    """

    reversible = True

    def describe(self) -> str:
        raise NotImplementedError

    def forward(self, connection) -> None:
        """Execute forward migration."""
        raise NotImplementedError

    def backward(self, connection) -> None:
        """Execute backward migration."""
        raise NotImplementedError


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
        from sqlalchemy.schema import CreateTable
        tmp_meta = MetaData()
        cols = list(self.columns)
        if self.primary_key:
            cols.append(PrimaryKeyConstraint(*self.primary_key))
        for c in self.constraints:
            cols.append(c)
        table = Table(self.table, tmp_meta, *cols)
        table.create(connection, checkfirst=True)

    def backward(self, connection) -> None:
        from sqlalchemy import text
        connection.execute(text(f"DROP TABLE IF EXISTS {_quote(connection, self.table)}"))

    def __repr__(self) -> str:
        parts = [
            f"    operations.CreateModel(\n"
            f"        name={self.name!r},\n"
            f"        table={self.table!r},\n"
            f"        columns=[\n"
        ]
        for col in self.columns:
            parts.append(f"            {_repr_column(col)},\n")
        parts.append(f"        ],\n")
        if self.primary_key:
            parts.append(f"        primary_key={self.primary_key!r},\n")
        if self.constraints:
            parts.append(f"        constraints=[\n")
            for c in self.constraints:
                parts.append(f"            {_repr_constraint(c)},\n")
            parts.append(f"        ],\n")
        parts.append(f"    )")
        return "".join(parts)


class DeleteModel(Operation):
    """Drop a database table."""

    def __init__(self, name: str, table: str):
        self.name = name
        self.table = table

    def describe(self) -> str:
        return f"Delete model {self.name}"

    def forward(self, connection) -> None:
        from sqlalchemy import text
        connection.execute(text(f"DROP TABLE IF EXISTS {_quote(connection, self.table)}"))

    def backward(self, connection) -> None:
        pass  # Cannot recreate without column info

    def __repr__(self) -> str:
        return f"    operations.DeleteModel(name={self.name!r}, table={self.table!r})"


class AddField(Operation):
    """Add a column to an existing table."""

    def __init__(self, model_name: str, table: str, name: str, column: Any):
        self.model_name = model_name
        self.table = table
        self.name = name
        self.column = column

    def describe(self) -> str:
        return f"Add field {self.name} to {self.model_name}"

    def forward(self, connection) -> None:
        from alembic.operations import Operations, MigrateOperation
        from alembic.runtime.migration import MigrationContext
        ctx = MigrationContext.configure(connection)
        op = Operations(ctx)
        op.add_column(self.table, self.column)

    def backward(self, connection) -> None:
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        ctx = MigrationContext.configure(connection)
        op = Operations(ctx)
        op.drop_column(self.table, self.name)

    def __repr__(self) -> str:
        return (
            f"    operations.AddField(\n"
            f"        model_name={self.model_name!r},\n"
            f"        table={self.table!r},\n"
            f"        name={self.name!r},\n"
            f"        column={_repr_column(self.column)},\n"
            f"    )"
        )


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
        return self.field is not None

    def describe(self) -> str:
        return f"Remove field {self.name} from {self.model_name}"

    def forward(self, connection) -> None:
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        ctx = MigrationContext.configure(connection)
        op = Operations(ctx)
        op.drop_column(self.table, self.name)

    def backward(self, connection) -> None:
        if self.field is not None:
            from alembic.operations import Operations
            from alembic.runtime.migration import MigrationContext
            ctx = MigrationContext.configure(connection)
            op = Operations(ctx)
            op.add_column(self.table, self.field)

    def __repr__(self) -> str:
        parts = [
            f"    operations.RemoveField(\n"
            f"        model_name={self.model_name!r},\n"
            f"        table={self.table!r},\n"
            f"        name={self.name!r},\n"
        ]
        if self.field is not None:
            parts.append(f"        field={_repr_column(self.field)},\n")
        parts.append(f"    )")
        return "".join(parts)


class AlterField(Operation):
    """Alter a column's type, nullable, default, etc.

    Pass ``old_column_type`` and/or ``old_nullable`` to enable reversal.
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
            alter_kwargs["server_default"] = self.server_default
        alter_kwargs.update(self.kwargs)
        self._apply_alter(connection, alter_kwargs)

    def backward(self, connection) -> None:
        alter_kwargs: dict[str, Any] = {}
        if self.old_column_type is not None:
            alter_kwargs["type_"] = self.old_column_type
        if self.old_nullable is not None:
            alter_kwargs["nullable"] = self.old_nullable
        if self.old_server_default is not None:
            alter_kwargs["server_default"] = self.old_server_default
        self._apply_alter(connection, alter_kwargs)

    def __repr__(self) -> str:
        parts = [
            f"    operations.AlterField(\n"
            f"        model_name={self.model_name!r},\n"
            f"        table={self.table!r},\n"
            f"        name={self.name!r},\n"
        ]
        if self.column_type is not None:
            parts.append(f"        column_type={_repr_sa_type(self.column_type)},\n")
        if self.nullable is not None:
            parts.append(f"        nullable={self.nullable!r},\n")
        if self.server_default is not None:
            parts.append(f"        server_default={self.server_default!r},\n")
        if self.old_column_type is not None:
            parts.append(f"        old_column_type={_repr_sa_type(self.old_column_type)},\n")
        if self.old_nullable is not None:
            parts.append(f"        old_nullable={self.old_nullable!r},\n")
        if self.old_server_default is not None:
            parts.append(f"        old_server_default={self.old_server_default!r},\n")
        parts.append(f"    )")
        return "".join(parts)


class AddIndex(Operation):
    """Create an index."""

    def __init__(self, model_name: str, table: str, name: str, columns: list[str], unique: bool = False):
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

    def __repr__(self) -> str:
        parts = [
            f"    operations.AddIndex(\n"
            f"        model_name={self.model_name!r},\n"
            f"        table={self.table!r},\n"
            f"        name={self.name!r},\n"
            f"        columns={self.columns!r},\n"
        ]
        if self.unique:
            parts.append(f"        unique=True,\n")
        parts.append(f"    )")
        return "".join(parts)


class RemoveIndex(Operation):
    """Drop an index."""

    def __init__(self, model_name: str, table: str, name: str):
        self.model_name = model_name
        self.table = table
        self.name = name

    def describe(self) -> str:
        return f"Remove index {self.name} from {self.model_name}"

    def forward(self, connection) -> None:
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        ctx = MigrationContext.configure(connection)
        op = Operations(ctx)
        op.drop_index(self.name, table_name=self.table)

    def backward(self, connection) -> None:
        pass

    def __repr__(self) -> str:
        return (
            f"    operations.RemoveIndex(\n"
            f"        model_name={self.model_name!r},\n"
            f"        table={self.table!r},\n"
            f"        name={self.name!r},\n"
            f"    )"
        )


class RunSQL(Operation):
    """Execute raw SQL."""

    def __init__(self, sql: str, reverse_sql: str | None = None):
        self.sql = sql
        self.reverse_sql = reverse_sql

    @property
    def reversible(self):
        return self.reverse_sql is not None

    def describe(self) -> str:
        return "Run SQL"

    def forward(self, connection) -> None:
        from sqlalchemy import text
        connection.execute(text(self.sql))

    def backward(self, connection) -> None:
        if self.reverse_sql:
            from sqlalchemy import text
            connection.execute(text(self.reverse_sql))

    def __repr__(self) -> str:
        parts = [f"    operations.RunSQL(\n        sql={self.sql!r},\n"]
        if self.reverse_sql:
            parts.append(f"        reverse_sql={self.reverse_sql!r},\n")
        parts.append(f"    )")
        return "".join(parts)


class RunPython(Operation):
    """Execute a Python callable."""

    def __init__(self, code: Any, reverse_code: Any = None):
        self.code = code
        self.reverse_code = reverse_code

    @property
    def reversible(self):
        return self.reverse_code is not None

    def describe(self) -> str:
        name = getattr(self.code, '__name__', 'function')
        return f"Run Python {name}"

    def forward(self, connection) -> None:
        self.code(connection)

    def backward(self, connection) -> None:
        if self.reverse_code:
            self.reverse_code(connection)

    def __repr__(self) -> str:
        name = getattr(self.code, '__name__', 'function')
        return f"    operations.RunPython({name})"


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

    def __repr__(self) -> str:
        return (
            f"    operations.RenameModel(\n"
            f"        old_name={self.old_name!r},\n"
            f"        new_name={self.new_name!r},\n"
            f"        old_table={self.old_table!r},\n"
            f"        new_table={self.new_table!r},\n"
            f"    )"
        )


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

    def __repr__(self) -> str:
        return (
            f"    operations.RenameField(\n"
            f"        model_name={self.model_name!r},\n"
            f"        table={self.table!r},\n"
            f"        old_name={self.old_name!r},\n"
            f"        new_name={self.new_name!r},\n"
            f"    )"
        )


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

    def __repr__(self) -> str:
        return (
            f"    operations.AddConstraint(\n"
            f"        model_name={self.model_name!r},\n"
            f"        table={self.table!r},\n"
            f"        constraint={_repr_constraint(self.constraint)},\n"
            f"    )"
        )


class RemoveConstraint(Operation):
    """Drop a named constraint from a table."""

    reversible = False

    def __init__(self, model_name: str, table: str, name: str, constraint_type: str = "unique"):
        """
        Args:
            model_name: Model name for documentation
            table: Table name
            name: Constraint name
            constraint_type: Type of constraint ('unique', 'check', 'foreignkey', 'primary')
                           Defaults to 'unique' for backward compatibility.
        """
        self.model_name = model_name
        self.table = table
        self.name = name
        self.constraint_type = constraint_type

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
        pass  # Cannot recreate without constraint definition

    def __repr__(self) -> str:
        return (
            f"    operations.RemoveConstraint(\n"
            f"        model_name={self.model_name!r},\n"
            f"        table={self.table!r},\n"
            f"        name={self.name!r},\n"
            f"        constraint_type={self.constraint_type!r},\n"
            f"    )"
        )


# --- Helpers for repr ---

def _repr_constraint(constraint) -> str:
    """Render a UniqueConstraint/CheckConstraint as an ``sa.*`` constructor."""
    from sqlalchemy import CheckConstraint, UniqueConstraint

    if isinstance(constraint, UniqueConstraint):
        cols = [
            col.key if hasattr(col, "key") else str(col)
            for col in constraint.columns
        ]
        if not cols:
            pending = getattr(constraint, "_pending_colargs", ())
            cols = [c.name if hasattr(c, "name") else str(c) for c in pending]
        col_repr = ", ".join(repr(c) for c in cols)
        return f"sa.UniqueConstraint({col_repr}, name={constraint.name!r})"
    if isinstance(constraint, CheckConstraint):
        return f"sa.CheckConstraint({str(constraint.sqltext)!r}, name={constraint.name!r})"
    return repr(constraint)


def _repr_column(col) -> str:
    """Generate repr for a SQLAlchemy Column."""
    from sqlalchemy import Column
    if not isinstance(col, Column):
        return repr(col)

    parts = [f"sa.Column({col.name!r}, {_repr_sa_type(col.type)}"]

    for fk in col.foreign_keys:
        fk_args = [f"{str(fk.target_fullname)!r}"]
        if fk.ondelete:
            fk_args.append(f"ondelete={fk.ondelete!r}")
        if fk.onupdate:
            fk_args.append(f"onupdate={fk.onupdate!r}")
        parts.append(f", sa.ForeignKey({', '.join(fk_args)})")

    if not col.nullable:
        parts.append(", nullable=False")
    if col.unique:
        parts.append(", unique=True")
    if col.server_default is not None:
        parts.append(f", server_default={col.server_default.arg!r}")

    parts.append(")")
    return "".join(parts)


def _repr_sa_type(sa_type) -> str:
    """Generate repr for a SQLAlchemy type."""
    from sqlalchemy import types as satypes

    type_name = type(sa_type).__name__
    module = type(sa_type).__module__

    # Common SQLAlchemy types
    if module.startswith("sqlalchemy"):
        prefix = "sa."
    else:
        prefix = f"{module}."

    # Types with parameters
    if isinstance(sa_type, satypes.String) and sa_type.length:
        return f"{prefix}{type_name}({sa_type.length})"
    if isinstance(sa_type, satypes.Numeric):
        args = []
        if sa_type.precision is not None:
            args.append(str(sa_type.precision))
        if sa_type.scale is not None:
            args.append(str(sa_type.scale))
        return f"{prefix}{type_name}({', '.join(args)})" if args else f"{prefix}{type_name}()"
    if isinstance(sa_type, satypes.Float) and sa_type.precision is not None:
        return f"{prefix}{type_name}({sa_type.precision})"

    return f"{prefix}{type_name}()"
