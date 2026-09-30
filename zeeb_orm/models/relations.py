"""Relation bookkeeping: pending reverse relations and relation resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from zeeb_orm.models.base import Model

# Pending relations to set up (for forward references)
_pending_relations: list[tuple[type, Any]] = []

# Pending many-to-many fields awaiting reverse-accessor installation
# (mirrors _pending_relations — the target may not be registered yet).
_pending_m2m: list[tuple[type, Any]] = []


def related_accessor_name(field: Any, source_model: type) -> str | None:
    """The reverse accessor a relation installs on its target, or ``None``.

    ``related_name`` may use Django's ``%(class)s`` and ``%(app_label)s``
    placeholders (lowercased), which is how an abstract base gives each
    concrete subclass its own accessor. A name ending in ``+`` hides the
    reverse relation. Without a ``related_name`` the accessor is
    ``<sourcemodel>_set``.
    """
    name = field.related_name
    if name is None:
        return f"{source_model.__name__.lower()}_set"
    if "%(" in name:
        meta = getattr(source_model, "_meta", None)
        name = name % {
            "class": source_model.__name__.lower(),
            "model_name": source_model.__name__.lower(),
            "app_label": (getattr(meta, "app_label", "") or "").lower(),
        }
    if name.endswith("+"):
        return None
    return name


def _descriptor_source(descriptor: Any) -> type | None:
    """The model whose relation installed ``descriptor`` (None if not ours)."""
    from zeeb_orm.models.manager import RelatedManagerDescriptor
    from zeeb_orm.models.related_m2m import ManyToManyDescriptor

    if isinstance(descriptor, RelatedManagerDescriptor):
        return descriptor.related_model
    if isinstance(descriptor, ManyToManyDescriptor):
        return descriptor.field.model
    return None


def _check_accessor_free(
    target_model: type, accessor: str, source_model: type, field: Any
) -> str | None:
    """Return a clash message when ``accessor`` is taken on ``target_model``.

    Free means: not set at all, or set by a relation of a model that has
    since been replaced in the registry (a module re-import) or that has
    the same registry label as ``source_model`` (the same model, redefined).
    Anything else — a field, a method, another live model's reverse
    accessor — is a clash, reported instead of silently leaving one of the
    two relations without its accessor.
    """
    import inspect

    from zeeb_orm.models.base import _model_registry, model_label

    existing = inspect.getattr_static(target_model, accessor, _MISSING)
    if existing is _MISSING:
        return None
    other = _descriptor_source(existing)
    if other is not None:
        if getattr(existing, "field", None) is field:
            return None
        other_label = model_label(other)
        if other_label == model_label(source_model):
            return None
        if _model_registry.get(other_label) is not other:
            return None  # stale: that model was unregistered or replaced
        what = f"the reverse accessor of {other_label}"
    else:
        what = "an existing attribute"
    return (
        f"Reverse accessor {target_model.__name__}.{accessor} for "
        f"{model_label(source_model)}.{field.name} clashes with {what}. "
        f"Add or change related_name on {model_label(source_model)}.{field.name} "
        f"(e.g. related_name=\"{source_model.__name__.lower()}_{field.name}s\", or "
        f"\"%(class)s_{field.name}s\" on an abstract base)."
    )


_MISSING: Any = object()


def _process_pending_relations(resolve_callables: bool = False) -> None:
    """Process pending reverse relations.

    Args:
        resolve_callables: If True, also resolve callable FK targets
            (e.g. ``get_user_model``).  Set to True after all models
            and settings have been loaded (inside ``_register_models``).

    Raises:
        FieldError: when a reverse accessor clashes with an attribute of its
            target model — a field, a method, or another model's reverse
            accessor. Every other pending relation is still processed first.
    """
    from zeeb_orm.exceptions import FieldError
    from zeeb_orm.models.base import AmbiguousModelReferenceError
    from zeeb_orm.models.manager import RelatedManagerDescriptor
    from zeeb_orm.models.related_m2m import ManyToManyDescriptor

    still_pending = []
    clashes: list[str] = []

    for source_model, fk_field in _pending_relations:
        try:
            # Callable FK targets (e.g. get_user_model) may not be resolvable
            # during import — keep them pending until _register_models() runs
            if (not resolve_callables
                    and callable(fk_field.to)
                    and not isinstance(fk_field.to, type)):
                still_pending.append((source_model, fk_field))
                continue
            target_model = fk_field.get_target_model()
        except (KeyError, AttributeError, AmbiguousModelReferenceError):
            # Target model not yet registered (or the bare name is ambiguous,
            # which build_table()/queries report when the relation is used)
            still_pending.append((source_model, fk_field))
            continue

        related_name = related_accessor_name(fk_field, source_model)
        if related_name is None:
            continue
        clash = _check_accessor_free(target_model, related_name, source_model, fk_field)
        if clash:
            clashes.append(clash)
            continue

        # Add the RelatedManager descriptor to the target model
        descriptor = RelatedManagerDescriptor(
            related_model=source_model,
            field_name=related_name,
            fk_field_name=f"{fk_field.name}_id",
            field=fk_field,
        )
        setattr(target_model, related_name, descriptor)

    _pending_relations.clear()
    _pending_relations.extend(still_pending)

    # Many-to-many reverse accessors (no table creation here — the join
    # table is built lazily by build_table()/get_through_table() so model
    # imports never pollute the shared metadata).
    still_pending_m2m = []
    for source_model, m2m_field in _pending_m2m:
        try:
            target_model = m2m_field.get_target_model()
        except (KeyError, AttributeError, AmbiguousModelReferenceError):
            # Target model not yet registered
            still_pending_m2m.append((source_model, m2m_field))
            continue

        related_name = related_accessor_name(m2m_field, source_model)
        if related_name is None:
            continue
        clash = _check_accessor_free(target_model, related_name, source_model, m2m_field)
        if clash:
            clashes.append(clash)
            continue

        setattr(
            target_model,
            related_name,
            ManyToManyDescriptor(m2m_field, reverse=True),
        )

    _pending_m2m.clear()
    _pending_m2m.extend(still_pending_m2m)

    if clashes:
        raise FieldError("\n".join(clashes))


RelationKind = Literal["fk", "o2o", "reverse_fk", "reverse_o2o", "m2m", "reverse_m2m"]


@dataclass
class RelationInfo:
    """Describes a single relation hop from ``source_model`` via ``accessor_name``.

    Attributes:
        kind: Relation kind ("fk", "o2o", "reverse_fk", "reverse_o2o",
            "m2m", "reverse_m2m").
        source_model: The model the traversal starts from.
        target_model: The model the traversal ends on (the table to join).
        fk_field: The ForeignKeyField implementing the relation.  For
            forward relations it lives on ``source_model``; for reverse
            relations it lives on ``target_model``.
        accessor_name: The attribute name used to traverse the relation
            (field name for forward, related_name / ``<model>_set`` for
            reverse).
        fk_column: The database column implementing the relation,
            e.g. ``"author_id"``.
    """

    kind: RelationKind
    source_model: type
    target_model: type
    fk_field: Any | None
    accessor_name: str
    fk_column: str


def resolve_relation(model: type, name: str) -> RelationInfo | None:
    """Resolve ``name`` as a relation accessor on ``model``.

    Handles forward FK/O2O fields, reverse FK/O2O accessors
    (``related_name`` or the default ``<modelname>_set``), forward M2M
    fields and reverse M2M accessors.

    For M2M relations ``fk_column`` holds the through-table column
    referencing ``source_model`` (the side traversal starts from).

    Returns None when ``name`` is not a relation on ``model``.
    """
    from zeeb_orm.models.fields import OneToOneField

    # 1. Forward FK / O2O
    for fk_field in getattr(model, "_fk_fields", []):
        if fk_field.name != name:
            continue
        try:
            target_model = fk_field.get_target_model()
        except (KeyError, AttributeError):
            return None
        kind: RelationKind = "o2o" if isinstance(fk_field, OneToOneField) else "fk"
        return RelationInfo(
            kind=kind,
            source_model=model,
            target_model=target_model,
            fk_field=fk_field,
            accessor_name=name,
            fk_column=fk_field.db_column or f"{name}_id",
        )

    # 1b. Forward M2M
    for m2m_field in getattr(model, "_m2m_fields", []):
        if m2m_field.name != name:
            continue
        try:
            target_model = m2m_field.get_target_model()
        except (KeyError, AttributeError):
            return None
        return RelationInfo(
            kind="m2m",
            source_model=model,
            target_model=target_model,
            fk_field=m2m_field,
            accessor_name=name,
            fk_column=m2m_field.get_source_column(),
        )

    # 2. Reverse FK / O2O: scan the registry for FKs targeting `model`
    from zeeb_orm.models.base import _model_registry

    # Snapshot: resolving a string-referenced target below can import a module
    # and register further models, which would invalidate a live iterator.
    for model_cls in list(_model_registry.values()):
        for fk_field in getattr(model_cls, "_fk_fields", []):
            try:
                target = fk_field.get_target_model()
            except (KeyError, AttributeError):
                continue
            if target is not model:
                continue
            rel_name = related_accessor_name(fk_field, model_cls)
            if rel_name is None or rel_name != name:
                continue
            kind = "reverse_o2o" if isinstance(fk_field, OneToOneField) else "reverse_fk"
            return RelationInfo(
                kind=kind,
                source_model=model,
                target_model=model_cls,
                fk_field=fk_field,
                accessor_name=name,
                fk_column=fk_field.db_column or f"{fk_field.name}_id",
            )

    # 3. Reverse M2M: scan the registry for M2M fields targeting `model`
    for model_cls in list(_model_registry.values()):
        for m2m_field in getattr(model_cls, "_m2m_fields", []):
            try:
                target = m2m_field.get_target_model()
            except (KeyError, AttributeError):
                continue
            if target is not model:
                continue
            rel_name = related_accessor_name(m2m_field, model_cls)
            if rel_name is None or rel_name != name:
                continue
            return RelationInfo(
                kind="reverse_m2m",
                source_model=model,
                target_model=model_cls,
                fk_field=m2m_field,
                accessor_name=name,
                fk_column=m2m_field.get_target_column(),
            )

    return None


__all__ = [
    "RelationInfo",
    "related_accessor_name",
    "RelationKind",
    "resolve_relation",
    "_pending_relations",
    "_pending_m2m",
    "_process_pending_relations",
]
