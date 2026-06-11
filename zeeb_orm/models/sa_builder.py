"""SQLAlchemy Table / ORM-model construction for zeeb_orm models."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Column, MetaData, Table

if TYPE_CHECKING:
    from zeeb_orm.models.base import Model

# Shared metadata for all models (re-exported from zeeb_orm.models.base)
metadata = MetaData()


def build_table(model: type[Model]) -> Table:
    """Get or create the SQLAlchemy Table for ``model`` (cached on the class)."""
    if model._sa_table is None:
        from sqlalchemy import ForeignKey, Integer

        from zeeb_orm.models.deletion import to_db_ondelete
        from zeeb_orm.models.fields import ForeignKeyField

        columns = []

        for field in model._meta.local_fields:
            col_type = field.get_column_type()
            col_kwargs: dict[str, Any] = {
                "nullable": field.null,
                "primary_key": field.primary_key,
                "unique": field.unique,
                "index": field.index,
            }

            if field.default is not None and not callable(field.default):
                col_kwargs["default"] = field.default

            # Handle FK columns
            if isinstance(field, ForeignKeyField):
                target_model = field.get_target_model()
                target_table = target_model._meta.db_table
                target_pk = target_model._meta.pk_name
                # Get FK column type from target model's PK
                target_pk_field = target_model._meta.pk
                if target_pk_field:
                    col_type = target_pk_field.get_column_type()
                else:
                    col_type = Integer()  # Fallback
                col_kwargs["nullable"] = field.null
                # Map the on_delete constant to a valid SQL ON DELETE action
                # (e.g. SET_NULL -> "SET NULL"; DO_NOTHING -> no clause).
                db_ondelete = to_db_ondelete(field.on_delete)
                fk = ForeignKey(
                    f"{target_table}.{target_pk}",
                    ondelete=db_ondelete,
                )
                columns.append(Column(field.db_column, col_type, fk, **col_kwargs))
            else:
                columns.append(Column(field.db_column or field.name, col_type, **col_kwargs))

        model._sa_table = Table(model._meta.db_table, metadata, *columns)

    # Ensure auto-created M2M join tables exist in the shared metadata.
    # Done lazily (not at class definition) so importing model modules does
    # not pollute the global metadata; Database.create_all() and the Alembic
    # autodetector both pick the tables up once the model table is built.
    for m2m_field in getattr(model, "_m2m_fields", []):
        if m2m_field.through is None:
            try:
                build_m2m_through_table(m2m_field)
            except (KeyError, AttributeError):
                # Target model not registered yet — built on first use.
                pass

    return model._sa_table


def build_m2m_through_table(m2m_field: Any) -> Table:
    """Get or create the auto-generated join Table for ``m2m_field``.

    The table lives in the shared metadata under
    ``m2m_field.get_through_table_name()`` and has a ``{source}_id`` and a
    ``{target}_id`` column (types copied from each side's primary key), both
    with ``ON DELETE CASCADE`` foreign keys, plus a unique constraint over
    the pair.
    """
    from sqlalchemy import ForeignKey, Integer, UniqueConstraint

    name = m2m_field.get_through_table_name()
    existing = metadata.tables.get(name)
    if existing is not None:
        return existing

    source = m2m_field.model
    target = m2m_field.get_target_model()
    source_col = m2m_field.get_source_column()
    target_col = m2m_field.get_target_column()

    def _pk_type(model: Any) -> Any:
        pk = model._meta.pk
        return pk.get_column_type() if pk is not None else Integer()

    source_pk = source._meta.pk.db_column or source._meta.pk_name
    target_pk = target._meta.pk.db_column or target._meta.pk_name

    return Table(
        name,
        metadata,
        Column(
            source_col,
            _pk_type(source),
            ForeignKey(f"{source._meta.db_table}.{source_pk}", ondelete="CASCADE"),
            nullable=False,
        ),
        Column(
            target_col,
            _pk_type(target),
            ForeignKey(f"{target._meta.db_table}.{target_pk}", ondelete="CASCADE"),
            nullable=False,
        ),
        UniqueConstraint(
            source_col, target_col, name=f"uq_{name}_{source_col}_{target_col}"
        ),
    )


def build_sa_model(model: type[Model]) -> type[Any]:
    """Get or create a SQLAlchemy ORM model class for ``model`` (cached)."""
    if model._sa_model is None:
        from sqlalchemy import ForeignKey as SAForeignKey
        from sqlalchemy.orm import DeclarativeBase, mapped_column

        from zeeb_orm.models.deletion import to_db_ondelete
        from zeeb_orm.models.fields import ForeignKeyField

        # Create a unique base for this model
        class Base(DeclarativeBase):
            pass

        # Build attributes for the SA model
        attrs: dict[str, Any] = {
            "__tablename__": model._meta.db_table,
            "__table_args__": {"extend_existing": True},
        }

        for field in model._meta.local_fields:
            if isinstance(field, ForeignKeyField):
                # FK column
                target_model = field.get_target_model()
                target_table = target_model._meta.db_table
                target_pk = target_model._meta.pk_name
                attrs[field.db_column] = mapped_column(
                    SAForeignKey(
                        f"{target_table}.{target_pk}",
                        ondelete=to_db_ondelete(field.on_delete),
                    ),
                    nullable=field.null,
                )
            else:
                # Regular column
                col_type = field.get_column_type()
                kwargs: dict[str, Any] = {
                    "nullable": field.null,
                    "primary_key": field.primary_key,
                    "unique": field.unique,
                    "index": field.index,
                }
                if field.default is not None and not callable(field.default):
                    kwargs["default"] = field.default
                attrs[field.db_column or field.name] = mapped_column(col_type, **kwargs)

        # Create the SA model class
        sa_model = type(f"_SA{model.__name__}", (Base,), attrs)
        model._sa_model = sa_model

    return model._sa_model


def to_sa_instance(instance: Model) -> Any:
    """Convert a model instance to a SQLAlchemy ORM instance.

    .. deprecated::
        Use :meth:`Model._to_insert_values` for new INSERT paths.  This
        function is kept for backwards compatibility but is not used
        internally.
    """
    from zeeb_orm.models.fields import DateField, DateTimeField

    sa_model = instance._get_sa_model()
    kwargs = {}

    for field in instance._meta.local_fields:
        value = getattr(instance, field.name, None)

        # Handle callable defaults (e.g., UUIDAutoField)
        if value is None and hasattr(field, 'default') and field.default is not None:
            if callable(field.default):
                value = field.default()
                setattr(instance, field.name, value)  # Store on instance too

        # Handle auto timestamps
        if isinstance(field, DateTimeField):
            if field.auto_now_add and value is None and not instance._state.persisted:
                value = datetime.datetime.now(datetime.timezone.utc)
            elif field.auto_now:
                value = datetime.datetime.now(datetime.timezone.utc)
        elif isinstance(field, DateField):
            if field.auto_now_add and value is None and not instance._state.persisted:
                value = datetime.date.today()
            elif field.auto_now:
                value = datetime.date.today()

        if value is not None:
            kwargs[field.db_column or field.name] = value

    return sa_model(**kwargs)


__all__ = [
    "metadata",
    "build_table",
    "build_m2m_through_table",
    "build_sa_model",
    "to_sa_instance",
]
