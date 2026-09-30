"""JOIN bookkeeping for related-field traversal in QuerySets.

A :class:`JoinContext` is created per statement build (it is never stored on
the QuerySet, preserving ``_clone()`` semantics and result caching).  Both
``select_related`` and ``__``-path traversal in filter/exclude/order_by/values
register their joins here, so a path that appears in both places shares a
single JOIN.

Single-valued hops (forward FK/O2O, reverse O2O) are always shared.
Multi-valued hops (reverse FK, M2M) follow Django's rule instead: conditions
inside one ``filter()`` call share a join — they must hold for the *same*
related row — while every further ``filter()`` call gets a join of its own.
Callers express that with ``scope``: a join registered without a scope
(annotations, ordering, values) is reused by whoever asks next, and the first
filter scope to reach it claims it.

Aliases follow the ``_sr_{path_with_underscores}_{last_part}`` naming scheme
used by ``select_related`` column labelling; a second join of the same path
gets a numeric suffix.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import outerjoin

from zeeb_orm.exceptions import FieldError
from zeeb_orm.models.relations import RelationInfo, resolve_relation

#: Relation kinds that can yield several rows per source row.
MULTI_VALUED_KINDS = frozenset({"reverse_fk", "m2m", "reverse_m2m"})


def is_multi_valued_path(model: type, path_parts: Sequence[str]) -> bool:
    """True when any hop of ``path_parts`` (from ``model``) is multi-valued."""
    current = model
    for part in path_parts:
        relation = resolve_relation(current, part)
        if relation is None:
            return False
        if relation.kind in MULTI_VALUED_KINDS:
            return True
        current = relation.target_model
    return False


@dataclass
class JoinInfo:
    """A single registered JOIN for a relation path prefix.

    Attributes:
        path: Full ``__``-joined path prefix, e.g. ``"author__profile"``.
        alias: The aliased SQLAlchemy Table joined for this path.
        target_model: The model class the alias maps to.
        relation: The RelationInfo describing the final hop.
        is_multi: True when the hop can yield multiple rows per source row
            (reverse FK), which may produce duplicate results.
        onclause: SQLAlchemy join condition connecting the parent table or
            alias to ``alias``.
        key: Unique registry key (parent key plus hop, plus a suffix when
            the same path is joined more than once).
        parent_key: Key of the join this hop starts from (``""`` = base).
        part: The relation accessor of this hop.
        scope: The filter scope that owns a multi-valued join (``None`` =
            unclaimed).
    """

    path: str
    alias: Any
    target_model: type
    relation: RelationInfo
    is_multi: bool
    onclause: Any = field(repr=False, default=None)
    key: str = ""
    parent_key: str = ""
    part: str = ""
    scope: Hashable | None = None


class JoinContext:
    """Collects the JOINs needed by a single statement build."""

    def __init__(self, model: type, base_table: Any, *, alias_prefix: str = "_sr") -> None:
        self.model = model
        self.base_table = base_table
        self.alias_prefix = alias_prefix
        #: Ordered mapping of key -> JoinInfo (parents come first).
        self.joins: dict[str, JoinInfo] = {}
        self._alias_names: set[str] = set()

    @property
    def has_joins(self) -> bool:
        return bool(self.joins)

    @property
    def has_multi_joins(self) -> bool:
        """True when any registered join can multiply result rows."""
        return any(info.is_multi for info in self.joins.values())

    def _find_hop(
        self, parent_key: str, part: str, multi: bool, scope: Hashable | None
    ) -> JoinInfo | None:
        """An existing join of ``part`` from ``parent_key`` this caller may reuse."""
        candidates = [
            info
            for info in self.joins.values()
            if info.parent_key == parent_key and info.part == part
        ]
        if not candidates:
            return None
        if not multi or scope is None:
            # Single-valued hops are always shared; unscoped callers reuse
            # the most recent join of the path (Django reuses the last one).
            return candidates[-1]
        for info in candidates:
            if info.scope == scope:
                return info
        for info in candidates:
            if info.scope is None:
                info.scope = scope  # claim an unowned join
                self._claim_through(info, scope)
                return info
        return None

    def _claim_through(self, info: JoinInfo, scope: Hashable) -> None:
        through = self.joins.get(f"{info.key}__#through")
        if through is not None:
            through.scope = scope

    def ensure_join(self, path_parts: Sequence[str], *, scope: Hashable | None = None) -> JoinInfo:
        """Register (or reuse) the JOIN chain for ``path_parts``.

        ``scope`` identifies the ``filter()`` call asking: multi-valued hops
        are only shared within one scope (see the module docstring).
        Returns the JoinInfo of the last hop.  Raises FieldError when a part
        does not resolve as a relation.
        """
        if not path_parts:
            raise FieldError("ensure_join() requires at least one path part")

        current_model = self.model
        current_table = self.base_table
        parent_key = ""
        info: JoinInfo | None = None

        for i, part in enumerate(path_parts):
            path = "__".join(path_parts[: i + 1])
            relation = resolve_relation(current_model, part)
            if relation is None:
                raise FieldError(
                    f"'{part}' is not a relation on {current_model.__name__} "
                    f"(in path {'__'.join(path_parts)!r})"
                )
            multi = relation.kind in MULTI_VALUED_KINDS

            info = self._find_hop(parent_key, part, multi, scope)
            if info is None:
                info = self._register_hop(
                    path,
                    part,
                    relation,
                    parent_key,
                    current_model,
                    current_table,
                    scope if multi else None,
                )
            parent_key = info.key
            current_model = info.target_model
            current_table = info.alias

        assert info is not None
        return info

    def _register_hop(
        self,
        path: str,
        part: str,
        relation: RelationInfo,
        parent_key: str,
        current_model: type,
        current_table: Any,
        scope: Hashable | None,
    ) -> JoinInfo:
        """Create the alias (and M2M through alias) for one new hop."""
        key = f"{parent_key}__{part}" if parent_key else part
        if key in self.joins:
            n = 2
            while f"{key}#{n}" in self.joins:
                n += 1
            key = f"{key}#{n}"

        target_model = relation.target_model
        target_table = target_model._get_table()  # type: ignore[attr-defined]
        alias_stem = f"{self.alias_prefix}_{path.replace('__', '_')}"
        alias = target_table.alias(self._unique_alias(f"{alias_stem}_{part}"))

        meta = target_model._meta  # type: ignore[attr-defined]
        if relation.kind in ("fk", "o2o"):
            # src.fk_column -> alias.pk
            src_col = current_table.c[relation.fk_column]
            pk_col_name = meta.pk.db_column or meta.pk_name
            onclause = src_col == alias.c[pk_col_name]
            is_multi = False
        elif relation.kind in ("reverse_fk", "reverse_o2o"):
            # src.pk -> alias.fk_column
            src_meta = current_model._meta  # type: ignore[attr-defined]
            src_pk_name = src_meta.pk.db_column or src_meta.pk_name
            onclause = current_table.c[src_pk_name] == alias.c[relation.fk_column]
            is_multi = relation.kind == "reverse_fk"
        elif relation.kind in ("m2m", "reverse_m2m"):
            # Two hops: src.pk -> through.near_col / through.far_col -> alias.pk
            m2m_field = relation.fk_field
            through = m2m_field.get_through_table()
            through_alias = through.alias(self._unique_alias(f"{alias_stem}_through"))
            if relation.kind == "m2m":
                near_col = m2m_field.get_source_column()
                far_col = m2m_field.get_target_column()
            else:
                near_col = m2m_field.get_target_column()
                far_col = m2m_field.get_source_column()

            src_meta = current_model._meta  # type: ignore[attr-defined]
            src_pk_name = src_meta.pk.db_column or src_meta.pk_name

            # Register the intermediate hop under a synthetic key
            # ("#through" never appears in parsed paths) so apply() chains
            # base -> through -> target in insertion order.
            through_key = f"{key}__#through"
            self.joins[through_key] = JoinInfo(
                path=f"{path}__#through",
                alias=through_alias,
                target_model=target_model,
                relation=relation,
                is_multi=True,
                onclause=current_table.c[src_pk_name] == through_alias.c[near_col],
                key=through_key,
                parent_key="#through",  # never matched by _find_hop
                part=part,
                scope=scope,
            )

            pk_col_name = meta.pk.db_column or meta.pk_name
            onclause = through_alias.c[far_col] == alias.c[pk_col_name]
            is_multi = True
        else:
            raise FieldError(
                f"Traversal across {relation.kind!r} relations is not supported yet (path {path!r})"
            )

        info = JoinInfo(
            path=path,
            alias=alias,
            target_model=target_model,
            relation=relation,
            is_multi=is_multi,
            onclause=onclause,
            key=key,
            parent_key=parent_key,
            part=part,
            scope=scope,
        )
        self.joins[key] = info
        return info

    def _unique_alias(self, name: str) -> str:
        """``name``, or ``name_2``/``name_3``… when a join already uses it."""
        candidate, n = name, 2
        while candidate in self._alias_names:
            candidate = f"{name}_{n}"
            n += 1
        self._alias_names.add(candidate)
        return candidate

    def column(
        self, path_parts: Sequence[str], field_name: str, *, scope: Hashable | None = None
    ) -> Any:
        """Return the aliased column for ``field_name`` at ``path_parts``."""
        info = self.ensure_join(path_parts, scope=scope)
        meta = info.target_model._meta  # type: ignore[attr-defined]
        if field_name == "pk":
            col_name = meta.pk.db_column or meta.pk_name
        else:
            model_field = meta.get_field(field_name)
            col_name = (
                (model_field.db_column or model_field.name)
                if model_field is not None
                else field_name
            )
        column = info.alias.c.get(col_name)
        if column is None:
            raise FieldError(
                f"Unknown field '{field_name}' on {info.target_model.__name__} "
                f"(in path {'__'.join(path_parts)!r})"
            )
        return column

    def apply(self, base_table: Any) -> Any:
        """Build the FROM clause: ``base_table`` outer-joined to all aliases.

        Parents are guaranteed to be registered before children (insertion
        order of ``ensure_join``), so a simple left-to-right chain works.
        """
        join_target = base_table
        for info in self.joins.values():
            join_target = outerjoin(join_target, info.alias, info.onclause)
        return join_target


__all__ = ["JoinContext", "JoinInfo", "MULTI_VALUED_KINDS", "is_multi_valued_path"]
