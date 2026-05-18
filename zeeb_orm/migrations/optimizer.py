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
    from zeeb_orm.migrations.operations import (
        AddField,
        AddIndex,
        AlterField,
        CreateModel,
        DeleteModel,
        RemoveField,
        RemoveIndex,
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
            ):
                import sqlalchemy as sa
                col = current.column
                new_col = sa.Column(
                    col.name,
                    later.column_type if later.column_type is not None else col.type,
                    nullable=later.nullable if later.nullable is not None else col.nullable,
                )
                fused = AddField(
                    model_name=current.model_name,
                    table=current.table,
                    name=current.name,
                    column=new_col,
                )
                result.append(fused)
                ops = ops[:i] + ops[i + 1:j] + ops[j + 1:]
                changed = True
                merged = True
                break

            # AddIndex + RemoveIndex (same name) → eliminate both
            if (
                isinstance(current, AddIndex)
                and isinstance(later, RemoveIndex)
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

    A conservative check: if both operations touch the same table we assume
    they don't commute.
    """
    table_a = getattr(a, "table", None)
    table_b = getattr(b, "table", None)
    if table_a and table_b and table_a == table_b:
        return True
    return False
