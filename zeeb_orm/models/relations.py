"""Relation bookkeeping: pending reverse relations and relation resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from zeeb_orm.models.base import Model

# Pending relations to set up (for forward references)
_pending_relations: list[tuple[type, Any]] = []


def _process_pending_relations(resolve_callables: bool = False) -> None:
    """Process pending reverse relations.

    Args:
        resolve_callables: If True, also resolve callable FK targets
            (e.g. ``get_user_model``).  Set to True after all models
            and settings have been loaded (inside ``_register_models``).
    """
    from zeeb_orm.models.manager import RelatedManagerDescriptor

    still_pending = []

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

            # Determine the reverse accessor name
            related_name = fk_field.related_name
            if related_name is None:
                # Default: modelname_set
                related_name = f"{source_model.__name__.lower()}_set"

            # Skip if already set
            if hasattr(target_model, related_name):
                continue

            # Add the RelatedManager descriptor to the target model
            descriptor = RelatedManagerDescriptor(
                related_model=source_model,
                field_name=related_name,
                fk_field_name=f"{fk_field.name}_id",
            )
            setattr(target_model, related_name, descriptor)

        except (KeyError, AttributeError):
            # Target model not yet registered
            still_pending.append((source_model, fk_field))

    _pending_relations.clear()
    _pending_relations.extend(still_pending)


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

    Handles forward FK/O2O fields and reverse FK/O2O accessors
    (``related_name`` or the default ``<modelname>_set``).
    Many-to-many relations are not yet supported (returns None).

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

    # 2. Reverse FK / O2O: scan the registry for FKs targeting `model`
    from zeeb_orm.models.base import _model_registry

    for model_cls in _model_registry.values():
        for fk_field in getattr(model_cls, "_fk_fields", []):
            try:
                target = fk_field.get_target_model()
            except (KeyError, AttributeError):
                continue
            if target is not model:
                continue
            rel_name = fk_field.related_name or f"{model_cls.__name__.lower()}_set"
            if rel_name != name:
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

    # 3. M2M relations: not yet implemented
    return None


__all__ = [
    "RelationInfo",
    "RelationKind",
    "resolve_relation",
    "_pending_relations",
    "_process_pending_relations",
]
