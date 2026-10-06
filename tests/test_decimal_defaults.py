"""A DecimalField default is a Decimal, never the string it was written as."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import Column, Numeric

from zeeb_agents._utils.errors import AgentError
from zeeb_agents._utils.field_types import (
    field_extra_imports,
    render_field_line,
    validate_field_spec,
)
from zeeb_orm.migrations.operations import _default_clause, _scalar_default


@pytest.mark.parametrize(
    ("default", "rendered"),
    [
        ("0.00", 'default=Decimal("0.00")'),
        (" 12.5 ", 'default=Decimal("12.5")'),
        (5, 'default=Decimal("5")'),
        (0.1, 'default=Decimal("0.1")'),
    ],
)
def test_a_decimal_default_renders_as_a_decimal(default, rendered):
    field = {
        "name": "rate",
        "type": "decimal",
        "max_digits": 10,
        "decimal_places": 2,
        "default": default,
    }
    line = render_field_line(field)
    assert rendered in line
    assert field_extra_imports(field) == ["from decimal import Decimal"]
    compile(
        f"from decimal import Decimal\nfrom zeeb_orm import fields\n{line}\n", "models.py", "exec"
    )


@pytest.mark.parametrize("default", ["abc", "NaN", "Infinity", ""])
def test_a_non_decimal_default_is_refused_up_front(default):
    with pytest.raises(AgentError) as exc:
        validate_field_spec({"name": "rate", "type": "DecimalField", "default": default})
    assert exc.value.result.data["error_code"] == "invalid_field_spec"


def test_other_fields_and_raw_defaults_are_left_alone():
    assert 'default="0.00"' in render_field_line(
        {"name": "code", "type": "string", "default": "0.00"}
    )
    assert field_extra_imports({"name": "code", "type": "string", "default": "0.00"}) == []
    raw = {"name": "rate", "type": "decimal", "default": "1", "raw": {"default": "Decimal('2')"}}
    assert "default=Decimal('2')" in render_field_line(raw)
    assert field_extra_imports(raw) == []
    # A null default stays None, and bool never reads as a number.
    assert "default=None" in render_field_line({"name": "rate", "type": "decimal", "default": None})


def test_a_decimal_default_reaches_migrations_as_exact_text():
    column = Column("rate", Numeric(10, 2), default=Decimal("0.10"), nullable=False)
    assert _scalar_default(column) == "0.10"
    clause = _default_clause(column)
    assert clause is not None and clause.arg == "0.10"
