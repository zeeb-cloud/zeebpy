"""LIKE-family lookups match their value literally on every dialect.

``%`` and ``_`` in a lookup value used to reach the LIKE pattern unescaped:
``email__iexact="%"`` matched every row, ``startswith="a_"`` matched "abc".
"""

import pytest
from sqlalchemy.dialects import mysql, postgresql, sqlite

from zeeb_orm import F, Model, close_all_connections, configure, fields, setup_database
from zeeb_orm.query.queryset import lookup_to_condition


class LkWord(Model):
    name = fields.CharField(max_length=50, null=True)
    other = fields.CharField(max_length=50, null=True)

    class Meta:
        table_name = "lk_words"


@pytest.fixture
async def db():
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    Settings.reset()
    LkWord._sa_table = None
    LkWord._sa_model = None
    metadata.clear()
    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    database = await setup_database("sqlite+aiosqlite:///:memory:")
    LkWord._get_table()
    await database.create_all()
    for name in ("100%", "a_b", "abc", "x/y", "%", "ABC"):
        await LkWord.objects.create(name=name, other=name.lower())
    await LkWord.objects.create(name=None)
    yield database
    await database.drop_all()
    await close_all_connections()
    table = metadata.tables.get("lk_words")
    if table is not None:
        metadata.remove(table)
    LkWord._sa_table = None
    LkWord._sa_model = None
    Settings.reset()


async def _names(**lookup):
    return sorted(w.name for w in await LkWord.objects.filter(**lookup))


@pytest.mark.parametrize(
    "lookup, value, expected",
    [
        ("iexact", "%", ["%"]),
        ("iexact", "A_B", ["a_b"]),
        ("iexact", "abc", ["ABC", "abc"]),
        ("contains", "%", ["%", "100%"]),
        ("icontains", "_", ["a_b"]),
        ("startswith", "a_", ["a_b"]),
        ("istartswith", "A_", ["a_b"]),
        ("endswith", "0%", ["100%"]),
        ("iendswith", "/Y", ["x/y"]),
        ("contains", "/", ["x/y"]),
        ("icontains", "b", ["ABC", "a_b", "abc"]),
    ],
)
async def test_wildcards_in_values_are_literal(db, lookup, value, expected):
    assert await _names(**{f"name__{lookup}": value}) == expected


async def test_iexact_none_is_null(db):
    assert [w.name for w in await LkWord.objects.filter(name__iexact=None)] == [None]


async def test_non_string_values_are_stringified(db):
    assert await _names(name__contains=100) == ["100%"]


async def test_f_expression_values_still_work(db):
    assert await _names(name__iexact=F("other")) == ["%", "100%", "ABC", "a_b", "abc", "x/y"]
    # SQLite's LIKE is case-insensitive for ASCII, so "ABC" starts with "abc".
    assert await _names(name__startswith=F("other")) == ["%", "100%", "ABC", "a_b", "abc", "x/y"]


@pytest.mark.parametrize(
    "dialect", [sqlite.dialect(), postgresql.dialect(), mysql.dialect()], ids=str
)
@pytest.mark.parametrize(
    "lookup",
    ["iexact", "contains", "icontains", "startswith", "istartswith", "endswith", "iendswith"],
)
def test_every_dialect_gets_an_escape_clause(dialect, lookup):
    LkWord._get_table()
    compiled = lookup_to_condition(LkWord, f"name__{lookup}", "5%_/").compile(dialect=dialect)
    assert "ESCAPE '/'" in str(compiled)
    assert list(compiled.params.values()) == ["5/%/_//"]
