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

        return opts

    def get_field(self, name: str) -> Field[Any] | None:
        """Get a field by name."""
        for f in self.local_fields:
            if f.name == name:
                return f
        return None

    def get_fields(self) -> list[Field[Any]]:
        """Get all fields."""
        return self.local_fields.copy()
