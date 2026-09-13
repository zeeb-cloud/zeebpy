"""JOIN bookkeeping for related-field traversal in QuerySets.

A :class:`JoinContext` is created per statement build (it is never stored on
the QuerySet, preserving ``_clone()`` semantics and result caching).  Both
``select_related`` and ``__``-path traversal in filter/exclude/order_by/values
register their joins here, so a path that appears in both places shares a
single JOIN.

Aliases follow the ``_sr_{path_with_underscores}_{last_part}`` naming scheme
used by ``select_related`` column labelling.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import outerjoin

from zeeb_orm.exceptions import FieldError
from zeeb_orm.models.relations import RelationInfo, resolve_relation


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
    """

    path: str
    alias: Any
    target_model: type
    relation: RelationInfo
    is_multi: bool
    onclause: Any = field(repr=False, default=None)


class JoinContext:
    """Collects the JOINs needed by a single statement build."""

    def __init__(self, model: type, base_table: Any) -> None:
        self.model = model
        self.base_table = base_table
        #: Ordered mapping of path prefix -> JoinInfo (parents come first).
        self.joins: dict[str, JoinInfo] = {}

    @property
    def has_joins(self) -> bool:
        return bool(self.joins)

    @property
    def has_multi_joins(self) -> bool:
        """True when any registered join can multiply result rows."""
        return any(info.is_multi for info in self.joins.values())

    def ensure_join(self, path_parts: Sequence[str]) -> JoinInfo:
        """Register (or reuse) the JOIN chain for ``path_parts``.

        Returns the JoinInfo of the last hop.  Raises FieldError when a part
        does not resolve as a relation.
        """
        if not path_parts:
            raise FieldError("ensure_join() requires at least one path part")

        current_model = self.model
        current_table = self.base_table
        info: JoinInfo | None = None

        for i, part in enumerate(path_parts):
            path = "__".join(path_parts[: i + 1])
            info = self.joins.get(path)
            if info is not None:
                current_model = info.target_model
                current_table = info.alias
                continue

            relation = resolve_relation(current_model, part)
            if relation is None:
                raise FieldError(
                    f"'{part}' is not a relation on {current_model.__name__} "
                    f"(in path {'__'.join(path_parts)!r})"
                )

            target_model = relation.target_model
            target_table = target_model._get_table()  # type: ignore[attr-defined]
            alias_name = f"_sr_{path.replace('__', '_')}_{part}"
            alias = target_table.alias(alias_name)

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
                through_alias = through.alias(
                    f"_sr_{path.replace('__', '_')}_through"
                )
                if relation.kind == "m2m":
                    near_col = m2m_field.get_source_column()
                    far_col = m2m_field.get_target_column()
                else:
                    near_col = m2m_field.get_target_column()
                    far_col = m2m_field.get_source_column()

                src_meta = current_model._meta  # type: ignore[attr-defined]
                src_pk_name = src_meta.pk.db_column or src_meta.pk_name

                # Register the intermediate hop under a synthetic key
                # ("#" never appears in parsed paths) so apply() chains
                # base -> through -> target in insertion order.
                through_path = f"{path}__#through"
                self.joins[through_path] = JoinInfo(
                    path=through_path,
                    alias=through_alias,
                    target_model=target_model,
                    relation=relation,
                    is_multi=True,
                    onclause=(
                        current_table.c[src_pk_name] == through_alias.c[near_col]
                    ),
                )

                pk_col_name = meta.pk.db_column or meta.pk_name
                onclause = through_alias.c[far_col] == alias.c[pk_col_name]
                is_multi = True
            else:
                raise FieldError(
                    f"Traversal across {relation.kind!r} relations is not "
                    f"supported yet (path {'__'.join(path_parts)!r})"
                )

            info = JoinInfo(
                path=path,
                alias=alias,
                target_model=target_model,
                relation=relation,
                is_multi=is_multi,
                onclause=onclause,
            )
            self.joins[path] = info
            current_model = target_model
            current_table = alias

        assert info is not None
        return info

    def column(self, path_parts: Sequence[str], field_name: str) -> Any:
        """Return the aliased column for ``field_name`` at ``path_parts``."""
        info = self.ensure_join(path_parts)
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


__all__ = ["JoinContext", "JoinInfo"]
