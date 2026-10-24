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

The allow-list may also be a mapping ``{client name: model path}`` - what
:func:`serializer_field_paths` builds from a serializer's output. A client then
names fields by the names the API returns, and :func:`check_field_path` hands
back the path to apply to the queryset (``nick__icontains`` ->
``nickname__icontains`` for ``nick = CharField(source="nickname")``). A column
that is not returned under its own name - one shadowed by a
``SerializerMethodField``, or only returned through another field's
``source`` - is not reachable under that name.

``regex``/``iregex`` lookups are refused unless explicitly allowed: on SQLite
they run Python's ``re`` inside the database, where a crafted pattern
(catastrophic backtracking) ties up the worker.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
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
    allowed: Mapping[str, str] | Collection[str] | str | None,
    *,
    allow_regex: bool = True,
) -> str:
    """Raise :class:`FieldPathError` unless *path* may be used on *model*.

    Returns the path to apply to the queryset: *path* itself, or - when
    *allowed* is a mapping - *path* with its client-facing root replaced by
    the model path it stands for (a leading ``-`` is kept).

    Args:
        model: The queryset's model (``queryset.model``); None falls back to a
            syntactic check of the path.
        path: A filter keyword (``author__name__icontains``) or an ordering
            term (``-created_at``; one leading ``-`` is ignored — ``--id`` is
            refused, as ``order_by`` would fail on it).
        allowed: The allow-list. ``None`` means unrestricted (only the regex
            rule applies); ``"__all__"`` means any field of *model* itself; a
            mapping ``{client name: model path}`` admits exactly its keys (and,
            for a key that maps to itself, the field's other names: a foreign
            key's ``author_id``, ``pk``) and translates them.
        allow_regex: Whether ``regex``/``iregex`` lookups are permitted.
    """
    sign = "-" if path.startswith("-") else ""
    bare = path[1:] if sign else path
    if not bare:
        raise FieldPathError("Empty field name")
    if isinstance(allowed, Mapping):
        translated, targets = _translate(model, bare, allowed)
        try:
            _check_path(model, translated, targets, allow_regex=allow_regex)
        except FieldPathError as exc:
            if translated == bare:
                raise
            # Name the path the client sent, not the column behind it.
            raise FieldPathError(str(exc).replace(translated, bare)) from None
        return sign + translated
    _check_path(model, bare, allowed, allow_regex=allow_regex)
    return path


def serializer_field_paths(serializer_class: Any) -> dict[str, str] | None:
    """``{client name: model path}`` a serializer's output makes queryable.

    Built from what the serializer *returns*, never from the model behind it:
    a ``SerializerMethodField`` (or any other computed output) is left out,
    so a method field named like a column does not make that column
    filterable; a declared field with ``source=`` maps its output name to the
    source path; write-only and unlisted fields are absent. Returns None when
    the serializer has no response schema.
    """
    builder = getattr(serializer_class, "_query_field_map", None)
    if callable(builder):
        return builder()
    schema = getattr(serializer_class, "ResponseSchema", None) or getattr(
        serializer_class, "Schema", None
    )
    names = getattr(schema, "model_fields", None)
    if names:
        return {name: name for name in names}
    return None


def rename_q_paths(q: Any, renames: Mapping[str, str]) -> Any:
    """Rewrite the keyword paths of *q* (in place) through *renames*."""
    if not renames:
        return q
    stack = [q]
    while stack:
        node = stack.pop()
        children = getattr(node, "children", None)
        if not children:
            continue
        for index, child in enumerate(children):
            if isinstance(child, tuple):
                key, value = child
                if key in renames:
                    children[index] = (renames[key], value)
            else:
                stack.append(child)
    return q


def _translate(model: Any, path: str, mapping: Mapping[str, str]) -> tuple[str, set[str]]:
    """``(model path, allow-list)`` for a client *path* under a name mapping."""
    root, sep, rest = path.partition("__")
    target = mapping.get(root)
    if target is not None:
        return target + sep + rest, {target}
    # Another name of a field returned under its own name (``author_id`` for
    # ``author``, ``pk`` for ``id``) - never a column only reachable through
    # some other field's ``source`` or hidden behind a method field.
    if getattr(model, "_meta", None) is not None:
        names = _field_names(model, root)
        for key, value in mapping.items():
            if key == value and key in names:
                return path, {key}
    raise FieldPathError(f"Field '{path}' is not allowed")


def _check_path(
    model: Any,
    path: str,
    allowed: Collection[str] | str | None,
    *,
    allow_regex: bool,
) -> None:
    """:func:`check_field_path` on a model path without a leading ``-``."""
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
