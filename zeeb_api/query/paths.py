"""
Allow-list checks for client-supplied field paths (filters and ordering).

A client names fields in ``POST /query`` filters (``Q(author__name='x')``), in
its ``order_by`` and in ``?ordering=``. Checking only the first segment of such
a path is not enough: with ``author`` exposed, ``Q(author__password__startswith=
'$2b$')`` or ``order_by=["author__password"]`` reads a column of the related
model one character (or one sort position) at a time. So the whole path is
resolved against the model, and every hop must be allowed:

- a path that stays on the model (``title``, ``title__icontains``,
  ``created_at__year__gte``, ``author`` / ``author_id`` for a foreign key) needs
  its field in the allow-list;
- a path that follows a relation needs the **full** field path listed
  (``"author__name"``). The one exception is the related primary key right
  behind an allowed relation (``author__id``, ``tags__in=[...]``): that value
  is what the relation field already exposes.

``regex``/``iregex`` lookups are refused unless explicitly allowed: on SQLite
they run Python's ``re`` inside the database, where a crafted pattern
(catastrophic backtracking) ties up the worker.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

REGEX_LOOKUPS = frozenset({"regex", "iregex"})

#: Sentinel an allow-list may be set to (``ordering_fields = "__all__"``):
#: every field **of the model itself** — never a path through a relation.
ALL_FIELDS = "__all__"


class FieldPathError(ValueError):
    """A client-supplied field path that may not be used."""


def check_field_path(
    model: Any,
    path: str,
    allowed: Collection[str] | str | None,
    *,
    allow_regex: bool = True,
) -> None:
    """Raise :class:`FieldPathError` unless *path* may be used on *model*.

    Args:
        model: The queryset's model (``queryset.model``); None falls back to a
            syntactic check of the path.
        path: A filter keyword (``author__name__icontains``) or an ordering
            term (``-created_at``; the sign is ignored).
        allowed: The allow-list. ``None`` means unrestricted (only the regex
            rule applies); ``"__all__"`` means any field of *model* itself.
        allow_regex: Whether ``regex``/``iregex`` lookups are permitted.
    """
    path = path.lstrip("-")
    if not path:
        raise FieldPathError("Empty field name")

    if model is None or getattr(model, "_meta", None) is None:
        _check_syntactically(path, allowed, allow_regex=allow_regex)
        return

    from zeeb_orm.exceptions import FieldError
    from zeeb_orm.query.q import parse_path

    try:
        relation_parts, field_name, _transform, lookup = parse_path(model, path)
    except FieldError as exc:
        raise FieldPathError(str(exc)) from None

    if lookup in REGEX_LOOKUPS and not allow_regex:
        raise FieldPathError(f"The '{lookup}' lookup is not allowed ('{path}')")
    if allowed is None:
        return

    target = _walk(model, relation_parts)
    names = _field_names(target, field_name)

    if not relation_parts:
        if allowed == ALL_FIELDS:
            if _is_model_field(target, field_name):
                return
        elif names & set(allowed):
            return
        raise FieldPathError(f"Field '{path}' is not allowed")

    if allowed != ALL_FIELDS:
        allowed_set = set(allowed)
        prefix = "__".join(relation_parts)
        if any(f"{prefix}__{name}" in allowed_set for name in names):
            return
        # The related primary key right behind an allowed relation is the
        # value that relation already exposes (author -> author__id).
        if (
            len(relation_parts) == 1
            and relation_parts[0] in allowed_set
            and names & _pk_names(target)
        ):
            return
    raise FieldPathError(
        f"Field '{path}' follows a relation; list the full path in the allow-list to permit it"
    )


def _walk(model: Any, relation_parts: list[str]) -> Any:
    """The model *relation_parts* lead to from *model*."""
    from zeeb_orm.models.relations import resolve_relation

    current = model
    for part in relation_parts:
        relation = resolve_relation(current, part)
        if relation is None:  # pragma: no cover - parse_path resolved it
            return None
        current = relation.target_model
    return current


def _field_names(model: Any, field_name: str) -> set[str]:
    """Every name a client may know *field_name* by (``author_id`` / ``author``)."""
    names = {field_name}
    meta = getattr(model, "_meta", None)
    if meta is None:
        return names
    if field_name == "pk" and meta.pk is not None:
        names.add(meta.pk.name)
    field = meta.get_field(field_name) or meta.get_field_by_column(field_name)
    if field is not None:
        names.add(field.name)
        column = getattr(field, "db_column", None) or getattr(field, "column", None)
        if isinstance(column, str):
            names.add(column)
    return names


def _pk_names(model: Any) -> set[str]:
    meta = getattr(model, "_meta", None)
    names = {"pk"}
    if meta is not None and meta.pk is not None:
        names.add(meta.pk.name)
    return names


def _is_model_field(model: Any, field_name: str) -> bool:
    meta = getattr(model, "_meta", None)
    if meta is None:
        return False
    if field_name == "pk":
        return True
    return meta.get_field(field_name) is not None or (
        meta.get_field_by_column(field_name) is not None
    )


def _check_syntactically(
    path: str, allowed: Collection[str] | str | None, *, allow_regex: bool
) -> None:
    """Fallback without a model: strip a trailing lookup/transform, then compare."""
    from zeeb_orm.query.q import LOOKUP_EXPRESSIONS
    from zeeb_orm.query.transforms import DATETIME_TRANSFORMS

    parts = path.split("__")
    if len(parts) > 1 and parts[-1] in LOOKUP_EXPRESSIONS:
        if parts[-1] in REGEX_LOOKUPS and not allow_regex:
            raise FieldPathError(f"The '{parts[-1]}' lookup is not allowed ('{path}')")
        parts = parts[:-1]
    if len(parts) > 1 and parts[-1] in DATETIME_TRANSFORMS:
        parts = parts[:-1]
    if allowed is None:
        return
    if len(parts) == 1:
        if allowed == ALL_FIELDS or parts[0] in allowed:
            return
        raise FieldPathError(f"Field '{path}' is not allowed")
    if allowed != ALL_FIELDS and "__".join(parts) in allowed:
        return
    raise FieldPathError(
        f"Field '{path}' follows a relation; list the full path in the allow-list to permit it"
    )
