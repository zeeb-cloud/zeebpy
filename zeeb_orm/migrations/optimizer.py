"""
Migration optimizer — reduces a list of operations to the smallest equivalent set.

Applied automatically by ``squashmigrations`` to produce compact squashed files.

Rules implemented
-----------------
* ``CreateModel`` + ``AddField`` (same table) → ``CreateModel`` with the extra column
* ``CreateModel`` + ``DeleteModel`` (same table) → nothing (they cancel out)
* ``AddField`` + ``RemoveField`` (same table, same column) → nothing (cancel out)
* ``AddField`` + ``AlterField`` (same table, same column) → ``AddField`` with updated type
* ``AddIndex`` + ``RemoveIndex`` (same name) → nothing (cancel out)

A reduction moves an operation past the ones between the pair, so it is only
made when they commute: they touch disjoint tables, a foreign key counting as a
touch of the table it references. ``RunSQL``, ``RunPython`` and any operation
whose tables are unknown commute with nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zeeb_orm.migrations.operations import Operation


def optimize(operations: list[Operation]) -> list[Operation]:
    """
    Return an optimized (reduced) copy of *operations*.

    The list is processed repeatedly until no further reductions are possible.
    """
    changed = True
    ops = list(operations)
    while changed:
        ops, changed = _one_pass(ops)
    return ops


def _one_pass(ops: list[Operation]) -> tuple[list[Operation], bool]:
    """Perform one optimization pass over the list.

    Returns ``(new_ops, changed)`` where *changed* is True if any reduction
    was made.
    """
    from sqlalchemy.schema import DefaultClause

    from zeeb_orm.migrations.operations import (
        NO_DEFAULT,
        AddField,
        AddIndex,
        AlterField,
        CreateModel,
        DeleteModel,
        RemoveField,
        RemoveIndex,
        copy_column,
    )

    result: list[Operation] = []
    i = 0
    changed = False

    while i < len(ops):
        current = ops[i]
        merged = False

        # Try to combine current with any later operation
        for j in range(i + 1, len(ops)):
            later = ops[j]

            # CreateModel + AddField (same table) → fuse into CreateModel
            if (
                isinstance(current, CreateModel)
                and isinstance(later, AddField)
                and current.table == later.table
                and _can_move_back(later, ops[i + 1:j])
            ):
                new_columns = list(current.columns) + [later.column]
                fused = CreateModel(
                    name=current.name,
                    table=current.table,
                    columns=new_columns,
                    primary_key=current.primary_key,
                    constraints=current.constraints,
                )
                result.append(fused)
                ops = ops[:i] + ops[i + 1:j] + ops[j + 1:]
                changed = True
                merged = True
                break

            # CreateModel + DeleteModel (same table) → eliminate both
            if (
                isinstance(current, CreateModel)
                and isinstance(later, DeleteModel)
                and current.table == later.table
            ):
                ops = ops[:i] + ops[i + 1:j] + ops[j + 1:]
                changed = True
                merged = True
                break

            # AddField + RemoveField (same table + column) → eliminate both
            if (
                isinstance(current, AddField)
                and isinstance(later, RemoveField)
                and current.table == later.table
                and current.name == later.name
            ):
                ops = ops[:i] + ops[i + 1:j] + ops[j + 1:]
                changed = True
                merged = True
                break

            # AddField + AlterField (same table + column) → AddField with new type
            if (
                isinstance(current, AddField)
                and isinstance(later, AlterField)
                and current.table == later.table
                and current.name == later.name
                and all(key.startswith("existing_") for key in later.kwargs)
                and _can_move_back(later, ops[i + 1:j])
            ):
                col = current.column
                # Copy the column to preserve all attributes (server_default,
                # unique, foreign_keys, comment, autoincrement, etc.) and only
                # override the specific attributes changed by AlterField.
                new_col = copy_column(col)
                if later.column_type is not None:
                    new_col.type = later.column_type
                if later.nullable is not None:
                    new_col.nullable = later.nullable
                if later.server_default is NO_DEFAULT:
                    new_col.server_default = None
                elif later.server_default is not None:
                    new_col.server_default = DefaultClause(later.server_default)
                fused = AddField(
                    model_name=current.model_name,
                    table=current.table,
                    name=current.name,
                    column=new_col,
                    backfill=current.backfill,
                )
                result.append(fused)
                ops = ops[:i] + ops[i + 1:j] + ops[j + 1:]
                changed = True
                merged = True
                break

            # AddIndex + RemoveIndex (same table + name) → eliminate both
            if (
                isinstance(current, AddIndex)
                and isinstance(later, RemoveIndex)
                and current.table == later.table
                and current.name == later.name
            ):
                ops = ops[:i] + ops[i + 1:j] + ops[j + 1:]
                changed = True
                merged = True
                break

            # If operations are on the same table and non-commutative, stop
            # trying to combine current with anything further.
            if _blocks(current, later):
                break

        if not merged:
            result.append(ops[i])
            i += 1

    return result, changed


def _blocks(a: Operation, b: Operation) -> bool:
    """Return True if operation *b* cannot be reordered past *a*.

    Conservative: two operations commute only when both touch a known set of
    tables and those sets are disjoint.
    """
    tables_a, tables_b = _tables(a), _tables(b)
    if tables_a is None or tables_b is None:
        return True
    return bool(tables_a & tables_b)


def _can_move_back(op: Operation, between: list[Operation]) -> bool:
    """Whether *op* can run before every operation in *between*."""
    return not any(_blocks(other, op) for other in between)


def _tables(op: Operation) -> set[str] | None:
    """The tables *op* reads or changes — ``None`` when that is unknowable.

    Includes the tables its foreign keys reference: creating a table that
    points at another must stay after that table's creation and before its
    deletion.
    """
    from zeeb_orm.migrations import operations as ops

    if isinstance(op, (ops.RunSQL, ops.RunPython)):
        return None
    if isinstance(op, (ops.RenameModel, ops.AlterModelTable)):
        return {op.old_table, op.new_table}
    table = getattr(op, "table", None)
    if not table:
        return None
    tables = {table}
    columns: list = []
    if isinstance(op, (ops.CreateModel, ops.DeleteModel)):
        columns = list(op.columns)
    elif isinstance(op, ops.AddField):
        columns = [op.column]
    elif isinstance(op, ops.RemoveField) and op.field is not None:
        columns = [op.field]
    for column in columns:
        for fk in getattr(column, "foreign_keys", ()):
            tables.add(str(fk.target_fullname).rpartition(".")[0])
    for constraint in getattr(op, "constraints", None) or []:
        for element in getattr(constraint, "elements", ()):
            tables.add(str(element.target_fullname).rpartition(".")[0])
    constraint = getattr(op, "constraint", None)
    for element in getattr(constraint, "elements", ()) if constraint is not None else ():
        tables.add(str(element.target_fullname).rpartition(".")[0])
    return tables
