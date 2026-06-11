"""Many-to-many accessors: descriptor and per-instance related manager.

Forward access (``post.tags``) goes through
:meth:`~zeeb_orm.models.fields.ManyToManyField.__get__`; reverse access
(``tag.posts``) through a :class:`ManyToManyDescriptor` installed during
pending-relation processing.  Both return a :class:`ManyRelatedManager`
bound to the instance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, insert, select

from zeeb_orm.exceptions import NotSupportedError
from zeeb_orm.models.manager import Manager

if TYPE_CHECKING:
    from zeeb_orm.models.fields import ManyToManyField
    from zeeb_orm.query.queryset import QuerySet


def _check_instance_usable(instance: Any) -> None:
    """Raise when the instance has no primary key yet (Django parity)."""
    pk_name = instance._meta.pk_name
    if getattr(instance, pk_name, None) is None:
        raise ValueError(
            f'"{instance!r}" needs to have a value for field "{pk_name}" '
            "before this many-to-many relationship can be used."
        )


class ManyToManyDescriptor:
    """Descriptor for the reverse side of a ManyToManyField.

    Installed on the target model under ``related_name`` (or the default
    ``<sourcemodel>_set``) by ``_process_pending_relations``.
    """

    def __init__(self, field: ManyToManyField[Any], reverse: bool = True) -> None:
        self.field = field
        self.reverse = reverse

    def __get__(self, obj: Any | None, objtype: type | None = None) -> Any:
        if obj is None:
            return self
        return ManyRelatedManager(field=self.field, instance=obj, reverse=self.reverse)


class ManyRelatedManager(Manager[Any]):
    """Manager for one side of a many-to-many relation.

    Provides Django-style access to related objects:

        await post.tags.all()
        await post.tags.add(tag1, tag2)        # also accepts pk values
        await post.tags.remove(tag1)
        await post.tags.set([tag2, tag3])
        await post.tags.clear()
        post.tags.filter(name__startswith="p")

    With a custom ``through=`` model, only reads are supported — the write
    methods raise :class:`~zeeb_orm.exceptions.NotSupportedError`.
    """

    def __init__(
        self,
        field: ManyToManyField[Any] | None = None,
        instance: Any = None,
        reverse: bool = False,
    ) -> None:
        super().__init__()
        self._field = field
        self._instance = instance
        self._reverse = reverse
        if field is not None and instance is not None:
            _check_instance_usable(instance)
            self.model = field.model if reverse else field.get_target_model()
            self._original_model = self.model

    def __get__(self, obj: Any | None, objtype: type | None = None) -> Any:
        """Return self — already bound to an instance."""
        return self

    # Column helpers

    def _columns(self) -> tuple[str, str]:
        """(my_column, other_column) names in the through table."""
        assert self._field is not None
        if self._reverse:
            return self._field.get_target_column(), self._field.get_source_column()
        return self._field.get_source_column(), self._field.get_target_column()

    @staticmethod
    def _coerce_pk(obj: Any) -> Any:
        """Accept model instances or raw primary-key values."""
        if hasattr(obj, "_state") and hasattr(obj, "pk"):
            return obj.pk
        return obj

    def _coerce_pks(self, objs: tuple[Any, ...] | list[Any]) -> list[Any]:
        """Coerce to unique pk values, rejecting unsaved instances."""
        pks: list[Any] = []
        for obj in objs:
            pk = self._coerce_pk(obj)
            if pk is None:
                raise ValueError(
                    "Cannot use an unsaved object in a many-to-many relation: "
                    f"{obj!r} has no primary key."
                )
            if pk not in pks:
                pks.append(pk)
        return pks

    def _check_writable(self) -> None:
        assert self._field is not None
        if self._field.has_custom_through:
            through_model = self._field.get_through_model()
            through_name = through_model.__name__ if through_model else "through"
            raise NotSupportedError(
                f"Cannot modify a ManyToManyField with an intermediary model: "
                f"use the through model directly ({through_name})."
            )

    # Reads

    def get_queryset(self) -> QuerySet[Any]:
        """QuerySet of related objects via the through table."""
        from zeeb_orm.query.queryset import QuerySet

        assert self._field is not None and self.model is not None
        through = self._field.get_through_table()
        my_col, other_col = self._columns()
        subquery = (
            select(through.c[other_col])
            .where(through.c[my_col] == self._instance.pk)
            .scalar_subquery()
        )
        return QuerySet(self.model).filter(pk__in=subquery)

    # Writes (portable: SELECT existing pairs, then INSERT the missing ones)

    async def add(self, *objs: Any) -> None:
        """Link the given objects (or pk values); duplicates are ignored."""
        self._check_writable()
        pks = self._coerce_pks(objs)
        if not pks:
            return

        assert self._field is not None
        through = self._field.get_through_table()
        my_col, other_col = self._columns()
        my_pk = self._instance.pk

        async def _do(session: Any) -> None:
            result = await session.execute(
                select(through.c[other_col]).where(
                    and_(
                        through.c[my_col] == my_pk,
                        through.c[other_col].in_(pks),
                    )
                )
            )
            existing = {row[0] for row in result.fetchall()}
            missing = [pk for pk in pks if pk not in existing]
            if missing:
                await session.execute(
                    insert(through),
                    [{my_col: my_pk, other_col: pk} for pk in missing],
                )

        await self._run_atomic(_do)

    async def remove(self, *objs: Any) -> None:
        """Unlink the given objects (or pk values)."""
        self._check_writable()
        pks = self._coerce_pks(objs)
        if not pks:
            return

        assert self._field is not None
        through = self._field.get_through_table()
        my_col, other_col = self._columns()

        from zeeb_orm.db.connection import get_session

        async with get_session() as (session, should_commit):
            await session.execute(
                delete(through).where(
                    and_(
                        through.c[my_col] == self._instance.pk,
                        through.c[other_col].in_(pks),
                    )
                )
            )
            if should_commit:
                await session.commit()

    async def clear(self) -> None:
        """Unlink all related objects."""
        self._check_writable()

        assert self._field is not None
        through = self._field.get_through_table()
        my_col, _other_col = self._columns()

        from zeeb_orm.db.connection import get_session

        async with get_session() as (session, should_commit):
            await session.execute(
                delete(through).where(through.c[my_col] == self._instance.pk)
            )
            if should_commit:
                await session.commit()

    async def set(self, objs: list[Any] | tuple[Any, ...], *, clear: bool = False) -> None:
        """Replace the related set.

        With ``clear=False`` (default) only the difference is written:
        stale links are removed, missing ones added.  With ``clear=True``
        all links are removed first, then the new set is inserted.
        """
        self._check_writable()
        target_pks = self._coerce_pks(list(objs))

        assert self._field is not None
        through = self._field.get_through_table()
        my_col, other_col = self._columns()
        my_pk = self._instance.pk

        async def _do(session: Any) -> None:
            if clear:
                await session.execute(
                    delete(through).where(through.c[my_col] == my_pk)
                )
                current: set[Any] = set()
            else:
                result = await session.execute(
                    select(through.c[other_col]).where(through.c[my_col] == my_pk)
                )
                current = {row[0] for row in result.fetchall()}
                stale = current - set(target_pks)
                if stale:
                    await session.execute(
                        delete(through).where(
                            and_(
                                through.c[my_col] == my_pk,
                                through.c[other_col].in_(list(stale)),
                            )
                        )
                    )
            missing = [pk for pk in target_pks if pk not in current]
            if missing:
                await session.execute(
                    insert(through),
                    [{my_col: my_pk, other_col: pk} for pk in missing],
                )

        await self._run_atomic(_do)

    async def create(self, *, validate: bool = True, **kwargs: Any) -> Any:
        """Create a new related object and link it in the same call."""
        self._check_writable()
        obj = await super().create(validate=validate, **kwargs)
        await self.add(obj)
        return obj

    @staticmethod
    async def _run_atomic(operation: Any) -> None:
        """Run ``operation(session)`` inside the active or a new transaction."""
        from zeeb_orm.db.connection import atomic, get_active_session

        session = get_active_session()
        if session is not None:
            await operation(session)
        else:
            async with atomic() as session:
                await operation(session)

    def __repr__(self) -> str:
        model_name = self.model.__name__ if self.model else "unbound"
        return f"<ManyRelatedManager: {model_name}>"


__all__ = ["ManyRelatedManager", "ManyToManyDescriptor"]
