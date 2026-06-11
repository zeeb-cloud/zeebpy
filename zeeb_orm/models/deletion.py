"""Django-style ``on_delete`` handling for ForeignKey relations.

This module defines the ``on_delete`` constants, the mapping from those
constants to database-level ``ON DELETE`` clauses, and the :class:`Collector`
that implements Python-side deletion semantics (cascades, protection,
SET NULL / SET DEFAULT updates) for :meth:`Model.delete` and
:meth:`QuerySet.delete`.

The constants are plain strings so they remain backwards compatible with the
previous API of passing ``on_delete="CASCADE"`` directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zeeb_orm.models.base import Model
    from zeeb_orm.models.fields import ForeignKeyField

#: Delete referencing rows together with the referenced row.
CASCADE = "CASCADE"
#: Refuse to delete when referencing rows exist (always).
PROTECT = "PROTECT"
#: Refuse to delete when referencing rows exist, unless those rows are
#: themselves deleted in the same operation (via another cascade path).
RESTRICT = "RESTRICT"
#: Set the referencing foreign key column to NULL (requires ``null=True``).
SET_NULL = "SET_NULL"
#: Set the referencing foreign key column to the field default
#: (requires a ``default``).
SET_DEFAULT = "SET_DEFAULT"
#: Take no action; referencing rows are left untouched (may dangle).
DO_NOTHING = "DO_NOTHING"

#: All valid values for ``ForeignKey(on_delete=...)``.
ON_DELETE_VALUES = frozenset(
    {CASCADE, PROTECT, RESTRICT, SET_NULL, SET_DEFAULT, DO_NOTHING}
)

#: Mapping from on_delete constant to database ``ON DELETE`` clause.
#: ``PROTECT`` maps to ``RESTRICT`` at the database level (the stricter
#: Python-side check happens in the Collector before any SQL runs).
#: ``DO_NOTHING`` maps to ``None`` (no ON DELETE clause is emitted).
_DB_ONDELETE = {
    CASCADE: "CASCADE",
    SET_NULL: "SET NULL",
    SET_DEFAULT: "SET DEFAULT",
    RESTRICT: "RESTRICT",
    PROTECT: "RESTRICT",
    DO_NOTHING: None,
}


def to_db_ondelete(on_delete: str) -> str | None:
    """Return the SQL ``ON DELETE`` action for an on_delete constant.

    Returns ``None`` when no ``ON DELETE`` clause should be emitted
    (``DO_NOTHING`` or unknown values).
    """
    return _DB_ONDELETE.get(on_delete)


def get_inbound_foreign_keys(
    model: type[Model],
) -> list[tuple[type[Model], ForeignKeyField[Any]]]:
    """Return ``(referencing_model, fk_field)`` pairs for FKs targeting ``model``."""
    from zeeb_orm.models.base import _model_registry

    result: list[tuple[type[Model], ForeignKeyField[Any]]] = []
    for ref_model in list(_model_registry.values()):
        for fk_field in getattr(ref_model, "_fk_fields", []):
            try:
                target = fk_field.get_target_model()
            except Exception:
                continue
            if target is model:
                result.append((ref_model, fk_field))
    return result


def model_has_inbound_refs(model: type[Model]) -> bool:
    """True when any registered model has a non-DO_NOTHING FK to ``model``."""
    return any(
        fk.on_delete != DO_NOTHING for _m, fk in get_inbound_foreign_keys(model)
    )


class Collector:
    """Collects the full object graph affected by deleting some instances.

    Usage::

        collector = Collector()
        await collector.collect([instance])      # may raise Protected/RestrictedError
        total, per_model = await collector.delete()

    ``collect()`` does a breadth-first walk over reverse FK edges
    (via :func:`get_inbound_foreign_keys`):

    - ``CASCADE``: referencing rows are fetched and collected recursively.
    - ``PROTECT``: any referencing row raises :class:`ProtectedError`
      (checked before any delete is executed).
    - ``RESTRICT``: referencing rows raise :class:`RestrictedError` unless
      they are themselves collected for deletion via another cascade path.
    - ``SET_NULL`` / ``SET_DEFAULT``: a bulk UPDATE of the referencing
      column is queued (executed before the parent rows are deleted).
    - ``DO_NOTHING``: skipped entirely.

    Self-referential FKs are handled by a recursion guard: instances already
    collected are never re-collected.
    """

    def __init__(self, using: str | None = None) -> None:
        self.using = using
        #: model -> {pk: instance}; insertion order is BFS order (roots first).
        self.data: dict[type[Model], dict[Any, Model]] = {}
        #: queued (model, fk_field, parent_pks, new_value) bulk updates.
        self.field_updates: list[tuple[type[Model], Any, list[Any], Any]] = []
        self._protected: list[tuple[type[Model], Any, list[Model]]] = []
        self._restricted: list[tuple[type[Model], Any, list[Model]]] = []

    async def collect(self, objs: list[Model]) -> None:
        """Collect ``objs`` and everything reachable through on_delete edges.

        Raises:
            ProtectedError: when a PROTECT relation references collected rows.
            RestrictedError: when a RESTRICT relation references rows that are
                not themselves collected for deletion.
        """
        from zeeb_orm.exceptions import ProtectedError, RestrictedError

        await self._collect(list(objs))

        if self._protected:
            protected_objects = {
                obj for _model, _fk, refs in self._protected for obj in refs
            }
            references = sorted(
                {
                    f"{model.__name__}.{fk.name}"
                    for model, fk, _refs in self._protected
                }
            )
            raise ProtectedError(
                "Cannot delete some instances because they are referenced "
                f"through protected foreign keys: {', '.join(references)}.",
                protected_objects,
            )

        restricted_objects = {
            obj
            for model, _fk, refs in self._restricted
            for obj in refs
            if obj.pk not in self.data.get(model, {})
        }
        if restricted_objects:
            references = sorted(
                {
                    f"{model.__name__}.{fk.name}"
                    for model, fk, _refs in self._restricted
                }
            )
            raise RestrictedError(
                "Cannot delete some instances because they are referenced "
                f"through restricted foreign keys: {', '.join(references)}.",
                restricted_objects,
            )

    async def _collect(self, objs: list[Model]) -> None:
        from zeeb_orm.query.queryset import QuerySet

        by_model: dict[type[Model], list[Model]] = {}
        for obj in objs:
            by_model.setdefault(type(obj), []).append(obj)

        for model, instances in by_model.items():
            bucket = self.data.setdefault(model, {})
            new = [o for o in instances if o.pk not in bucket]
            if not new:
                continue  # recursion guard (self-referential FKs / cycles)
            for obj in new:
                bucket[obj.pk] = obj
            pks = [o.pk for o in new]

            for ref_model, fk_field in get_inbound_foreign_keys(model):
                on_delete = fk_field.on_delete
                if on_delete == DO_NOTHING:
                    continue
                if on_delete == SET_NULL:
                    self.field_updates.append((ref_model, fk_field, pks, None))
                    continue
                if on_delete == SET_DEFAULT:
                    default = fk_field.default
                    if callable(default):
                        default = default()
                    self.field_updates.append((ref_model, fk_field, pks, default))
                    continue

                qs = QuerySet(ref_model).filter(**{f"{fk_field.db_column}__in": pks})
                qs._db_alias = self.using
                refs = await qs._fetch_all()
                if not refs:
                    continue
                if on_delete == PROTECT:
                    self._protected.append((ref_model, fk_field, refs))
                elif on_delete == RESTRICT:
                    self._restricted.append((ref_model, fk_field, refs))
                else:  # CASCADE
                    await self._collect(refs)

    async def delete(self) -> tuple[int, dict[str, int]]:
        """Execute the queued updates and deletes (leaf-first).

        Fires :data:`~zeeb_orm.signals.pre_delete` for every collected
        instance before its row is deleted and
        :data:`~zeeb_orm.signals.post_delete` after the deletes have been
        committed (or, inside ``atomic()``, after they have been executed).

        Returns:
            ``(total_deleted, {model_name: count})``.
        """
        from sqlalchemy import delete as sa_delete
        from sqlalchemy import update as sa_update

        from zeeb_orm.db.connection import get_session
        from zeeb_orm.signals import post_delete, pre_delete

        # Children were collected after their parents (BFS order) and
        # reference them — delete in reverse collection order (leaf-first).
        delete_order = list(reversed(list(self.data.items())))

        total = 0
        per_model: dict[str, int] = {}

        async with get_session(self.using) as (session, should_commit):
            # SET_NULL / SET_DEFAULT updates run before parent rows vanish.
            for model, fk_field, parent_pks, value in self.field_updates:
                table = model._get_table()
                col = table.c[fk_field.db_column]
                stmt = (
                    sa_update(table)
                    .where(col.in_(parent_pks))
                    .values({fk_field.db_column: value})
                )
                await session.execute(stmt)

            for model, instances in delete_order:
                if not instances:
                    continue
                for instance in instances.values():
                    await pre_delete.send(sender=model, instance=instance)

                table = model._get_table()
                pk_col = table.c[model._meta.pk.db_column or model._meta.pk_name]
                result = await session.execute(
                    sa_delete(table).where(pk_col.in_(list(instances.keys())))
                )
                count = result.rowcount
                if count is None or count < 0:
                    count = len(instances)
                if count:
                    total += count
                    label = model.__name__
                    per_model[label] = per_model.get(label, 0) + count

            if should_commit:
                await session.commit()

        for model, instances in delete_order:
            for instance in instances.values():
                instance._state.persisted = False
                await post_delete.send(sender=model, instance=instance)

        return total, per_model


__all__ = [
    "CASCADE",
    "PROTECT",
    "RESTRICT",
    "SET_NULL",
    "SET_DEFAULT",
    "DO_NOTHING",
    "ON_DELETE_VALUES",
    "to_db_ondelete",
    "get_inbound_foreign_keys",
    "model_has_inbound_refs",
    "Collector",
]
