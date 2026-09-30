"""prefetch_related() machinery.

Every lookup is split on ``__`` and resolved level by level: each level is
one query (two for many-to-many: the through table, then the targets) over
all objects of the previous level, so ``prefetch_related("posts__comments")``
costs three queries however many authors and posts there are. A level that
an earlier lookup already fetched is reused, never refetched.

What a level leaves on each object:

* forward ForeignKey / OneToOne — the related object in the FK cache, so
  ``post.author`` returns it without a query;
* reverse ForeignKey / many-to-many — a :class:`PrefetchedRelated` under the
  accessor name: the related objects as a list that also answers the related
  manager API from that list (``await author.posts.all()``, ``.count()``);
* ``Prefetch(..., to_attr="name")`` — a plain list (or the object, for a
  forward FK) under ``name``; the accessor is left alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zeeb_orm.query.queryset import QuerySet

#: Related-manager methods that change the relation: they write through the
#: real manager and then drop the prefetched list from the instance, so the
#: next access reads the database again.
_WRITE_METHODS = frozenset(
    {"add", "remove", "clear", "set", "create", "get_or_create", "update_or_create"}
)


class PrefetchedRelated(list):  # type: ignore[type-arg]
    """Prefetched objects of a to-many relation, usable as its manager.

    It is the list of related objects (iterate it, ``len()`` it, index it)
    and it answers the related manager API off that list where the manager
    would read: ``all()`` returns a queryset already holding the objects, so
    ``await author.posts.all()`` needs no query; ``count()`` and ``exists()``
    answer from the list. Everything else — ``filter()``, ``order_by()``,
    ``get()``, … — goes to the real manager and queries the database, and a
    write (``add``/``remove``/``clear``/``set``/``create``) also drops this
    list from the instance so later reads see the change.
    """

    def __init__(self, objs: list[Any], manager: Any, instance: Any, attr: str) -> None:
        super().__init__(objs)
        self._manager = manager
        self._instance = instance
        self._attr = attr

    # Reads answered from the prefetched objects

    def get_queryset(self) -> QuerySet[Any]:
        """The manager's queryset, already evaluated to the prefetched objects."""
        queryset = self._manager.get_queryset()
        queryset._result_cache = list(self)
        return queryset

    def all(self) -> QuerySet[Any]:
        """Like the manager's ``all()``, answered from the prefetched objects."""
        return self.get_queryset()

    async def count(self) -> int:  # type: ignore[override]
        """Number of prefetched objects (no query)."""
        return len(self)

    async def exists(self) -> bool:
        """Whether any object was prefetched (no query)."""
        return bool(self)

    # Writes go to the database and invalidate the prefetched list

    async def _write(self, name: str, *args: Any, **kwargs: Any) -> Any:
        result = await getattr(self._manager, name)(*args, **kwargs)
        if self._instance.__dict__.get(self._attr) is self:
            del self._instance.__dict__[self._attr]
        return result

    async def add(self, *objs: Any, **kwargs: Any) -> Any:
        return await self._write("add", *objs, **kwargs)

    async def remove(self, *objs: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        return await self._write("remove", *objs, **kwargs)

    async def clear(self, **kwargs: Any) -> Any:  # type: ignore[override]
        return await self._write("clear", **kwargs)

    async def set(self, objs: Any, **kwargs: Any) -> Any:
        return await self._write("set", objs, **kwargs)

    async def create(self, **kwargs: Any) -> Any:
        return await self._write("create", **kwargs)

    async def get_or_create(self, **kwargs: Any) -> Any:
        return await self._write("get_or_create", **kwargs)

    async def update_or_create(self, **kwargs: Any) -> Any:
        return await self._write("update_or_create", **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Only reached for names neither list nor this class defines.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._manager, name)

    def __repr__(self) -> str:
        return f"<PrefetchedRelated {list.__repr__(self)}>"


def _unique(objs: list[Any]) -> list[Any]:
    """``objs`` without repeated objects (by identity), order kept."""
    seen: set[int] = set()
    out = []
    for obj in objs:
        if id(obj) not in seen:
            seen.add(id(obj))
            out.append(obj)
    return out


async def prefetch_related_objects(
    instances: list[Any], model: type, lookups: list[Any], using: str | None
) -> None:
    """Resolve every ``prefetch_related()`` lookup onto ``instances``."""
    from zeeb_orm.exceptions import FieldError
    from zeeb_orm.models.relations import resolve_relation
    from zeeb_orm.query.queryset import Prefetch

    if not instances:
        return

    # path prefix -> (objects of that level, their model)
    done: dict[str, tuple[list[Any], type]] = {}

    for lookup in lookups:
        if isinstance(lookup, Prefetch):
            path, custom_qs, to_attr = lookup.lookup, lookup.queryset, lookup.to_attr
        elif isinstance(lookup, str):
            path, custom_qs, to_attr = lookup, None, None
        else:
            raise TypeError(
                "prefetch_related() lookups must be strings or Prefetch objects, "
                f"got {type(lookup).__name__}."
            )

        parts = path.split("__")
        level: list[Any] = instances
        level_model: type = model
        for depth, part in enumerate(parts):
            prefix = "__".join(parts[: depth + 1])
            last = depth == len(parts) - 1
            plain = not last or (custom_qs is None and to_attr is None)
            if plain and prefix in done:
                level, level_model = done[prefix]
                continue

            relation = resolve_relation(level_model, part)
            if relation is None:
                raise FieldError(
                    f"Cannot find {part!r} on {level_model.__name__} object, "
                    f"{path!r} is an invalid parameter to prefetch_related()."
                )
            level = await _prefetch_level(
                level,
                relation,
                custom_qs if last else None,
                to_attr if last else None,
                using,
            )
            level_model = relation.target_model
            if not (last and to_attr):
                done[prefix] = (level, level_model)
            if not level:
                break


async def _prefetch_level(
    instances: list[Any],
    relation: Any,
    custom_qs: QuerySet[Any] | None,
    to_attr: str | None,
    using: str | None,
) -> list[Any]:
    """Fetch one relation hop for ``instances``; return the related objects."""
    if relation.kind in ("fk", "o2o"):
        return await _prefetch_forward(instances, relation, custom_qs, to_attr, using)
    if relation.kind in ("reverse_fk", "reverse_o2o"):
        return await _prefetch_reverse_fk(instances, relation, custom_qs, to_attr, using)
    return await _prefetch_m2m(instances, relation, custom_qs, to_attr, using)


def _base_queryset(relation: Any, custom_qs: QuerySet[Any] | None, using: str | None) -> Any:
    from zeeb_orm.query.queryset import QuerySet

    queryset = custom_qs._clone() if custom_qs is not None else QuerySet(relation.target_model)
    queryset._db_alias = using
    return queryset


def _assign_many(instance: Any, relation: Any, objs: list[Any], to_attr: str | None) -> None:
    """Attach a to-many result: ``to_attr`` list, or the manager-backed list."""
    if to_attr:
        setattr(instance, to_attr, list(objs))
        return
    name = relation.accessor_name
    # Drop an earlier prefetch so the descriptor hands out a real manager.
    instance.__dict__.pop(name, None)
    manager = getattr(instance, name)
    instance.__dict__[name] = PrefetchedRelated(objs, manager, instance, name)


async def _prefetch_forward(
    instances: list[Any],
    relation: Any,
    custom_qs: QuerySet[Any] | None,
    to_attr: str | None,
    using: str | None,
) -> list[Any]:
    name = relation.fk_field.name
    cache_attr = f"_cache_{name}"

    # Objects select_related() already attached are reused, not refetched.
    related_map: dict[Any, Any] = {}
    if custom_qs is None:
        for inst in instances:
            cached = inst.__dict__.get(cache_attr)
            if cached is not None:
                related_map[cached.pk] = cached

    wanted = {
        fk_id
        for inst in instances
        if (fk_id := getattr(inst, f"_field_{name}_id", None)) is not None
        and fk_id not in related_map
    }
    if wanted:
        queryset = _base_queryset(relation, custom_qs, using).filter(pk__in=list(wanted))
        for obj in await queryset._fetch_all():
            related_map[obj.pk] = obj

    related: list[Any] = []
    for inst in instances:
        fk_id = getattr(inst, f"_field_{name}_id", None)
        obj = related_map.get(fk_id) if fk_id is not None else None
        if to_attr:
            setattr(inst, to_attr, obj)
        elif obj is not None:
            setattr(inst, cache_attr, obj)
        if obj is not None:
            related.append(obj)
    return _unique(related)


async def _prefetch_reverse_fk(
    instances: list[Any],
    relation: Any,
    custom_qs: QuerySet[Any] | None,
    to_attr: str | None,
    using: str | None,
) -> list[Any]:
    fk_name = relation.fk_field.name
    parent_pks = [inst.pk for inst in instances if inst.pk is not None]
    queryset = _base_queryset(relation, custom_qs, using).filter(
        **{f"{fk_name}__in": parent_pks}
    )
    related = await queryset._fetch_all()

    by_parent = {inst.pk: inst for inst in instances}
    grouped: dict[Any, list[Any]] = {}
    for obj in related:
        fk_value = getattr(obj, f"_field_{fk_name}_id", None)
        grouped.setdefault(fk_value, []).append(obj)
        parent = by_parent.get(fk_value)
        if parent is not None:
            # The child's own FK now resolves to its parent without a query.
            setattr(obj, f"_cache_{fk_name}", parent)

    for inst in instances:
        _assign_many(inst, relation, grouped.get(inst.pk, []), to_attr)
    return list(related)


async def _prefetch_m2m(
    instances: list[Any],
    relation: Any,
    custom_qs: QuerySet[Any] | None,
    to_attr: str | None,
    using: str | None,
) -> list[Any]:
    """One IN-query over the through table, one for the related objects."""
    from sqlalchemy import select

    from zeeb_orm.db.connection import get_session

    m2m_field = relation.fk_field
    through = m2m_field.get_through_table()
    if relation.kind == "m2m":
        my_col = m2m_field.get_source_column()
        other_col = m2m_field.get_target_column()
    else:  # reverse_m2m
        my_col = m2m_field.get_target_column()
        other_col = m2m_field.get_source_column()

    parent_pks = [inst.pk for inst in instances if inst.pk is not None]
    stmt = select(through.c[my_col], through.c[other_col]).where(
        through.c[my_col].in_(parent_pks)
    )
    async with get_session(using) as (session, _):
        pairs = (await session.execute(stmt)).fetchall()

    parents_of: dict[Any, list[Any]] = {}
    for mine, other in pairs:
        parents_of.setdefault(other, []).append(mine)

    related: list[Any] = []
    if parents_of:
        queryset = _base_queryset(relation, custom_qs, using).filter(
            pk__in=list(parents_of)
        )
        related = await queryset._fetch_all()

    # Walk the related objects in the queryset's order (explicit order_by or
    # Meta.ordering), not in the through table's order.
    grouped: dict[Any, list[Any]] = {}
    for obj in related:
        for parent_pk in parents_of.get(obj.pk, []):
            grouped.setdefault(parent_pk, []).append(obj)

    for inst in instances:
        _assign_many(inst, relation, grouped.get(inst.pk, []), to_attr)
    return list(related)


__all__ = ["PrefetchedRelated", "prefetch_related_objects"]
