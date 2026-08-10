"""Model Meta options handling."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zeeb_orm.models.base import Model
    from zeeb_orm.models.fields import Field


@dataclass
class Index:
    """Database index definition."""

    fields: list[str]
    name: str | None = None
    unique: bool = False
    condition: str | None = None  # For partial indexes


@dataclass
class UniqueConstraint:
    """Unique constraint definition."""

    fields: list[str]
    name: str | None = None
    condition: str | None = None


@dataclass
class CheckConstraint:
    """Check constraint definition."""

    check: str
    name: str | None = None


#: Meta options a model picks up from its parents' ``class Meta``.
#:
#: ``abstract`` is excluded because inheriting it would make every subclass of
#: an abstract base abstract as well — ``AbstractBaseUser -> AbstractUser ->
#: User`` would leave no concrete user model. ``db_table``/``table_name`` are
#: excluded because each model owns its own table; a child derives its name
#: from its own class name.
INHERITED_META_OPTIONS = (
    "ordering",
    "managed",
    "unique_together",
    "index_together",
    "indexes",
    "constraints",
    "app_label",
)


def _inheritable_copy(item: Any) -> Any:
    """Copy an Index/constraint for a child model, dropping an explicit name.

    Two siblings inheriting the same abstract base would otherwise register
    the identical index name twice in the shared metadata. With ``name=None``
    :func:`zeeb_orm.models.sa_builder._build_table_args` derives a per-table
    name instead.
    """
    from dataclasses import replace

    kwargs: dict[str, Any] = {"name": None}
    if hasattr(item, "fields"):
        kwargs["fields"] = list(item.fields)
    return replace(item, **kwargs)


@dataclass
class Options:
    """Processed Meta options for a model."""

    model: type[Model] | None = None
    model_name: str = ""
    app_label: str = ""

    # Table options
    db_table: str = ""
    table_name: str = ""  # Alias for db_table
    abstract: bool = False
    managed: bool = True

    # Query options
    ordering: list[str] = field(default_factory=list)
    default_permissions: tuple[str, ...] = ("add", "change", "delete", "view")

    # Constraints and indexes
    indexes: list[Index] = field(default_factory=list)
    constraints: list[UniqueConstraint | CheckConstraint] = field(default_factory=list)
    unique_together: list[tuple[str, ...]] = field(default_factory=list)
    index_together: list[tuple[str, ...]] = field(default_factory=list)

    # Field tracking
    local_fields: list[Field[Any]] = field(default_factory=list)
    pk: Field[Any] | None = None
    pk_name: str = "id"

    # Relationship tracking
    related_objects: dict[str, Any] = field(default_factory=dict)

    #: Names of the options this model actually set — either in its own
    #: ``class Meta`` or by inheriting them. Distinguishes "explicitly chosen"
    #: from "still at the dataclass default", which :meth:`inherit_from`
    #: needs and the plain attribute values cannot express.
    declared_options: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.table_name and not self.db_table:
            self.db_table = self.table_name
        elif self.db_table and not self.table_name:
            self.table_name = self.db_table

    @classmethod
    def from_meta(cls, meta: type | None, model_name: str) -> Options:
        """Create Options from a Meta class."""
        opts = cls()
        opts.model_name = model_name

        # Default table name from model name (convert CamelCase to snake_case)
        default_table = "".join(
            f"_{c.lower()}" if c.isupper() else c for c in model_name
        ).lstrip("_")
        opts.db_table = default_table
        opts.table_name = default_table

        if meta is None:
            return opts

        # Copy attributes from Meta class
        for attr in dir(meta):
            if attr.startswith("_"):
                continue

            value = getattr(meta, attr)

            if attr == "table_name":
                opts.db_table = value
                opts.table_name = value
            elif attr == "db_table":
                opts.db_table = value
                opts.table_name = value
            elif attr == "ordering":
                opts.ordering = list(value) if value else []
            elif attr == "abstract":
                opts.abstract = bool(value)
            elif attr == "managed":
                opts.managed = bool(value)
            elif attr == "indexes":
                opts.indexes = [
                    idx if isinstance(idx, Index) else Index(**idx) for idx in value
                ]
            elif attr == "constraints":
                processed = []
                for c in value:
                    if isinstance(c, (UniqueConstraint, CheckConstraint)):
                        processed.append(c)
                    elif "check" in c:
                        processed.append(CheckConstraint(**c))
                    else:
                        processed.append(UniqueConstraint(**c))
                opts.constraints = processed
            elif attr == "unique_together":
                opts.unique_together = [tuple(ut) for ut in value]
            elif attr == "index_together":
                opts.index_together = [tuple(it) for it in value]
            elif attr == "app_label":
                opts.app_label = value
            else:
                continue

            opts.declared_options.add(attr)

        return opts

    def inherit_from(self, parent: Options) -> None:
        """Adopt inheritable Meta options from a parent model's options.

        Options this model declared in its own ``class Meta`` win, and so does
        an earlier base in the MRO — an option is taken exactly once. Because
        ``parent`` has already inherited from *its* parents, walking the
        direct bases is enough.

        ``abstract`` and ``db_table``/``table_name`` are deliberately excluded
        (see :data:`INHERITED_META_OPTIONS`).
        """
        for attr in INHERITED_META_OPTIONS:
            if attr in self.declared_options:
                continue
            if attr not in parent.declared_options:
                continue

            value = getattr(parent, attr)
            if attr in ("indexes", "constraints"):
                value = [_inheritable_copy(item) for item in value]
            elif isinstance(value, list):
                value = list(value)
            setattr(self, attr, value)
            self.declared_options.add(attr)

    def get_field(self, name: str) -> Field[Any] | None:
        """Get a field by name."""
        for f in self.local_fields:
            if f.name == name:
                return f
        return None

    def get_field_by_column(self, column: str) -> Field[Any] | None:
        """Get a field by its database column name.

        The inverse of :func:`zeeb_orm.models.sa_builder._column_name`. Only
        differs from :meth:`get_field` for fields that rename their column —
        in practice ForeignKeys, whose ``name`` is ``guild`` while their
        ``db_column`` is ``guild_id``.
        """
        for f in self.local_fields:
            if (f.db_column or f.name) == column:
                return f
        return None

    def get_fields(self) -> list[Field[Any]]:
        """Get all fields."""
        return self.local_fields.copy()
