"""Docs↔surface lockstep guard for the ORM support boundaries.

The agent doc (zeeb_agents/agent_docs/zeebpy/backend-generation.md) and the
feature reference (docs/orm/feature-reference.md) name concrete features as
absent or present. These tests fail when the code surface drifts from those
claims — whoever adds (or removes) a feature must update the docs in the
same change.
"""

import inspect
from pathlib import Path

from zeeb_orm import fields as fields_mod
from zeeb_orm.query.queryset import QuerySet

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOC_PATHS = (
    _REPO_ROOT / "zeeb_agents" / "agent_docs" / "zeebpy" / "backend-generation.md",
    _REPO_ROOT / "docs" / "orm" / "feature-reference.md",
)

# Named as unavailable in both docs. If one of these gains an
# implementation, delete it here AND update both documents.
ABSENT_QUERYSET_METHODS = (
    "earliest",
    "latest",
    "dates",
    "datetimes",
    "alias",
    "reverse",
    "contains",
    "extra",
)

# Field types the docs direct agents away from (no storage layer /
# PostgreSQL-only types).
ABSENT_FIELD_TYPES = (
    "FileField",
    "ImageField",
    "FilePathField",
    "ArrayField",
    "HStoreField",
)

# The complete model-signal inventory promised by the docs.
DOCUMENTED_SIGNALS = {"pre_save", "post_save", "pre_delete", "post_delete"}


def _docs_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in _DOC_PATHS)


def test_doc_files_exist():
    for path in _DOC_PATHS:
        assert path.exists(), path


def test_absent_queryset_methods_stay_absent_and_documented():
    docs = _docs_text()
    for name in ABSENT_QUERYSET_METHODS:
        assert not hasattr(QuerySet, name), (
            f"QuerySet.{name}() now exists - remove it from "
            "ABSENT_QUERYSET_METHODS and update the support-boundary docs."
        )
        assert f"{name}(" in docs, (
            f"{name}() is absent from the ORM but no longer documented as "
            "unavailable - restore it in the support-boundary docs."
        )


def test_absent_field_types_stay_absent():
    for name in ABSENT_FIELD_TYPES:
        assert not hasattr(fields_mod, name), (
            f"fields.{name} now exists - update the support-boundary docs "
            "and ABSENT_FIELD_TYPES."
        )


def test_signal_inventory_matches_docs():
    import zeeb_orm.signals as signals_mod
    from zeeb_orm.signals import Signal

    exported = {
        name
        for name, value in vars(signals_mod).items()
        if isinstance(value, Signal)
    }
    assert exported == DOCUMENTED_SIGNALS, (
        "Model-signal inventory changed - update the support-boundary docs "
        "and DOCUMENTED_SIGNALS."
    )


def test_documented_capabilities_exist():
    # Claims the docs make about what IS available must also hold.
    from zeeb_orm import IntegrityError  # noqa: F401  (constraint violations)
    from zeeb_orm.exceptions import ConnectionDoesNotExist  # noqa: F401
    from zeeb_orm.models.base import Model
    from zeeb_orm.query.expressions import Aggregate

    assert "ignore_conflicts" in inspect.signature(QuerySet.bulk_create).parameters
    assert "filter" in inspect.signature(Aggregate.__init__).parameters
    assert "using" in inspect.signature(Model.save).parameters
    assert "using" in inspect.signature(Model.refresh_from_db).parameters


def test_distinct_with_fields_raises_as_documented():
    import pytest

    from zeeb_orm.exceptions import NotSupportedError

    qs = QuerySet.__new__(QuerySet)
    qs._combinator = None
    with pytest.raises(NotSupportedError):
        qs.distinct("email")
